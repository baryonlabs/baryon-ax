"""Ontology merge v2: one store, one model, one merger for documents, Slack threads and gap packs.

Before this module the organization had two ontologies: the loop store (``loop.py``: terms with definitions, sources
and [쟁점], relations kept only when both ends already existed) and the gap packs (``gaps.PackStore``: typed entities
with synonyms, one JSON file per pack). They are one now: every organization has one JSON document
(``LoopStore``, schema 2) holding ``terms`` (with ``type``, ``synonyms`` and ``claims``), ``relations`` and the gap
``packs``. Every writer goes through :func:`merge` and gets a :class:`ChangeSet` back.

How a new statement meets what is already known:

1. **Candidates.** The label is normalized (spaces, punctuation, a leading particle or time word dropped:
   "는 고객 지원" → 고객 지원, "다음 주 워크숍 장소" → 워크숍 장소) and looked up by label and synonyms; spacing variants
   come from ``ontology.resolve_synonyms``. Labels that only nearly match (one word longer, one syllable more) are
   candidates, not matches.
2. **Same-as.** A model may pick the same thing among the candidates and nothing else (:func:`judge_same_as`); when
   it is off or fails, rules decide (:func:`rule_same_as`): one extra generic syllable ("견적" / "견적서",
   "납기" / "납기일") is the same thing, an extra word ("환불" / "환불 승인") is not. CI runs the rules.
3. **Definition relation.** Statements are compared as claims (subject, slot, value), not as strings
   (:func:`relate`): the same value is **same**, a value that adds detail is **refine**, and only a real
   disagreement is **contest** — different amounts or durations, different names, "not decided" against a value, or
   two short values with nothing in common. Different slots never contest.
4. **Source weight.** owner > person = document > another bot. A stronger source overrides a weaker one (the weaker
   claim is kept as superseded); a weaker one is attached as ``needs_verification``; only equals that disagree
   become [쟁점]. Another bot's claim alone is never more than "검증 필요" (``trust: needs_verification``).
5. **Relations keep their ends.** An end that is not a term yet becomes a ``stub`` term (typed when the thread knows
   what it is), so no relation is dropped.
6. **Provenance.** A Slack source is the message itself: channel, ts, thread and a workspace-relative permalink.

:func:`merge` builds the whole :class:`ChangeSet` on a copy and only then writes it to the store's data, inside one
``LoopStore.update`` (one lock, one file replace): all of it or nothing. Merging the same thread again changes
nothing (every claim is keyed by its source).
"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger("baryon.ontology_merge")

Complete = Callable[[list[dict[str, str]]], Awaitable[str]]

SCHEMA_VERSION = 2
DRAFT, VERIFIED, CERTIFIED = "draft", "verified", "certified"
OWNER, HUMAN, DOCUMENT, NEEDS_VERIFICATION = "owner", "human", "document", "needs_verification"
TRUST_RANK = {OWNER: 3, HUMAN: 2, DOCUMENT: 2, NEEDS_VERIFICATION: 1}
SAME, DIFFERENT, REFINE, CONTEST = "same", "different", "refine", "contest"
DEFAULT_TYPE = "개념"
# thread_understanding entity kinds → ontology.EntityType
ENTITY_TYPES = {"person": "사람·조직", "bot": "시스템", "self": "시스템", "term": DEFAULT_TYPE}
DEFINITION_SLOT = "정의"


def now() -> str:
    return datetime.now(UTC).isoformat()


def term_id(label: str) -> str:
    """The loop's id for a label (loop._term_id): kept so ids do not change across the migration."""
    slug = re.sub(r"\s+", "-", label.strip().lower())
    return "t_" + re.sub(r"[^0-9a-z가-힣_-]", "", slug)[:60]


# --------------------------------------------------------------------------- labels

_LEADING = re.compile(r"^(?:(?:은|는|이|가|을|를|의|에|도|및)\s+)+")
_TIME = re.compile(r"^(?:(?:다음|이번|지난|매)\s*(?:주|달|월|분기|해|번|회의)|오늘|내일|어제|모레|매주|매달|매일|요즘|앞으로)\s*")
_TRAILING = re.compile(r"(?<=[가-힣A-Za-z0-9])(?:에서는|에게는|에는|으로는|로는|은|는|을|를|의)$")
_PUNCT = re.compile(r"[\s·・.,\-_/'\"`*()\[\]{}:：~!?]+")
_JUNK = re.compile(r"^\d+(?:[.,]\d+)?\s*(?:건|개|번|명|가지|차|호)?$")


def clean_label(label: str) -> str:
    """The label as people would write it in the ontology: no leading particle or time word, no trailing particle."""
    text = re.sub(r"\s+", " ", str(label or "")).strip(" \"'“”‘’`*_.,:")
    for _ in range(2):
        text = _TIME.sub("", _LEADING.sub("", text)).strip()
    if len(text) > 3:
        text = _TRAILING.sub("", text).strip()
    return text


def label_key(label: str) -> str:
    return _PUNCT.sub("", clean_label(label).lower())


_SLOT_ONLY = {"담당", "담당자", "역할", "정체", "현황", "상태", "내용", "결론", "정리", "요약", "참고", "결정", "이유"}


def usable_label(label: str) -> bool:
    """A name worth a term: not a number ("47", "2건"), not a slot word alone ("담당", "현황"), not a sentence."""
    key = label_key(label)
    return (2 <= len(key) <= 40 and not _JUNK.match(clean_label(label)) and bool(re.search(r"[가-힣A-Za-z]", key))
            and clean_label(label) not in _SLOT_ONLY)


# --------------------------------------------------------------------------- values and claims

_VALUE_END = re.compile(
    r"\s*(?:(?:으로|로)\s*(?:하죠|하자|합시다|갑시다|가자|가죠|정했\S*|정합니다|정할게요|결정\S*|확정\S*|고정\S*|할게요|해요|해)"
    r"|(?:이|가)\s*맞(?:아|아요|습니다|다)|(?:입니다|이에요|이야|예요|이다|임|이고|이며|입니다만))[.!~]*$")
_EDIT_NOTE = re.compile(r"\s*\((?:수정|edit|edited)[^)]*\)\s*$", re.IGNORECASE)
UNKNOWN = re.compile(r"정해진\s*(?:것|게)\s*없|기록에\s*없|모르|알\s*수\s*없|확인\s*(?:하지\s*)?못|미정|아직\s*(?:안|못)\s*정|안\s*정했|정하지\s*않")
_QUANTITY = re.compile(r"(\d+(?:\.\d+)?)\s*(만\s*원|천\s*원|원|일|주|개월|달|년|시간|시|분|층|%|퍼센트|명|건|개|회|차)")
_DAYS = {"일": 1, "주": 7, "개월": 30, "달": 30, "년": 365}
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9._-]{1,30}")
_NAME_STOP = {"qa", "ok", "pm", "am", "id", "url", "api", "slack", "the", "and", "or", "of", "to", "in", "on", "for", "by"}
_STOP = {"있습니다", "합니다", "입니다", "있어요", "해요", "이에요", "예요", "됩니다", "있다", "한다", "이다", "현재", "지금", "바로",
         "그리고", "그럼", "다만", "단", "또는", "것", "수", "등", "때", "님", "제", "저", "이번", "다음", "매주", "꼭", "같이"}
_PARTICLES = ("에서는", "으로는", "에게는", "에서", "에게", "한테", "으로", "까지", "부터", "에는", "이고", "이며", "이면",
              "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "로", "요")


def clean_value(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = _EDIT_NOTE.sub("", text).strip()
    for _ in range(2):
        text = _VALUE_END.sub("", text).strip(" .!,")
    return text


def content_words(text: str) -> set[str]:
    found = set()
    for word in re.findall(r"[가-힣A-Za-z0-9]+", str(text or "").lower()):
        for particle in _PARTICLES:
            if word.endswith(particle) and len(word) - len(particle) >= 2:
                word = word[: -len(particle)]
                break
        if len(word) >= 2 and word not in _STOP:
            found.add(word)
    return found


_WORD_AMOUNTS = (("하루", "1일"), ("이틀", "2일"), ("사흘", "3일"), ("일주일", "1주"), ("한 주", "1주"), ("한 달", "1개월"),
                 ("한달", "1개월"), ("두 달", "2개월"), ("석 달", "3개월"), ("일 년", "1년"), ("반년", "6개월"))


def _amounts(text: str) -> str:
    """Amounts written one way: "하루" → 1일, "15만 원" → 15만원, "30 일" → 30일."""
    text = str(text or "")
    for word, amount in _WORD_AMOUNTS:
        text = text.replace(word, amount)
    text = re.sub(r"(\d)\s+(?=만|천|원|일|주|개월|달|년|시간|시|분|층|%|명|건|개|회)", r"\1", text)
    return re.sub(r"(\d)(만|천)\s+원", r"\1\2원", text)


def quantities(text: str) -> dict[str, set[float]]:
    out: dict[str, set[float]] = {}
    text = _amounts(text)
    for number, unit in _QUANTITY.findall(text):
        unit = unit.replace(" ", "")
        amount = float(number)
        if unit in _DAYS:
            out.setdefault("기간", set()).add(amount * _DAYS[unit])
        elif unit in ("원", "만원", "천원"):
            out.setdefault("금액", set()).add(amount * {"원": 1, "만원": 10000, "천원": 1000}[unit])
        elif unit in ("시간", "분"):
            out.setdefault("시간", set()).add(amount * (60 if unit == "시간" else 1))
        elif unit in ("%", "퍼센트"):
            out.setdefault("비율", set()).add(amount)
        else:
            out.setdefault(unit, set()).add(amount)
    return out


def names(text: str) -> set[str]:
    return {name.lower().strip("._-") for name in _NAME.findall(str(text or ""))} - _NAME_STOP


def _short(text: str) -> bool:
    return len(content_words(text)) <= 8


def relate(old: str, new: str) -> str:
    """How a new value for the same subject and slot relates to an existing one: same | refine | contest.

    Values are compared by what they claim: the amounts and durations in them, the names in them, whether one says
    "not decided"; then by content words. Two values that only word the same thing differently are not a contest."""
    a, b = _amounts(clean_value(old)), _amounts(clean_value(new))
    if not a or not b:
        return REFINE if (a or b) else SAME
    na, nb = re.sub(r"\s+", "", a).lower(), re.sub(r"\s+", "", b).lower()
    if na == nb:
        return SAME
    unknown_a, unknown_b = bool(UNKNOWN.search(a)), bool(UNKNOWN.search(b))
    if unknown_a != unknown_b:
        return CONTEST
    if unknown_a and unknown_b:
        return SAME
    qa, qb = quantities(a), quantities(b)
    wa, wb = content_words(a), content_words(b)
    if qa and qa == qb and {w for w in wa if not re.search(r"\d", w)} == {w for w in wb if not re.search(r"\d", w)}:
        return SAME  # "2주" / "14일": the same amount in other units
    for dimension in qa.keys() & qb.keys():
        if qa[dimension] - qb[dimension] and qb[dimension] - qa[dimension]:
            return CONTEST  # each says a number the other does not: 15일 / 20일, 1주 / 2주 (adding one is a refinement)
    ma, mb = names(a), names(b)
    if ma and mb and not ma & mb and (_short(a) or _short(b)):
        return CONTEST
    if nb in na or (wb and wb <= wa):
        return SAME
    if wa & wb or na in nb:
        return REFINE
    if _short(a) and _short(b):
        return CONTEST  # two short values about the same thing with nothing in common
    return REFINE


# --------------------------------------------------------------------------- same-as

_GENERIC_SUFFIX = ("서", "일")


def rule_same_as(new: str, existing: str) -> bool:
    """Without a model: the same key, or one generic syllable more ("견적" / "견적서", "납기" / "납기일")."""
    a, b = label_key(new), label_key(existing)
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= 2 and long.startswith(short) and long[len(short):] in _GENERIC_SUFFIX


def _keys_of(term: dict) -> set[str]:
    return {label_key(name) for name in [term.get("label", ""), *(term.get("synonyms") or [])] if name}


def exact_match(terms: dict, label: str) -> str | None:
    key = label_key(label)
    direct = terms.get(term_id(clean_label(label)))
    if direct and key in _keys_of(direct):
        return direct["id"]
    for term in terms.values():
        if key in _keys_of(term):
            return term["id"]
    return None


def candidates(terms: dict, label: str, limit: int = 5) -> list[str]:
    """Terms that might be the same thing (not certain): one key inside the other, or most words shared."""
    key, words = label_key(label), set(clean_label(label).lower().split())
    scored = []
    for term in terms.values():
        best = 0.0
        for other in [term.get("label", ""), *(term.get("synonyms") or [])]:
            other_key = label_key(other)
            if not other_key or other_key == key:
                continue
            if key in other_key or other_key in key:
                best = max(best, min(len(key), len(other_key)) / max(len(key), len(other_key)))
            other_words = set(clean_label(other).lower().split())
            if words and other_words and len(words | other_words) > 1:
                best = max(best, len(words & other_words) / len(words | other_words))
        if best >= 0.5:
            scored.append((best, term["id"]))
    return [term for _, term in sorted(scored, key=lambda pair: -pair[0])[:limit]]


SAME_AS_SYSTEM = """You keep one name per business concept in an organization's ontology.
For each NEW name you get CANDIDATES (existing names). Pick the candidate that is the same real-world thing (another
word for it, an abbreviation, a translation, a spacing or suffix variant), or none. Related is not the same:
환불 and 환불 승인 are different (the second is an act about the first); 견적 and 견적서 are the same; 연차 and 연차 휴가
are the same. Return JSON only: {"same": [{"new": str, "existing": str}]}, names exactly as listed; leave out a NEW
name with no match."""


async def judge_same_as(pairs: dict[str, list[str]], complete: Complete | None) -> dict[str, str | None]:
    """{new label: [candidate labels]} → {new label: the candidate it is, or None}. The model can only pick from
    the listed candidates; without a model, or when it fails, :func:`rule_same_as` decides."""
    decided = {new: next((c for c in options if rule_same_as(new, c)), None) for new, options in pairs.items()}
    if complete is None or not pairs:
        return decided
    try:
        listing = {new: options for new, options in list(pairs.items())[:40]}
        text = await complete([{"role": "system", "content": SAME_AS_SYSTEM},
                               {"role": "user", "content": json.dumps(listing, ensure_ascii=False)}])
        match = re.search(r"\{.*\}", text or "", re.DOTALL)
        picks = json.loads(match.group(0)).get("same", []) if match else None
        if not isinstance(picks, list):
            raise ValueError("no list")
        model = {new: None for new in listing}
        for pick in picks:
            new, old = str(pick.get("new", "")), str(pick.get("existing", ""))
            if new in listing and old in listing[new]:
                model[new] = old
        decided.update(model)
    except Exception as error:  # noqa: BLE001 - a model failure falls back to the rules
        logger.info("same-as judgment fell back to rules: %s", type(error).__name__)
    return decided


# --------------------------------------------------------------------------- input items


@dataclass
class Claim:
    """One statement to merge: about ``subject``, slot ``predicate``, saying ``value``."""
    subject: str
    value: str
    predicate: str = ""
    trust: str = HUMAN
    source: dict = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    type: str = ""
    group: str = ""            # claims of one contest (thread_understanding contests) share a group
    group_label: str = ""      # the subject the reader named the contest by
    confirmed_by: str = ""     # a person who confirmed this value in the thread (owner | human)
    status: str = ""           # "superseded": a value the thread itself settled against
    attach_to: str = ""        # superseded: the subject of the value that stands (its term keeps this claim)
    verified: bool = False     # the source quote was found in the source text (documents)


@dataclass
class Relation:
    s: str
    p: str
    o: str
    source: dict = field(default_factory=dict)
    s_type: str = ""
    o_type: str = ""
    status: str = DRAFT
    missing: bool = False      # a gap-pack slot: the value nobody has answered yet ("?금액")


@dataclass
class Entity:
    name: str
    type: str = DEFAULT_TYPE
    synonyms: list[str] = field(default_factory=list)
    source: dict = field(default_factory=dict)


def slot(predicate: str) -> str:
    predicate = str(predicate or "").strip()
    return "역할" if predicate in ("", "역할", "담당", "정체") else predicate


def source_key(source: dict) -> str:
    kind = source.get("type", "")
    if kind == "slack":
        return f"slack:{source.get('channel', '')}:{source.get('ts', '')}"
    if kind == "document":
        return f"document:{source.get('document_id', '')}:{source.get('chunk', '')}"
    if kind == "knowledge_gap":
        return f"pack:{source.get('pack_id', '')}"
    return f"{kind}:{json.dumps(source, ensure_ascii=False, sort_keys=True)[:200]}"


def slack_source(channel: str, thread_ts: str, ts: str, quote: str, by: str = "", author_kind: str = "", trust: str = "") -> dict:
    """Provenance at message level: the channel and ts, and a permalink path any workspace URL can be put in front of."""
    return {"type": "slack", "channel": channel, "thread_ts": thread_ts, "ts": ts,
            "permalink": f"/archives/{channel}/p{ts.replace('.', '')}" + (f"?thread_ts={thread_ts}" if thread_ts and thread_ts != ts else ""),
            "quote": quote, "by": by, "author_kind": author_kind, "trust": trust}


_DECISION_OBJECT = re.compile(r"^(?P<s>[^,.?]{1,24}?)(?:을|를)\s+(?P<v>[^,.?]{1,40}?)(?:으로|로)\s+\S+")
_RESTRICTION = re.compile(r"만\s*(?:유효|가능|진행|허용|인정)|에서만|후에만|경우에만|때만")
_RULE_VALUE = re.compile(r"^(?P<head>[^()]{1,12}?)\s*\((?P<cond>[^()]{4,80})\)\s*$")


def decision_claim(statement: str) -> tuple[str, str] | None:
    """(subject, value) of a decision said in words: "견적 유효기간은 30일로 하죠" → (견적 유효기간, 30일),
    "배포를 매주 목요일 오후 4시로 고정하면 어떨까요?" → (배포, 매주 목요일 오후 4시)."""
    from .thread_understanding import (
        _topic_split,  # the reader's own topic rule, so both read a sentence alike
    )

    text = _EDIT_NOTE.sub("", re.sub(r"\s+", " ", statement or "")).strip()
    text = re.sub(r"^(?:맞아요?|맞습니다|네|예|좋아요?|그럼)[,.!\s]+", "", text)
    text = _TIME.sub("", text).strip()
    topic = _topic_split(text)
    if topic:
        subject, _role, value = topic
        subject = clean_label(subject)
        if usable_label(subject):
            return subject, clean_value(value)
    match = _DECISION_OBJECT.match(text)
    if match and usable_label(match.group("s")):
        return clean_label(match.group("s")), clean_value(match.group("v"))
    return None


def items_from_understanding(result: dict, organization_owners: Iterable[str] = ()) -> tuple[list[Claim], list[Relation], dict[str, str]]:
    """The S3 thread reading (thread_understanding.understand) as claims and relations to merge, and entity types.

    * every claim the reader kept (bots' claims stay ``needs_verification``), grouped when the reader found a contest;
    * a decision a person confirmed in words, or a proposal people agreed to, as the claim it makes;
    * a value with a restriction in brackets ("승인 (운영 콘솔에서 받은 것만 유효)") also as a rule about its head;
    * the reader's relations (people's assignments)."""
    channel, thread_ts = str(result.get("channel") or ""), str(result.get("thread_ts") or "")
    types = {label_key(e["name"]): ENTITY_TYPES.get(e.get("type", "term"), DEFAULT_TYPE) for e in result.get("entities") or []
             if e.get("name")}
    for entity in result.get("entities") or []:
        for alias in entity.get("aliases") or []:
            types.setdefault(label_key(alias), ENTITY_TYPES.get(entity.get("type", "term"), DEFAULT_TYPE))
    contest_subjects = {index: str(contest.get("subject") or "") for index, contest in enumerate(result.get("contests") or [])}
    claims: list[Claim] = []
    people_said: list[tuple[float, str, set[str]]] = []  # (ts, trust, words) of what people said
    for decision in result.get("decisions") or []:
        trust = OWNER if decision.get("by_owner") else HUMAN
        people_said.append((float(decision["evidence_ts"][0]), trust, content_words(decision["statement"]) | names(decision["statement"])))
    for claim in result.get("claims") or []:
        if claim.get("author_kind") in ("human", "user_token_app"):
            people_said.append((float(claim["evidence_ts"][0]), claim.get("trust") or HUMAN,
                                content_words(claim["subject"] + " " + claim["value"]) | names(claim["value"])))

    for claim in result.get("claims") or []:
        subject, value = clean_label(claim.get("subject", "")), clean_value(claim.get("value", ""))
        if not usable_label(subject) or not value:
            continue
        ts = str(claim["evidence_ts"][0])
        trust = claim.get("trust") or HUMAN
        if trust not in TRUST_RANK:
            trust = NEEDS_VERIFICATION
        item = Claim(subject=subject, value=value, predicate=claim.get("predicate", ""), trust=trust,
                     source=slack_source(channel, thread_ts, ts, claim.get("quote", ""), claim.get("by", ""), claim.get("author_kind", ""), trust),
                     aliases=[a for a in claim.get("aliases") or [] if usable_label(a)], type=types.get(label_key(subject), ""),
                     group=f"contest:{claim['contest']}" if claim.get("contest") is not None else "",
                     group_label=contest_subjects.get(claim.get("contest"), "") if claim.get("contest") is not None else "")
        if trust == NEEDS_VERIFICATION and not item.group:  # a person repeated this value later: confirmed by them
            wanted = (content_words(value) | names(value)) - content_words(subject)
            for when, by, said in people_said:
                if when > float(ts) and wanted & said and (names(value) & said or len(wanted & said) >= 2):
                    item.confirmed_by = by if TRUST_RANK.get(by, 0) > TRUST_RANK.get(item.confirmed_by, 0) else item.confirmed_by
        claims.append(item)
        rule = _RULE_VALUE.match(value)
        if rule and _RESTRICTION.search(rule.group("cond")) and usable_label(rule.group("head")):
            head = clean_label(rule.group("head"))
            claims.append(Claim(subject=head, value=rule.group("cond").strip(), predicate="조건", trust=trust, source=item.source,
                                type=types.get(label_key(head), "")))

    for decision in result.get("decisions") or []:
        agreed = decision.get("kind") == "implicit" and any(s.get("agree") for s in decision.get("agreement_signals") or [])
        if not (decision.get("confirmed") or agreed) or decision.get("handover_to") or "담당:" in decision.get("statement", ""):
            continue
        found = decision_claim(decision["statement"])
        if not found:
            continue
        subject, value = found
        trust = OWNER if decision.get("by_owner") else HUMAN
        ts = str(decision["evidence_ts"][0])
        claims.append(Claim(subject=subject, value=value, trust=trust, type=types.get(label_key(subject), ""),
                            source=slack_source(channel, thread_ts, ts, decision.get("quote", ""), ", ".join(decision.get("decided_by") or []),
                                                "human", trust) | ({"agreed": True} if agreed and not decision.get("confirmed") else {})))

    # a group of claims the reader did not call a contest was settled in the thread: the confirmed or strongest
    # (then latest) value stands, values that disagree with it are kept as superseded
    by_key: dict[tuple[str, str], list[Claim]] = {}
    for item in claims:
        if not item.group:
            by_key.setdefault((label_key(item.subject), slot(item.predicate)), []).append(item)
    for (key, item_slot), group in list(by_key.items()):  # "정산 기한" is settled with "출장비 정산 기한"
        words = clean_label(group[0].subject).split()
        for (other, other_slot), others in list(by_key.items()):
            other_words = clean_label(others[0].subject).split() if others else []
            if other != key and other_slot == item_slot and len(words) >= 2 and len(other_words) > len(words) \
                    and other_words[-len(words):] == words and others is not group:
                group.extend(others)
                by_key[(other, other_slot)] = []
    for group in by_key.values():
        if len(group) < 2:
            continue
        def weight(c: Claim) -> tuple[int, int, float]:
            return (TRUST_RANK.get(c.confirmed_by, 0), TRUST_RANK.get(c.trust, 0), float(c.source.get("ts") or 0))
        winner = max(group, key=weight)
        for item in group:
            if item is not winner and relate(winner.value, item.value) == CONTEST:
                item.status = "superseded"
                item.attach_to = winner.subject

    relations = []
    for relation in result.get("relations") or []:
        s, p, o = (str(relation.get(k) or "").strip() for k in ("s", "p", "o"))
        if s and p and o:
            ts = str(relation["evidence_ts"][0])
            relations.append(Relation(s=s, p=p, o=o, s_type=types.get(label_key(s), ""), o_type=types.get(label_key(o), ""),
                                      source=slack_source(channel, thread_ts, ts, relation.get("quote", ""))))
    return claims, relations, types


# --------------------------------------------------------------------------- the change set


@dataclass
class ChangeSet:
    added: list[dict] = field(default_factory=list)      # new terms
    merged: list[dict] = field(default_factory=list)     # a new name or source for an existing term, same meaning
    refined: list[dict] = field(default_factory=list)    # more detail, an override by a stronger source, or a weaker claim to verify
    contested: list[dict] = field(default_factory=list)  # [쟁점]: equals that disagree, until a person decides
    relations: list[dict] = field(default_factory=list)  # new relations
    stubs: list[dict] = field(default_factory=list)      # relation ends that were not terms yet
    terms: dict[str, dict] = field(default_factory=dict)  # the final state of every touched term (applied as a whole)
    packs: dict[str, dict] = field(default_factory=dict)

    KEYS = ("added", "merged", "refined", "contested", "relations", "stubs")

    def count(self) -> int:
        return sum(len(getattr(self, key)) for key in self.KEYS)

    def counts(self) -> dict[str, int]:
        return {key: len(getattr(self, key)) for key in self.KEYS}

    def to_dict(self) -> dict:
        return {**{key: getattr(self, key) for key in self.KEYS}, "counts": self.counts(), "changes": self.count()}


def apply(data: dict, changes: ChangeSet) -> None:
    """Write a change set into a store document (in place). Called inside ``LoopStore.update``: one lock, one save."""
    upgrade(data)
    for key, term in changes.terms.items():
        data["terms"][key] = term
    data["relations"].extend(changes.relations)
    data["packs"].update(changes.packs)


# --------------------------------------------------------------------------- the merger


def _is_contested(term: dict) -> bool:
    return len(term.get("interpretations") or []) >= 2 or str(term.get("definition", "")).lstrip().startswith("[쟁점]")


def _best_trust(term: dict) -> str:
    live = [c for c in term.get("claims") or [] if c.get("status") not in ("superseded", NEEDS_VERIFICATION)]
    return max((c.get("trust", HUMAN) for c in live), key=lambda t: TRUST_RANK.get(t, 0), default=term.get("trust") or "")


class Merger:
    """Builds one change set over a working copy of the store's terms and relations."""

    def __init__(self, data: dict, same_as: dict[str, str | None] | None = None, round_number: int = 0) -> None:
        self.base = data
        self.terms: dict[str, dict] = copy.deepcopy(data.get("terms") or {})
        self.relations: list[dict] = data.get("relations") or []
        self.same_as = same_as or {}
        self.round = round_number
        self.changes = ChangeSet()
        self._added_ids: set[str] = set()
        self._contested_ids: set[str] = set()

    # ------------------------------------------------------------ resolution

    def resolve(self, label: str) -> tuple[str | None, bool]:
        """(term id, learned a new name): the term this label names, if any."""
        found = exact_match(self.terms, label)
        if found:
            return found, False
        options = candidates(self.terms, label)
        if not options:
            return None, False
        labels = {self.terms[i]["label"]: i for i in options}
        if clean_label(label) in self.same_as:
            picked = self.same_as[clean_label(label)]
            return (labels.get(picked) if picked else None), bool(picked and picked in labels)
        for option in options:
            if rule_same_as(label, self.terms[option]["label"]) or any(rule_same_as(label, s) for s in self.terms[option].get("synonyms") or []):
                return option, True
        return None, False

    def pending_same_as(self, labels: Iterable[str]) -> dict[str, list[str]]:
        """Labels whose match is uncertain, with their candidate labels (what a model would be asked)."""
        out = {}
        for label in labels:
            if not usable_label(label) or exact_match(self.terms, label):
                continue
            options = candidates(self.terms, label)
            if options:
                out[clean_label(label)] = [self.terms[i]["label"] for i in options]
        return out

    def _touch(self, key: str) -> dict:
        self.changes.terms[key] = self.terms[key]
        return self.terms[key]

    def _new_term(self, label: str, kind: str, type_: str, source: dict, definition: str = "", status: str = DRAFT,
                  trust: str = "") -> dict:
        label = clean_label(label)
        key = term_id(label)
        if key in self.terms:  # a different label with the same slug: keep both readable
            key = f"{key}-{len(self.terms)}"
        term = {"id": key, "label": label, "definition": definition, "kind": kind, "type": type_ or DEFAULT_TYPE,
                "synonyms": [], "status": status, "sources": [source] if source else [], "claims": [],
                "round": self.round, "created_at": now(), "trust": trust}
        self.terms[key] = term
        self._touch(key)
        return term

    def _learn_name(self, term: dict, label: str) -> bool:
        label = clean_label(label)
        if not label or label_key(label) in _keys_of(term):
            return False
        term["synonyms"] = sorted(set(term.get("synonyms") or []) | {label})
        self._touch(term["id"])
        return True

    def _add_source(self, term: dict, source: dict) -> bool:
        if not source:
            return False
        key = source_key(source)
        if any(source_key(s) == key for s in term.get("sources") or []):
            return False
        term.setdefault("sources", []).append(source)
        self._touch(term["id"])
        return True

    def _set_type(self, term: dict, type_: str) -> None:
        if type_ and type_ != DEFAULT_TYPE and term.get("type", DEFAULT_TYPE) in ("", DEFAULT_TYPE):
            term["type"] = type_
            self._touch(term["id"])

    # ------------------------------------------------------------ claims

    def claim(self, item: Claim, kind: str = "term") -> None:
        if not usable_label(item.subject) or not item.value:
            return
        key, learned = self.resolve(item.subject)
        if item.status == "superseded" and item.attach_to:
            key, learned = self.resolve(item.attach_to)[0], False
            if key is None:
                return  # the value that stands was not kept: nothing to attach a settled-against value to
        record = {"slot": slot(item.predicate), "predicate": item.predicate, "value": item.value, "trust": item.trust,
                  "source": item.source, **({"confirmed_by": item.confirmed_by} if item.confirmed_by else {})}
        if key is not None and learned and any(relate(c.get("value", ""), item.value) == CONTEST
                                               for c in self._current(self.terms[key], record["slot"])):
            key, learned = None, False  # only nearly the same name, and it says something else: another thing, not a [쟁점]
        if key is None:
            definition = item.value
            status = VERIFIED if item.verified else DRAFT
            term = self._new_term(item.subject, kind, item.type, item.source, definition, status, self._effective(item))
            if item.verified:
                term.update(verified_by="agent:source-match", verified_at=now())
            term["claims"].append({**record, "status": item.status or "current"})
            for alias in item.aliases:
                self._learn_name(term, alias)
            if item.trust == NEEDS_VERIFICATION and not item.confirmed_by:
                term["needs_verification"] = True
            self._added_ids.add(term["id"])
            self.changes.added.append({"id": term["id"], "label": term["label"], "definition": definition, "type": term["type"],
                                       "trust": term["trust"], "source": item.source})
            return
        term = self.terms[key]
        if item.source and any(source_key(c.get("source") or {}) == source_key(item.source) and c.get("slot") == record["slot"]
                               for c in term.get("claims") or []):
            if learned and self._learn_name(term, item.subject):
                self.changes.merged.append({"from": clean_label(item.subject), "into": term["label"], "id": key, "kind": "synonym"})
            return  # this exact statement from this exact source is already in: nothing to do (idempotent)
        if learned and self._learn_name(term, item.subject):
            self.changes.merged.append({"from": clean_label(item.subject), "into": term["label"], "id": key, "kind": "synonym"})
        for alias in item.aliases:
            self._learn_name(term, alias)
        self._set_type(term, item.type)
        fresh = key in self._added_ids
        if item.status == "superseded":
            self._add_source(term, item.source)
            term["claims"].append({**record, "status": "superseded"})
            return
        current = self._current(term, record["slot"])
        if not current:
            self._add_source(term, item.source)
            term["claims"].append({**record, "status": "current"})
            if not term.get("definition") or term.get("kind") == "stub":
                term["definition"] = item.value
                term["kind"] = kind if term.get("kind") == "stub" else term.get("kind", kind)
            if not fresh:
                self.changes.refined.append({"id": key, "label": term["label"], "slot": record["slot"], "value": item.value,
                                             "kind": "new_slot", "source": item.source})
            return
        verdict = max((relate(c.get("value", ""), item.value) for c in current), key=[SAME, REFINE, CONTEST].index)
        self._add_source(term, item.source)
        if verdict == SAME:
            term["claims"].append({**record, "status": "current"})
            if not fresh:
                self.changes.merged.append({"from": clean_label(item.subject), "into": term["label"], "id": key, "kind": "same",
                                            "source": item.source})
            return
        if verdict == REFINE:
            term["claims"].append({**record, "status": "current"})
            if not fresh:
                self.changes.refined.append({"id": key, "label": term["label"], "slot": record["slot"], "value": item.value,
                                             "kind": "refine", "source": item.source})
            return
        # a disagreement: the weight of the sources decides whether it is a [쟁점]
        mine = TRUST_RANK.get(self._effective(item), 1)
        theirs = max(TRUST_RANK.get(c.get("confirmed_by") or c.get("trust", HUMAN), 1) for c in current)
        if term.get("status") == CERTIFIED or mine < theirs:
            term["claims"].append({**record, "status": NEEDS_VERIFICATION})
            self.changes.refined.append({"id": key, "label": term["label"], "slot": record["slot"], "value": item.value,
                                         "kind": NEEDS_VERIFICATION, "source": item.source})
            return
        if mine > theirs:
            for c in current:
                c["status"] = "superseded"
            term["claims"].append({**record, "status": "current"})
            term["definition"] = item.value if not _is_contested(term) else term["definition"]
            term["trust"] = self._effective(item)
            term.pop("needs_verification", None)
            self.changes.refined.append({"id": key, "label": term["label"], "slot": record["slot"], "value": item.value,
                                         "kind": "override", "source": item.source})
            return
        term["claims"].append({**record, "status": "current"})
        self._contest(term, [*current, record])

    @staticmethod
    def _current(term: dict, claim_slot: str) -> list[dict]:
        current = [c for c in term.get("claims") or [] if c.get("slot") == claim_slot and c.get("status") in (None, "current")]
        if not current and not term.get("claims") and term.get("definition") and claim_slot == "역할":
            # a term with only a definition and no claims (written outside the merger): compare with that
            current = [{"slot": "역할", "value": term["definition"].removeprefix("[쟁점]").strip(), "trust": _best_trust(term) or DOCUMENT}]
        return current

    def _effective(self, item: Claim) -> str:
        if item.confirmed_by and TRUST_RANK.get(item.confirmed_by, 0) > TRUST_RANK.get(item.trust, 0):
            return item.confirmed_by
        return item.trust

    def _contest(self, term: dict, readings: list[dict]) -> None:
        interpretations = list(term.get("interpretations") or [])
        if not interpretations:
            first = readings[0]
            interpretations = [{"definition": first.get("value") or term.get("definition", ""), "source": first.get("source")
                                or (term.get("sources") or [{}])[0], "trust": first.get("trust", "")}]
        known = {(source_key(i.get("source") or {}), clean_value(i.get("definition", ""))) for i in interpretations}
        for reading in readings:
            pair = (source_key(reading.get("source") or {}), clean_value(reading.get("value", "")))
            if pair not in known and reading.get("value"):
                known.add(pair)
                interpretations.append({"definition": reading["value"], "source": reading.get("source"), "trust": reading.get("trust", "")})
        if len(interpretations) < 2:
            return
        term["interpretations"] = interpretations
        if not str(term.get("definition", "")).startswith("[쟁점]"):
            term["definition"] = "[쟁점] " + (term.get("definition") or interpretations[0]["definition"])
        term["status"] = DRAFT  # two readings: a person decides
        self._touch(term["id"])
        if term["id"] not in self._contested_ids:
            self._contested_ids.add(term["id"])
            self.changes.contested.append({"id": term["id"], "label": term["label"], "interpretations": interpretations,
                                           "evidence_ts": sorted({str((i.get("source") or {}).get("ts")) for i in interpretations
                                                                  if (i.get("source") or {}).get("ts")}, key=float)})

    def contest_group(self, label: str, items: list[Claim]) -> None:
        """Claims the thread reader found disagreeing (same subject and slot, different authors, nobody settled it):
        one term, one [쟁점] with every reading, unless the term is already contested with these very sources."""
        usable = [c for c in items if c.value]
        if not usable:
            return
        label = clean_label(label) if usable_label(label) else usable[0].subject
        key, learned = self.resolve(label)
        if key is None:
            first = usable[0]
            term = self._new_term(label, "term", first.type, first.source, first.value, DRAFT, first.trust)
            term["claims"].append({"slot": slot(first.predicate), "predicate": first.predicate, "value": first.value,
                                   "trust": first.trust, "source": first.source, "status": "current"})
            self._added_ids.add(term["id"])
            self.changes.added.append({"id": term["id"], "label": term["label"], "definition": first.value, "type": term["type"],
                                       "trust": first.trust, "source": first.source})
            rest = usable[1:]
        else:
            term = self.terms[key]
            if learned:
                self._learn_name(term, label)
            rest = usable
        readings = []
        seen = {(source_key(c.get("source") or {}), clean_value(c.get("value", ""))) for c in term.get("claims") or []}
        for item in rest:
            pair = (source_key(item.source), clean_value(item.value))
            if pair in seen:
                continue
            seen.add(pair)
            self._add_source(term, item.source)
            record = {"slot": slot(item.predicate), "predicate": item.predicate, "value": item.value, "trust": item.trust,
                      "source": item.source, "status": "current"}
            term["claims"].append(record)
            readings.append(record)
        if not readings:
            return
        if key is not None and term.get("status") == CERTIFIED:
            for record in readings:
                record["status"] = NEEDS_VERIFICATION
            return
        current = [c for c in term["claims"] if c.get("status") == "current"]
        self._contest(term, current)

    # ------------------------------------------------------------ relations and entities

    def endpoint(self, label: str, type_: str, source: dict) -> str | None:
        label = clean_label(label)
        if not usable_label(label) and not label.startswith("?"):
            return None
        key, learned = self.resolve(label)
        if key is not None:
            term = self.terms[key]
            if learned and self._learn_name(term, label):
                self.changes.merged.append({"from": label, "into": term["label"], "id": key, "kind": "synonym"})
            self._set_type(term, type_)
            return key
        term = self._new_term(label, "stub", type_, source, "", DRAFT)
        self.changes.stubs.append({"id": term["id"], "label": term["label"], "type": term["type"], "source": source})
        return term["id"]

    def relation(self, item: Relation) -> None:
        s_id = self.endpoint(item.s, item.s_type, item.source)
        if s_id is None:
            return
        o_id = "" if item.missing else self.endpoint(item.o, item.o_type, item.source)
        if o_id is None:
            return
        p = item.p.strip()[:60]
        existing = next((r for r in [*self.relations, *self.changes.relations]
                         if r.get("s_id") == s_id and r.get("p") == p and (r.get("o_id") or "") == o_id
                         and (o_id or r.get("o") == item.o)), None)
        if existing is not None:
            if item.source and not any(source_key(s) == source_key(item.source) for s in existing.get("sources") or []) \
                    and existing in self.changes.relations:
                existing.setdefault("sources", []).append(item.source)
            return
        self.changes.relations.append({"s": self.terms[s_id]["label"], "p": p, "o": item.o if item.missing else self.terms[o_id]["label"],
                                       "s_id": s_id, "o_id": o_id, "status": "missing" if item.missing else item.status,
                                       "round": self.round, "sources": [item.source] if item.source else [],
                                       **({"quote": item.source.get("quote", "")} if item.source.get("quote") else {})})

    def entity(self, item: Entity) -> None:
        """A typed entity from a gap pack: one term per thing, its type kept, its synonyms learned."""
        if not usable_label(item.name):
            return
        key, learned = self.resolve(item.name)
        for synonym in item.synonyms:
            key = key or self.resolve(synonym)[0]
        if key is None:
            term = self._new_term(item.name, "term", item.type, item.source)
            for synonym in item.synonyms:
                self._learn_name(term, synonym)
            self._added_ids.add(term["id"])
            self.changes.added.append({"id": term["id"], "label": term["label"], "definition": "", "type": term["type"],
                                       "trust": "", "source": item.source})
            return
        term = self.terms[key]
        changed = self._add_source(term, item.source)
        for name in [item.name, *item.synonyms]:
            if self._learn_name(term, name):
                self.changes.merged.append({"from": clean_label(name), "into": term["label"], "id": key, "kind": "synonym"})
                changed = False
        self._set_type(term, item.type)
        if changed and key not in self._added_ids:
            self.changes.merged.append({"from": clean_label(item.name), "into": term["label"], "id": key, "kind": "same",
                                        "source": item.source})


# --------------------------------------------------------------------------- entry points


def merge(data: dict, claims: Iterable[Claim] = (), relations: Iterable[Relation] = (), entities: Iterable[Entity] = (),
          same_as: dict[str, str | None] | None = None, round_number: int = 0, write: bool = True,
          packs: dict[str, dict] | None = None) -> ChangeSet:
    """Merge statements into a store document: build the change set on a copy, then (``write``) apply it."""
    upgrade(data)
    merger = Merger(data, same_as, round_number)
    claims = list(claims)
    groups: dict[str, list[Claim]] = {}
    for item in claims:
        if item.group:
            groups.setdefault(item.group, []).append(item)
    for item in entities:
        merger.entity(item)
    for item in sorted((c for c in claims if not c.group), key=lambda c: c.status == "superseded"):
        merger.claim(item)
    for items in groups.values():
        label = next((c.group_label for c in items if usable_label(c.group_label)), "") or \
            min((c.subject for c in items), key=lambda s: (len(label_key(s)), s))
        merger.contest_group(label, items)
    for item in relations:
        merger.relation(item)
    merger.changes.packs = dict(packs or {})
    if write:
        apply(data, merger.changes)
    return merger.changes


async def same_as_for(data: dict, labels: Iterable[str], complete: Complete | None) -> dict[str, str | None]:
    """The model's same-as answers for the labels whose match is uncertain (asked before taking the store lock)."""
    if complete is None:
        return {}
    upgrade(data)
    return await judge_same_as(Merger(data).pending_same_as(labels), complete)


def labels_of(claims: Iterable[Claim], relations: Iterable[Relation] = ()) -> list[str]:
    out = [c.subject for c in claims]
    for relation in relations:
        out += [relation.s, relation.o]
    return list(dict.fromkeys(out))


def merge_understanding(data: dict, result: dict, same_as: dict[str, str | None] | None = None, write: bool = True) -> ChangeSet:
    claims, relations, _types = items_from_understanding(result)
    return merge(data, claims, relations, same_as=same_as, write=write)


def pack_items(pack: dict) -> tuple[list[Entity], list[Relation]]:
    """A gap pack's typed entities and its open slots (relations to the value nobody has answered yet)."""
    source = {"type": "knowledge_gap", "pack_id": pack.get("id", ""), "pack": pack.get("name", "")}
    entities = [Entity(name=str(e.get("name") or ""), type=str(e.get("type") or DEFAULT_TYPE), synonyms=list(e.get("synonyms") or []),
                       source=source) for e in pack.get("entities") or [] if e.get("name")]
    relations = [Relation(s=str(r.get("from") or ""), p=str(r.get("type") or ""), o=str(r.get("to") or "?"), source=source, missing=True)
                 for r in pack.get("relations") or [] if r.get("from") and r.get("type")]
    return entities, relations


def merge_pack(data: dict, pack: dict, write: bool = True) -> ChangeSet:
    """Store a gap pack in the organization's ontology: the pack itself, its entities as typed terms, its slots."""
    entities, relations = pack_items(pack)
    stored = {**pack, "saved_at": now()}
    return merge(data, entities=entities, relations=relations, packs={pack["id"]: stored}, write=write)


# --------------------------------------------------------------------------- schema


def upgrade(data: dict) -> dict:
    """Schema 1 (loop store before S4) → 2, in place and idempotent: every term gets a type, synonyms and its
    definition as a claim; the document gets a ``packs`` map. Nothing is removed."""
    for key, empty in (("terms", {}), ("relations", []), ("actions", {}), ("questions", {}), ("rounds", []),
                       ("seen_documents", {}), ("packs", {})):
        data.setdefault(key, empty)
    if data.get("schema_version") == SCHEMA_VERSION:
        return data
    for term in data["terms"].values():
        term.setdefault("type", DEFAULT_TYPE)
        term.setdefault("synonyms", [])
        if "claims" not in term:
            definition = str(term.get("definition") or "")
            claims = []
            if term.get("interpretations"):
                for reading in term["interpretations"]:
                    claims.append({"slot": DEFINITION_SLOT, "predicate": DEFINITION_SLOT, "value": reading.get("definition", ""),
                                   "trust": DOCUMENT, "source": reading.get("source") or {}, "status": "current"})
            elif definition and term.get("kind", "term") == "term":
                claims.append({"slot": DEFINITION_SLOT, "predicate": DEFINITION_SLOT, "value": definition, "trust": DOCUMENT,
                               "source": (term.get("sources") or [{}])[0], "status": "current"})
            term["claims"] = claims
    data["schema_version"] = SCHEMA_VERSION
    return data


def document_claim(label: str, definition: str, source: dict, verified: bool) -> Claim:
    return Claim(subject=label, value=definition.strip(), predicate=DEFINITION_SLOT, trust=DOCUMENT, source=source, verified=verified)
