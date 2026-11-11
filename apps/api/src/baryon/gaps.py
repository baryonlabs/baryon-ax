"""Knowledge-gap detection and ontology-pack proposals.

The premise: every answer the platform cannot ground is a data requirement in
disguise. When retrieval returns nothing or the model hedges/refuses, we record
the question, the reasons, and the salient terms. Recurring terms cluster into a
proposed "ontology pack" — an admin-reviewable scaffold of entities, relations,
example questions, and the source systems that would close the gap.

This module is storage- and UI-agnostic:

* `GapStore`     appends to JSONL (Basic Core) and supports dismiss/convert.
* `cluster_gaps` groups open gaps by weighted term overlap.
* `PackStore`    writes draft ontology packs as JSON for admin review.

No embeddings are required; clustering uses term frequency and document
frequency so it stays deterministic and testable.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

REFUSAL_PATTERNS = (
    "찾을 수 없",
    "찾지 못",
    "확인할 수 없",
    "확인되지 않",
    "제공된 문서에서",
    "근거가 없",
    "정보가 없",
    "답변할 수 없",
    "알 수 없",
    "명시되어 있지 않",
    "포함되어 있지 않",
    "나와 있지 않",
    "couldn't find",
    "could not find",
    "cannot find",
    "not enough information",
    "insufficient information",
    "no information",
    "not provided",
    "unable to answer",
    "i don't know",
    "not mentioned",
    "does not contain",
    "cannot confirm",
)

HEDGE_PATTERNS = (
    "아마",
    "것 같",
    "듯합",
    "추정",
    "대략",
    "maybe",
    "possibly",
    "probably",
    "i think",
    "it seems",
)

_TERM_RE = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9_\-]{1,}")
_QUESTION_NOISE = {
    "무엇", "어떤", "어떻게", "어디", "언제", "누가", "왜", "인가요", "인가", "나요", "까요",
    "있나요", "있습니까", "알려", "알려주세요", "대해", "대한", "관련", "그리고", "또는",
    "얼마", "몇", "무슨", "어느", "누구", "어디", "언제", "어떤", "어떻게",
    "때문", "합니다", "입니다", "주세요", "정도", "경우", "내용", "질문", "답변",
    "what", "when", "where", "which", "who", "why", "how", "the", "and", "for", "with",
    "about", "does", "do", "did", "is", "are", "was", "were", "can", "could", "you",
    "tell", "please", "would", "should", "there", "this", "that", "from", "have", "has",
    "any", "our", "we", "i", "me", "my", "of", "to", "in", "on", "at", "by",
}
_PARTICLES = (
    "이라고", "라고", "에서", "에게", "한테", "으로", "까지", "부터", "인가요", "인가",
    "나요", "까요", "이다", "입니다", "은", "는", "이", "가", "을", "를", "의", "에",
    "와", "과", "도", "만", "로",
)

# Heuristic mapping from vocabulary to the system that would hold the answer.
SOURCE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("예산", "회계", "정산", "비용", "계약", "견적", "발주", "구매", "원가"), "ERP / 회계 시스템, 계약·견적 문서"),
    (("인사", "인건비", "급여", "노무", "근태", "채용", "평가", "조직", "휴가"), "HR 시스템, 인사 규정 문서"),
    (("법령", "규정", "조례", "지침", "고시", "판례"), "법규·규정 문서 저장소"),
    (("고객", "영업", "매출", "파이프라인", "리드"), "CRM, 영업 실적 데이터"),
    (("설비", "공정", "품질", "생산", "불량", "가동"), "MES / 품질 관리 시스템"),
    (("계정", "권한", "접근", "보안", "감사"), "IAM·감사 로그, 보안 정책 문서"),
    (("budget", "invoice", "contract", "vendor", "cost"), "ERP / finance system, contracts"),
    (("employee", "payroll", "hr", "leave", "hiring"), "HR system, HR policy documents"),
)


def normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def strip_particle(token: str) -> str:
    for particle in _PARTICLES:
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)]
    return token


def salient_terms(text: str) -> list[str]:
    terms: list[str] = []
    for match in _TERM_RE.findall(text or ""):
        token = strip_particle(match.lower())
        if len(token) < 2 or token in _QUESTION_NOISE:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def detect_reasons(question: str, answer: str, citations: int, used_retrieval: bool) -> list[str]:
    """Return the reasons this exchange should be reviewed as a knowledge gap."""
    answer = answer or ""
    lowered = answer.lower()
    reasons: list[str] = []

    if not answer.strip():
        reasons.append("empty_answer")
    if used_retrieval and citations == 0:
        reasons.append("no_citations")
    if any(pattern in lowered for pattern in REFUSAL_PATTERNS):
        reasons.append("refusal")
    elif any(pattern in lowered for pattern in HEDGE_PATTERNS):
        reasons.append("vague")
    return reasons


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


@dataclass
class KnowledgeGap:
    id: str
    organization_id: str
    user_id: str
    question: str
    answer_excerpt: str
    reasons: list[str]
    citations: int
    terms: list[str]
    thread_id: str | None = None
    trace_id: str = ""
    model: str = ""
    status: str = "open"  # open | dismissed | converted
    occurrences: int = 1
    first_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    pack_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def key(self) -> str:
        raw = f"{self.organization_id}|{normalize_question(self.question)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


class GapStore:
    """Append-only JSONL store with in-memory index (per process)."""

    def __init__(self, path: str | Path | None = None) -> None:
        default = os.getenv("GAP_STORE", "/tmp/baryon-ax-gaps/gaps.jsonl")
        self.path = Path(path or default)
        self._lock = threading.Lock()
        self._gaps: dict[str, KnowledgeGap] = {}
        self._by_key: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                gap = KnowledgeGap(**json.loads(line))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            self._gaps[gap.id] = gap
            self._by_key[gap.key] = gap.id

    def _persist(self, gap: KnowledgeGap) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(gap.as_dict(), ensure_ascii=False) + "\n")

    def observe(
        self,
        *,
        organization_id: str,
        user_id: str,
        question: str,
        answer: str,
        citations: int,
        used_retrieval: bool = True,
        thread_id: str | None = None,
        trace_id: str = "",
        model: str = "",
    ) -> KnowledgeGap | None:
        """Record the exchange when it looks ungrounded. Returns the gap or None."""
        reasons = detect_reasons(question, answer, citations, used_retrieval)
        if not reasons:
            return None
        now = datetime.now(timezone.utc).isoformat()
        key = KnowledgeGap(
            id="tmp", organization_id=organization_id, user_id=user_id, question=question,
            answer_excerpt="", reasons=reasons, citations=citations, terms=[],
        ).key
        with self._lock:
            existing_id = self._by_key.get(key)
            if existing_id and existing_id in self._gaps:
                gap = self._gaps[existing_id]
                gap.occurrences += 1
                gap.last_seen = now
                gap.reasons = sorted(set(gap.reasons) | set(reasons))
                gap.answer_excerpt = answer.strip()[:500]
                gap.citations = citations
                gap.trace_id = trace_id or gap.trace_id
                if gap.status == "dismissed":
                    gap.status = "open"
                self._persist(gap)
                return gap

            gap = KnowledgeGap(
                id="gap_" + uuid.uuid4().hex[:16],
                organization_id=organization_id,
                user_id=user_id,
                question=question.strip()[:500],
                answer_excerpt=answer.strip()[:500],
                reasons=sorted(set(reasons)),
                citations=citations,
                terms=salient_terms(question)[:8],
                thread_id=thread_id,
                trace_id=trace_id,
                model=model,
                first_seen=now,
                last_seen=now,
            )
            self._gaps[gap.id] = gap
            self._by_key[key] = gap.id
            self._persist(gap)
            return gap

    def list(self, organization_id: str, status: str | None = "open", limit: int = 100) -> list[KnowledgeGap]:
        gaps = [
            gap for gap in self._gaps.values()
            if gap.organization_id == organization_id and (status is None or gap.status == status)
        ]
        gaps.sort(key=lambda gap: gap.last_seen, reverse=True)
        return gaps[:limit]

    def get(self, organization_id: str, gap_id: str) -> KnowledgeGap | None:
        gap = self._gaps.get(gap_id)
        return gap if gap and gap.organization_id == organization_id else None

    def dismiss(self, organization_id: str, gap_id: str) -> KnowledgeGap | None:
        with self._lock:
            gap = self.get(organization_id, gap_id)
            if not gap:
                return None
            gap.status = "dismissed"
            self._persist(gap)
            return gap

    def mark_converted(self, organization_id: str, gap_ids: Iterable[str], pack_id: str) -> int:
        count = 0
        with self._lock:
            for gap_id in gap_ids:
                gap = self.get(organization_id, gap_id)
                if not gap:
                    continue
                gap.status = "converted"
                gap.pack_id = pack_id
                self._persist(gap)
                count += 1
        return count

    def summary(self, organization_id: str) -> dict[str, Any]:
        gaps = [gap for gap in self._gaps.values() if gap.organization_id == organization_id]
        by_reason: Counter[str] = Counter()
        for gap in gaps:
            by_reason.update(gap.reasons)
        return {
            "total": len(gaps),
            "open": sum(1 for gap in gaps if gap.status == "open"),
            "dismissed": sum(1 for gap in gaps if gap.status == "dismissed"),
            "converted": sum(1 for gap in gaps if gap.status == "converted"),
            "occurrences": sum(gap.occurrences for gap in gaps),
            "reasons": dict(by_reason.most_common()),
        }


# --------------------------------------------------------------------------
# clustering -> ontology proposal
# --------------------------------------------------------------------------


@dataclass
class GapCluster:
    id: str
    label: str
    size: int
    gap_ids: list[str]
    questions: list[str]
    terms: list[str]
    reasons: dict[str, int]
    suggested_pack_name: str
    required_sources: list[dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _term_weights(documents: list[list[str]]) -> tuple[list[dict[str, float]], Counter[str]]:
    document_frequency: Counter[str] = Counter()
    for terms in documents:
        document_frequency.update(set(terms))
    total = max(1, len(documents))
    weights: list[dict[str, float]] = []
    for terms in documents:
        counts = Counter(terms)
        weights.append({
            term: count * (1.0 + math.log((total + 1) / (document_frequency[term] + 0.5)))
            for term, count in counts.items()
        })
    return weights, document_frequency


def suggest_sources(terms: Iterable[str]) -> list[dict[str, str]]:
    """Rank source hints by how many domain keywords the terms hit."""
    joined = " ".join(terms).lower()
    scored: list[tuple[int, str]] = []
    for keywords, hint in SOURCE_HINTS:
        # Longer keyword hits are more specific ("인건비" beats "예산").
        hits = sum(len(keyword) for keyword in keywords if keyword in joined)
        if hits:
            scored.append((hits, hint))
    scored.sort(key=lambda item: -item[0])
    hints: list[dict[str, str]] = []
    for _, hint in scored[:2]:
        if all(item["hint"] != hint for item in hints):
            hints.append({"kind": "system", "hint": hint})
    if not hints:
        hints.append({"kind": "system", "hint": "내부 문서 저장소 + 사내 데이터베이스"})
    return hints


def cluster_gaps(gaps: list[KnowledgeGap], min_size: int = 1) -> list[GapCluster]:
    documents = [salient_terms(gap.question) or [gap.question[:20]] for gap in gaps]
    weights, document_frequency = _term_weights(documents)
    top_terms = [
        {term for term, _ in sorted(weight.items(), key=lambda item: item[1], reverse=True)[:5]}
        for weight in weights
    ]

    assigned = [False] * len(gaps)
    clusters: list[GapCluster] = []
    order = sorted(range(len(gaps)), key=lambda index: (-len(gaps[index].reasons), -gaps[index].occurrences))

    for seed in order:
        if assigned[seed]:
            continue
        members = [seed]
        assigned[seed] = True
        for candidate in range(len(gaps)):
            if assigned[candidate]:
                continue
            shared = top_terms[seed] & top_terms[candidate]
            overlap_weight = sum(weights[seed].get(term, 0.0) for term in shared)
            # Merge on two shared terms, or on one term that is rare enough in
            # this org's gap set to be a real topic rather than a generic word.
            rare_shared = any(document_frequency[term] <= max(2, len(gaps) * 0.3) for term in shared)
            if len(shared) >= 2 or (shared and rare_shared and overlap_weight >= 1.0):
                members.append(candidate)
                assigned[candidate] = True
        if len(members) < min_size:
            continue

        member_gaps = [gaps[index] for index in members]
        term_score: Counter[str] = Counter()
        reason_score: Counter[str] = Counter()
        for index in members:
            for term, weight in weights[index].items():
                term_score[term] += weight
            reason_score.update(gaps[index].reasons)
        ranked_terms = [term for term, _ in term_score.most_common(8)]
        label = ranked_terms[0] if ranked_terms else "지식 공백"
        cluster_key = "|".join(sorted(ranked_terms[:4])) or label
        clusters.append(
            GapCluster(
                id="cl_" + hashlib.sha1(cluster_key.encode("utf-8")).hexdigest()[:12],
                label=label,
                size=len(member_gaps),
                gap_ids=[gap.id for gap in member_gaps],
                questions=[gap.question for gap in member_gaps][:5],
                terms=ranked_terms,
                reasons=dict(reason_score.most_common()),
                suggested_pack_name=f"{label} 온톨로지",
                required_sources=suggest_sources(ranked_terms),
            )
        )

    clusters.sort(key=lambda cluster: (-cluster.size, -sum(cluster.reasons.values())))
    return clusters


def build_pack(
    *,
    organization_id: str,
    name: str,
    gaps: list[KnowledgeGap],
    cluster: GapCluster | None = None,
) -> dict[str, Any]:
    terms: list[str] = []
    for gap in gaps:
        for term in gap.terms or salient_terms(gap.question):
            if term not in terms:
                terms.append(term)
    terms = terms[:12] or ([cluster.label] if cluster else ["지식"])

    entities = [
        {"name": term, "type": "concept", "synonyms": [], "source": "knowledge_gap"}
        for term in terms
    ]
    relations = []
    if len(terms) >= 2:
        for index in range(min(6, len(terms) - 1)):
            relations.append({
                "from": terms[index],
                "type": "related_to",
                "to": terms[index + 1],
                "confidence": "candidate",
            })

    sources = cluster.required_sources if cluster else suggest_sources(terms)
    pack_id = "ont_" + uuid.uuid4().hex[:16]
    return {
        "schema_version": 1,
        "id": pack_id,
        "organization_id": organization_id,
        "name": name.strip() or (cluster.suggested_pack_name if cluster else "지식 온톨로지"),
        "domain": cluster.label if cluster else terms[0],
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_gap_ids": [gap.id for gap in gaps],
        "source_questions": [gap.question for gap in gaps][:20],
        "entities": entities,
        "relations": relations,
        "required_sources": sources,
        "acceptance_questions": [gap.question for gap in gaps][:10],
        "notes": "자동 생성된 초안입니다. 관리자 검토 후 엔티티·관계·소스를 확정하세요.",
    }


class PackStore:
    def __init__(self, root: str | Path | None = None) -> None:
        default = os.getenv("ONTOLOGY_STORE", "/tmp/baryon-ax-ontology")
        self.root = Path(root or default)

    def save(self, pack: dict[str, Any]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{pack['id']}.json").write_text(
            json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return pack

    def list(self, organization_id: str, limit: int = 50) -> list[dict[str, Any]]:
        packs: list[dict[str, Any]] = []
        if not self.root.exists():
            return packs
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                pack = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if pack.get("organization_id") == organization_id:
                packs.append(pack)
            if len(packs) >= limit:
                break
        return packs

    def get(self, organization_id: str, pack_id: str) -> dict[str, Any] | None:
        path = self.root / f"{pack_id}.json"
        if not path.exists():
            return None
        try:
            pack = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return pack if pack.get("organization_id") == organization_id else None
