"""The question–certification loop: the knowledge base asks people first, about what it cannot explain.

Hong, "Designing a Human-AI Certification Loop for Eliciting Tacit Knowledge" (HCI KOREA 2027) argues that the hard
part of eliciting tacit knowledge is knowing *what to ask*, and puts the starting point in the **behaviour–knowledge
gap**: something a person did that the existing knowledge does not explain. One round:

1. **Ontology** — terms (definition, source) and relations are drafted from the organization's documents. A term
   whose quoted sentence is found in its source chunk becomes ``verified`` (the agent's first-source check);
   otherwise it stays ``draft``. Two sources defining the same term differently are kept as ``[쟁점]`` with both
   interpretations.
2. **Actions** — decisions and requests people make ("who, what, in which situation", one sentence), recorded with
   consent by the chat front ends.
3. **Gap judgment** — for each new action the related terms and passages are retrieved, and the model judges
   whether any of them *states the reason* (criterion, condition) for it. Only gaps become questions.
4. **Question** — one question about the reason and three expected answers from different perspectives (principle,
   condition, expected result; 40 characters each). The screen adds "모두 틀림" and free text. Expected answers are
   a scaffold for the person to state their own criterion, not the right answer.
5. **Answer** — what the person picks, corrects or writes becomes a ``criterion`` item (draft), with the action
   and the answer as its primary source.
6. **Certification** — ``draft → verified`` (agent may) ``→ certified`` (people only). Certifying requires every
   source of the item to have been compared (``checked``) and any ``[쟁점]`` resolved; hold and reject need a
   reason and send the item back to draft. Only certified items are offered for decisions.

The state rules follow certify-gate (github.com/baryonlabs/certify-gate, MIT), the reference implementation of the
paper's certification screen. Export is the same ``ontology.json`` shape (``terms / relations / questions``) and an
Apache Ossie model.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("baryon.loop")

Complete = Callable[[list[dict[str, str]]], Awaitable[str]]
Search = Callable[[str, int], list[dict]]

DRAFT, VERIFIED, CERTIFIED = "draft", "verified", "certified"
DECISIONS = ("approve", "hold", "reject")
MIN_REASON = 5
ANSWER_CHARS = 40
PERSPECTIVES = ("원칙", "조건", "기대 결과")


class LoopError(Exception):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _json(text: str) -> dict:
    """The model's JSON, tolerating code fences and text around it."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        value = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def tokens(text: str) -> set[str]:
    """Words of two or more characters, with common Korean endings cut so "인증은" meets "인증"."""
    found = set()
    for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", str(text or "").lower()):
        found.add(re.sub(r"(은|는|이|가|을|를|의|에|에서|으로|로|와|과|도|만|까지|부터|하고|이다|하다|했다|한다)$", "", word) or word)
    return {word for word in found if len(word) >= 2}


# --------------------------------------------------------------------------- state rules (certify-gate)


def is_contested(item: dict) -> bool:
    return len(item.get("interpretations") or []) >= 2 or str(item.get("definition", "")).lstrip().startswith("[쟁점]")


def gate_missing(item: dict, session: dict | None) -> list[str]:
    """What still has to be done before a person may certify: every source compared, any [쟁점] resolved."""
    session = session or {}
    checked = {int(value) for value in session.get("checked") or [] if str(value).lstrip("-").isdigit()}
    missing = [f"source:{index}" for index in range(len(item.get("sources") or [])) if index not in checked]
    if not item.get("sources"):
        missing.append("source:none")  # nothing to compare against: cannot be certified
    if is_contested(item):
        contest = session.get("contest") or {}
        if contest.get("choice") is None and len(str(contest.get("reason") or "").strip()) < MIN_REASON:
            missing.append("contest")
    return missing


def verify(item: dict, actor: str) -> dict:
    """The agent's first-source check: draft → verified. Never certified."""
    if item.get("status") != DRAFT:
        raise LoopError("bad_state", f"확인됨으로는 초안에서만 바꿀 수 있습니다 (현재 {item.get('status')})")
    if not item.get("sources"):
        raise LoopError("no_source", "출처가 없는 항목은 확인할 수 없습니다")
    return {**item, "status": VERIFIED, "verified_by": actor, "verified_at": now()}


def decide(item: dict, actor: str, is_person: bool, decision: str, reason: str = "", session: dict | None = None) -> dict:
    """A person's decision on an item. Agents cannot certify, hold or reject."""
    if decision not in DECISIONS:
        raise LoopError("bad_decision", f"알 수 없는 결정: {decision}", 400)
    if not is_person:
        raise LoopError("forbidden_role", "인증 결정은 사람만 내릴 수 있습니다", 403)
    if item.get("status") == CERTIFIED:
        raise LoopError("already_certified", "이미 인증된 항목입니다")
    reason = (reason or "").strip()
    stamp = {"decision": decision, "by": actor, "at": now(), "reason": reason}
    history = list(item.get("history") or []) + [stamp]
    if decision == "approve":
        missing = gate_missing(item, session)
        if missing:
            raise LoopError("gate_incomplete", "원문 대조·쟁점 검토를 마치지 않았습니다: " + ", ".join(missing))
        certified = {**item, "status": CERTIFIED, "certified_by": actor, "certified_at": now(), "history": history}
        if is_contested(item):
            contest = (session or {}).get("contest") or {}
            certified["resolution"] = {"choice": contest.get("choice"), "reason": str(contest.get("reason") or "")}
        return certified
    if len(reason) < MIN_REASON:
        raise LoopError("reason_required", f"보류·거부는 사유가 필요합니다({MIN_REASON}자 이상)", 400)
    return {**item, "status": DRAFT, "last_decision": stamp, "history": history}


# --------------------------------------------------------------------------- store


class LoopStore:
    """One JSON file per organization under ONTOLOGY_STORE/loop (a volume in the compose files)."""

    def __init__(self, root: str | Path | None = None) -> None:
        base = root or os.getenv("LOOP_STORE") or os.path.join(os.getenv("ONTOLOGY_STORE", "/tmp/baryon-ax-ontology"), "loop")
        self.root = Path(base)
        self.lock = threading.RLock()

    def _path(self, organization_id: str) -> Path:
        return self.root / (re.sub(r"[^A-Za-z0-9_.-]", "_", organization_id) + ".json")

    def load(self, organization_id: str) -> dict:
        with self.lock:
            path = self._path(organization_id)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            for key, empty in (("terms", {}), ("relations", []), ("actions", {}), ("questions", {}), ("rounds", []), ("seen_documents", {})):
                data.setdefault(key, empty)
            return data

    def save(self, organization_id: str, data: dict) -> None:
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(organization_id)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            temporary.replace(path)

    def update(self, organization_id: str, change: Callable[[dict], Any]) -> Any:
        with self.lock:
            data = self.load(organization_id)
            result = change(data)
            self.save(organization_id, data)
            return result


# --------------------------------------------------------------------------- 1. ontology from documents

EXTRACT_PROMPT = """아래 사내 문서 조각에서 업무에 쓰이는 핵심 용어와 관계를 뽑아라.

- 용어(terms): 규정·절차·시스템·역할·지표처럼 이 조직에서 뜻이 정해진 말. 최대 {limit}개.
  label은 문서에 나온 그대로의 짧은 이름, definition은 문서가 말하는 뜻을 한 문장으로.
  quote에는 그 정의의 근거가 되는 문장을 **문서에서 글자 그대로** 복사한다(고쳐 쓰지 않는다).
- 관계(relations): 두 용어 사이의 관계(s, p, o). p는 짧은 한국어 동사구(예: 승인한다, 포함한다, 기한이다).
- 문서에 없는 내용은 만들지 않는다.

JSON만 출력: {{"terms": [{{"label": "...", "definition": "...", "quote": "..."}}], "relations": [{{"s": "...", "p": "...", "o": "...", "quote": "..."}}]}}

[문서: {title} · 조각 {chunk}]
{text}"""


def _term_id(label: str) -> str:
    slug = re.sub(r"\s+", "-", label.strip().lower())
    return "t_" + re.sub(r"[^0-9a-z가-힣_-]", "", slug)[:60]


def merge_term(terms: dict, label: str, definition: str, source: dict, round_number: int, verified: bool) -> str:
    """Add a term, or a second source to it. A different definition from another document makes it [쟁점]."""
    key = _term_id(label)
    existing = terms.get(key)
    if not existing:
        terms[key] = {"id": key, "label": label.strip(), "definition": definition.strip(), "kind": "term",
                      "status": VERIFIED if verified else DRAFT, "sources": [source], "round": round_number,
                      "created_at": now(), **({"verified_by": "agent:source-match", "verified_at": now()} if verified else {})}
        return key
    if any(s.get("document_id") == source.get("document_id") and s.get("chunk") == source.get("chunk") for s in existing["sources"]):
        return key
    existing["sources"].append(source)
    if _norm(definition) != _norm(existing["definition"].removeprefix("[쟁점]").strip()) and existing["status"] != CERTIFIED:
        if not existing.get("interpretations"):
            existing["interpretations"] = [{"definition": existing["definition"], "source": existing["sources"][0]}]
        existing["interpretations"].append({"definition": definition.strip(), "source": source})
        if not existing["definition"].startswith("[쟁점]"):
            existing["definition"] = "[쟁점] " + existing["definition"]
        existing["status"] = DRAFT  # two readings: a person decides
    return key


async def extract_from_chunk(document: dict, chunk_number: int, text: str, complete: Complete, limit: int = 6) -> dict:
    prompt = EXTRACT_PROMPT.format(limit=limit, title=document.get("title", ""), chunk=chunk_number, text=text[:6000])
    data = _json(await complete([{"role": "user", "content": prompt}]))
    terms, relations = [], []
    for item in (data.get("terms") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        label, definition, quote = (str(item.get(key) or "").strip() for key in ("label", "definition", "quote"))
        if 1 < len(label) <= 40 and definition:
            # The agent's check: the quoted sentence must be in the chunk, word for word (spaces aside).
            terms.append({"label": label, "definition": definition[:300], "quote": quote[:400],
                          "verified": bool(quote) and _norm(quote) in _norm(text)})
    for item in data.get("relations") or []:
        if isinstance(item, dict) and all(str(item.get(key) or "").strip() for key in ("s", "p", "o")):
            relations.append({key: str(item[key]).strip()[:60] for key in ("s", "p", "o")} | {"quote": str(item.get("quote") or "")[:300]})
    return {"terms": terms, "relations": relations}


# --------------------------------------------------------------------------- 2–4. actions, gap judgment, questions

JUDGE_PROMPT = """사람의 행동 하나와, 조직의 기존 지식(온톨로지 항목·문서 조각)이 있다.
기존 지식이 **이 행동을 한 이유(따른 기준·규칙, 적용 조건)를 진술하는지**만 판정하라.
관련 용어가 나오기만 하고 이유·기준을 말하지 않으면 진술하지 않은 것이다.

[행동] {action}
[행동 직전 맥락] {context}
[기존 지식]
{knowledge}

JSON만 출력: {{"explained": true 또는 false, "by": "이유를 진술한 항목 이름(없으면 빈칸)", "why": "판정 근거 한 문장"}}"""

QUESTION_PROMPT = """너는 사람의 행동 뒤에 있는 판단 기준(암묵지)을 끌어내는 질문을 만드는 연구 보조다.
아래 행동은 기존 온톨로지 항목으로 이유가 설명되지 않는 '행동-지식 간극'으로 판정되었다.

## 행동 문장(누가, 무엇을, 어떤 상황에서)
{action}

## 조회한 온톨로지 항목(이 항목들은 행동의 이유·기준·조건을 진술하지 않는다)
{terms}

## 그 행동 직전의 대화 맥락
{context}

## 할 일
1. 이 행동을 한 이유나 기준을 묻는 질문을 **1개** 만든다. 행동의 구체적 내용을 넣어 '왜 그렇게 했는가' 또는 '어떤 기준으로'를 묻는다.
2. 그 질문에 대한 그럴듯한 **예상 답 3개**를 만든다.
   - 세 답은 **서로 다른 관점**이어야 한다: (a) 지키려는 원칙, (b) 적용 조건, (c) 기대한 결과. 같은 관점을 바꿔 말한 답은 안 된다.
   - 각 답은 **40자 이내**의 한 문장. 일반론보다 이 행동과 맥락에 맞는 구체적 기준을 쓴다.
   - 예상 답은 정답이 아니라 사람이 자기 기준을 구체화하도록 돕는 발판이다. 확신하는 말투를 피한다.
3. 화면은 예상 답 아래에 '모두 틀림'과 자유 입력 칸을 함께 보여 준다(네가 만들 필요는 없다).

## 출력 형식(JSON만, 다른 글 없이)
{{"question": "...", "expected_answers": [{{"perspective": "원칙", "text": "..."}}, {{"perspective": "조건", "text": "..."}}, {{"perspective": "기대 결과", "text": "..."}}]}}"""


def related_terms(data: dict, text: str, limit: int = 6) -> list[dict]:
    """Ontology terms that share words with the action (② 조회)."""
    words = tokens(text)
    scored = []
    for term in data["terms"].values():
        overlap = len(words & tokens(term["label"] + " " + term["definition"]))
        label_hit = _norm(term["label"]) in _norm(text)
        if overlap or label_hit:
            scored.append((overlap + (3 if label_hit else 0), term))
    return [term for _, term in sorted(scored, key=lambda pair: -pair[0])[:limit]]


async def judge_gap(action: dict, terms: list[dict], passages: list[dict], complete: Complete | None) -> dict:
    """③ 판정: does what we know state the reason for this action? Without a model, only a certified or verified
    criterion that shares most of the action's words counts as an explanation."""
    if complete is None:
        words = tokens(action["text"])
        for term in terms:
            if term.get("kind") == "criterion" and term.get("status") in (VERIFIED, CERTIFIED):
                if len(words & tokens(term["definition"] + " " + term.get("action", ""))) >= max(2, len(words) // 2):
                    return {"explained": True, "by": term["label"], "why": "같은 행동에 대한 기준이 이미 있다"}
        return {"explained": False, "by": "", "why": "모델 없이 판정: 이 행동의 기준을 진술한 항목이 없다"}
    knowledge = "\n".join([f"- 용어 {t['label']}: {t['definition']}" for t in terms]
                          + [f"- 문서 {p.get('title', '')}: {str(p.get('content', ''))[:400]}" for p in passages]) or "(없음)"
    data = _json(await complete([{"role": "user", "content": JUDGE_PROMPT.format(
        action=action["text"], context=action.get("context") or "(없음)", knowledge=knowledge)}]))
    explained = data.get("explained")
    if isinstance(explained, str):
        explained = explained.strip().lower() in {"true", "yes", "예", "참"}
    return {"explained": bool(explained), "by": str(data.get("by") or ""), "why": str(data.get("why") or "")[:200]}


def _fallback_question(action: dict) -> dict:
    return {"question": f"'{action['text'][:60]}' — 어떤 기준으로 그렇게 정하셨나요?",
            "expected_answers": [{"perspective": "원칙", "text": "지켜야 하는 원칙이 있어서"},
                                 {"perspective": "조건", "text": "이번 상황의 조건이 특별해서"},
                                 {"perspective": "기대 결과", "text": "그렇게 하면 결과가 더 좋아서"}],
            "generated_by": "template"}


async def make_question(action: dict, terms: list[dict], complete: Complete | None) -> dict:
    """④ 질문: one question about the reason and three expected answers (principle, condition, expected result)."""
    if complete is None:
        return _fallback_question(action)
    listed = "\n".join(f"- {t['label']}: {t['definition']}" for t in terms) or "(관련 항목 없음)"
    data = _json(await complete([{"role": "user", "content": QUESTION_PROMPT.format(
        action=action["text"], terms=listed, context=action.get("context") or "(없음)")}]))
    question = str(data.get("question") or "").strip()
    answers = []
    for index, item in enumerate((data.get("expected_answers") or [])[:3]):
        text = str(item.get("text") if isinstance(item, dict) else item or "").strip()
        if text:
            perspective = str(item.get("perspective") or PERSPECTIVES[index]) if isinstance(item, dict) else PERSPECTIVES[index]
            answers.append({"perspective": perspective, "text": text[:ANSWER_CHARS + 20]})
    if not question or len(answers) < 3:
        logger.info("question generation did not validate; using the template")
        return _fallback_question(action)
    return {"question": question[:300], "expected_answers": answers, "generated_by": "model"}


# --------------------------------------------------------------------------- 5. answers become criteria


def answer_text(question: dict, choice: int | None, none_correct: bool, text: str) -> str:
    text = (text or "").strip()
    if none_correct or choice is None:
        if not text:
            raise LoopError("answer_required", "'모두 틀림'이면 실제 기준을 적어 주세요", 400)
        return text
    if not 0 <= choice < len(question["expected_answers"]):
        raise LoopError("bad_choice", "예상 답 번호가 맞지 않습니다", 400)
    picked = question["expected_answers"][choice]["text"]
    return f"{picked} — {text}" if text else picked


def criterion_item(question: dict, action: dict, statement: str, who: str) -> dict:
    """The person's answer as a knowledge item. Its primary sources are the recorded action and the answer itself,
    which a certifier compares against (the paper's '원문 대조')."""
    label = re.sub(r"\s+", " ", action["text"])[:50]
    return {
        "id": _id("c"), "label": f"기준: {label}", "definition": statement[:500], "kind": "criterion",
        "status": DRAFT, "action": action["text"], "question_id": question["id"], "answered_by": who,
        "sources": [
            {"type": "action", "action_id": action["id"], "quote": action["text"], "context": action.get("context", ""),
             "recorded_at": action["created_at"], "actor": action["actor"]},
            {"type": "answer", "question_id": question["id"], "quote": statement, "question": question["question"],
             "answered_at": now(), "by": who},
        ],
        "created_at": now(),
    }


# --------------------------------------------------------------------------- export


def ontology_json(data: dict, certified_only: bool = False) -> dict:
    """certify-gate's ontology.json shape: terms / relations / questions (state as extra fields)."""
    terms = [term for term in data["terms"].values() if not certified_only or term["status"] == CERTIFIED]
    known = {term["id"] for term in terms}
    return {
        "updated": now(),
        "terms": terms,
        "relations": [rel for rel in data["relations"] if not certified_only or (rel.get("s_id") in known and rel.get("o_id") in known)],
        "questions": [{"kind": "behaviour-gap", "q": q["question"], "expected_answers": q["expected_answers"],
                       "status": q["status"], "action": data["actions"].get(q["action_id"], {}).get("text", ""),
                       "answer": (q.get("answer") or {}).get("statement", ""), "id": q["id"]}
                      for q in data["questions"].values()],
    }


def ossie_model(data: dict, organization_id: str) -> dict:
    """An Apache Ossie semantic model: one dataset of knowledge items, a field per term with its state."""
    fields = [{"name": "item_id", "expression": {"sql": "item_id"}, "datatype": "String", "description": "Knowledge item identifier"},
              {"name": "status", "expression": {"sql": "status"}, "dimension": {}, "datatype": "String",
               "description": "draft | verified (agent matched a primary source) | certified (a person certified it)"}]
    for term in data["terms"].values():
        fields.append({"name": term["label"][:80], "expression": {"sql": term["id"]}, "dimension": {}, "datatype": "String",
                       "description": f"[{term['status']}] {term['definition']}"[:500]})
    open_questions = [q["question"] for q in data["questions"].values() if q["status"] == "open"]
    return {
        "version": "0.3.0.dev0", "name": f"baryon_loop_{re.sub(r'[^A-Za-z0-9_]', '_', organization_id)}",
        "description": "Baryon AX knowledge from the question–certification loop. Use only certified items for decisions.",
        "ai_context": {"instructions": "Only fields whose description starts with [certified] were certified by a person. "
                                       "[verified] means an agent matched a primary source; [draft] is unchecked."
                                       + (" Questions still open: " + "; ".join(open_questions[:20]) + "." if open_questions else ""),
                       "synonyms": ["ontology", "certified knowledge"]},
        "datasets": [{"name": "knowledge_items", "source": "baryon.loop", "primary_key": ["item_id"], "fields": fields}],
        "relationships": [], "metrics": [],
        "nonportable_extensions": [{"vendor_name": "Baryon AX", "data": {"ontology": ontology_json(data)}}],
    }
