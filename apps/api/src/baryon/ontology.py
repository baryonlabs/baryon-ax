"""Typed entities and open slots for ontology packs built from knowledge gaps.

A knowledge gap is a question nobody could answer, so the useful part of a pack is not a bag of
terms but *what is missing*: "법인카드 해외 결제 한도는 얼마야?" says the card exists and its limit
is unknown (`법인카드 —한도→ [?금액]`). This module extracts that structure two ways:

* `rule_extract`: no model. Noun phrases only (verb forms and question words are dropped), and a
  slot from Korean question patterns (얼마 → 한도, 누구에게 신고 → 신고처, 언제·며칠 → 기한 …).
* `model_extract`: the configured OpenAI-compatible model returns JSON in a fixed schema; anything
  that does not validate is dropped and the rules are used instead.

The strict-schema approach follows NVIDIA Auto Ontology's term extraction (Apache-2.0, no code
copied); see docs/ontology-quality.md. `apps/api/eval/ontology_eval.py` scores both on a Korean
gold set.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("baryon.ontology")

# Amounts and durations are the *values* a slot asks for, not entities.
EntityType = Literal["사람·조직", "시스템", "문서·규정", "절차", "자산·대상", "개념"]
RelationType = Literal["한도", "기한", "승인자", "신고처", "담당", "제출처", "근거"]
ValueType = Literal["금액", "기간", "사람·조직", "시스템", "문서·규정"]

VALUE_TYPE: dict[str, str] = {
    "한도": "금액", "기한": "기간", "승인자": "사람·조직", "신고처": "사람·조직",
    "담당": "사람·조직", "제출처": "시스템", "근거": "문서·규정",
}


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=40,
                      description="A noun phrase the question is about, as written (법인카드, 연차 휴가). "
                                  "Never a verb form (분실하면, 신고해) or a question word (얼마, 누구).")
    type: EntityType = Field("개념", description="What kind of thing it is.")
    synonyms: list[str] = Field(default_factory=list, max_length=10,
                                description="Other names for the same thing, merged from earlier packs.")


class Slot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(..., min_length=1, max_length=40,
                         description="The entity whose value is missing (법인카드).")
    relation: RelationType = Field(..., description="Which value the question asks for.")
    value_type: ValueType = Field(..., description="The kind of the missing value.")
    question: str = Field("", max_length=300, description="The question this slot came from.")


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[Entity] = Field(default_factory=list, max_length=30)
    slots: list[Slot] = Field(default_factory=list, max_length=30)
    method: str = "rules"


# --- rules -------------------------------------------------------------------------------------

# Checked in order; the first match names the missing value.
_SLOT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("근거", re.compile(r"기준|규정|지침|근거")),
    ("승인자", re.compile(r"승인|결재")),
    ("신고처", re.compile(r"신고|연락|알려|알리|보고(?!서)")),  # 보고해 = report to; 보고서 = a report (a document)
    ("한도", re.compile(r"한도|상한|얼마")),
    ("기한", re.compile(r"언제|마감|며칠|기한|일정|까지|걸려|걸리")),
    ("담당", re.compile(r"요청|문의|담당|누구한테|누구에게")),
    ("제출처", re.compile(r"시스템|어디서|어디에|올려|올리|제출|신청")),
)
# Words that name the slot itself rather than an entity ("한도는", "마감이").
_SLOT_WORDS = {"한도", "상한", "마감", "기한", "기준", "규정", "지침", "근거", "결재선", "승인", "시스템"}
_QUESTION_WORDS = {
    "누구", "누가", "누구에게", "누구한테", "어디", "어디서", "어디에", "어느", "어떤", "어떻게", "언제", "며칠",
    "얼마", "무엇", "뭐", "무슨", "왜", "전", "필요", "몇", "혹시", "그럼", "근데", "그리고",
}
_PARTICLES = ("에서는", "에게서", "한테서", "이라고", "라고", "에서", "에게", "한테", "으로", "까지", "부터",
              "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "로")
# Endings of verbs, adjectives and copulas: a token ending this way is not an entity.
_PREDICATE_END = re.compile(
    r"(하면|하려면|나면|되면|려면|해야|해요|해|했어|했|하나요|하나|할까|할까요|하는|할|돼|돼요|됐|되나|"
    r"이야|야|이에요|예요|있어|없어|어|아|지|나요|까요|까|니|냐|걸려|걸리나|올려|넣어|보내|받아)$"
)
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]+")


def _strip_particle(token: str) -> tuple[str, bool]:
    for particle in _PARTICLES:
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)], True
    return token, False


def _noun(token: str) -> str | None:
    stem, _ = _strip_particle(token)
    if len(stem) < 2 or stem in _QUESTION_WORDS or token in _QUESTION_WORDS:
        return None
    if _PREDICATE_END.search(token) and not _strip_particle(token)[1]:
        return None  # 분실하면, 신고해, 얼마야
    if _PREDICATE_END.search(stem) and stem not in _SLOT_WORDS:
        return None
    return stem


# Particles that also appear inside ordinary nouns (가 in 휴가, 도 in 한도) are left to _noun's shorter
# list; these multi-syllable ones are safe to call a particle at the end of a pack label.
_LONG_PARTICLES = ("에서는", "에게서", "한테서", "이라고", "으로는", "에게는", "에서", "에게", "한테",
                   "으로", "까지", "부터", "라고", "에는")


def is_entity_noise(name: str) -> bool:
    """True when a label is a verb form, a question word or a word with a particle stuck to it — not a noun
    phrase an ontology should hold ("분실하면", "신고해", "얼마", "법인카드에서"). Used by ontology packs."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    if not text or text in _QUESTION_WORDS:
        return True
    last = text.split()[-1]
    if any(last.endswith(particle) and len(last) - len(particle) >= 2 for particle in _LONG_PARTICLES):
        return True
    stem, had_particle = _strip_particle(text)
    return bool(_PREDICATE_END.search(text)) and not had_particle and stem == text


def rule_extract_question(question: str) -> Extraction:
    tokens = _TOKEN.findall(question or "")
    relation = next((name for name, pattern in _SLOT_PATTERNS if pattern.search(question or "")), None)
    nouns: list[tuple[str, bool]] = []  # (noun, carried a particle)
    for token in tokens:
        noun = _noun(token)
        if noun is None:
            continue
        nouns.append((noun, _strip_particle(token)[1]))
    entities: list[str] = []
    for noun, _ in nouns:
        if noun not in _SLOT_WORDS and noun not in entities:
            entities.append(noun)
    # A leading pair of bare nouns is usually one concept: 연차 휴가, 고객 데이터, 보안 사고.
    if len(nouns) >= 2 and not nouns[0][1] and nouns[1][0] not in _SLOT_WORDS:
        pair = f"{nouns[0][0]} {nouns[1][0]}"
        entities = [pair] + [name for name in entities if name not in {nouns[0][0], nouns[1][0]}]
    slots: list[Slot] = []
    if relation and entities:
        slots.append(Slot(subject=entities[0], relation=relation, value_type=VALUE_TYPE[relation], question=question))
    return Extraction(entities=[Entity(name=name) for name in entities[:6]], slots=slots, method="rules")


def merge(extractions: list[Extraction], method: str) -> Extraction:
    entities: dict[str, Entity] = {}
    slots: list[Slot] = []
    seen: set[tuple[str, str]] = set()
    for extraction in extractions:
        for entity in extraction.entities:
            key = entity.name.replace(" ", "")
            if key not in entities or entities[key].type == "개념":
                entities[key] = entity
        for slot in extraction.slots:
            key = (slot.subject.replace(" ", ""), slot.relation)
            if key not in seen:
                seen.add(key)
                slots.append(slot)
    return Extraction(entities=list(entities.values())[:30], slots=slots[:30], method=method)


def rule_extract(questions: list[str]) -> Extraction:
    return merge([rule_extract_question(question) for question in questions], "rules")


# --- model --------------------------------------------------------------------------------------

_SYSTEM = """You build an ontology draft from questions that a company's documents could not answer.
Return JSON only, exactly in this schema:
{"entities": [{"name": str, "type": one of %s}],
 "slots": [{"subject": str, "relation": one of %s, "value_type": one of %s, "question": str}]}
Rules:
- entities are noun phrases the questions are about, written as in the question (법인카드, 연차 휴가, 고객 데이터).
  Never verb forms (분실하면, 신고해), question words (얼마, 누구, 언제) or the asked-for attribute alone (한도, 마감).
- a slot is the value a question asks for and nobody has answered: subject is an entity name from the list,
  relation says which value (한도 = a limit or amount, 기한 = a deadline or duration, 승인자 = who approves,
  신고처 = whom to report or contact, 담당 = who handles a request, 제출처 = which system or place to submit to,
  근거 = which rule or document defines it), value_type is its kind.
- an entity is the thing itself: leave out situations and attributes (in "출장 중 회사 차량 주유비 한도는?" the
  entity is 회사 차량 주유비, not "출장 중 회사 차량 주유비"). Amounts and durations are never entities.
- keep the thing that owns a value apart from the words that describe the value: in "사내 식당 쿠폰 월 지급
  한도는 얼마야?" the entities are 사내 식당 쿠폰 and 월 지급, and the slot subject is 사내 식당 쿠폰 (not the whole
  phrase); in "복지 포인트 사용 기준 문서는 어디 있어?" the subject is 복지 포인트, and 기준 문서 is the missing
  value, not an entity.
- one slot per question at most; copy the question text into "question".
- Korean names stay in Korean."""


def _schema_prompt() -> str:
    return _SYSTEM % (
        list(EntityType.__args__), list(RelationType.__args__), list(ValueType.__args__)  # type: ignore[attr-defined]
    )


def parse_model_output(text: str, questions: list[str]) -> Extraction | None:
    """Validate a model's JSON against the schema; drop entities that are verb forms and slots whose
    subject is not an entity. None when nothing usable is left."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
        # Small models often list entities as bare names; accept those as untyped entities.
        entities_in = [item if isinstance(item, dict) else {"name": str(item)} for item in raw.get("entities", [])]
        for item in entities_in:  # an unknown type (금액, "object", …) is not worth losing the entity over
            if item.get("type") not in EntityType.__args__:  # type: ignore[attr-defined]
                item["type"] = "개념"
            item.pop("synonyms", None)
        extraction = Extraction(entities=entities_in, slots=raw.get("slots", []), method="model")
    except (ValueError, ValidationError, AttributeError) as error:
        logger.info("model extraction rejected: %s", type(error).__name__)
        return None
    entities = [entity for entity in extraction.entities
                if entity.name.strip() not in _QUESTION_WORDS and not _PREDICATE_END.search(entity.name.strip())]
    names = {entity.name.replace(" ", "") for entity in entities}
    slots = [slot for slot in extraction.slots if slot.subject.replace(" ", "") in names
             and slot.value_type == VALUE_TYPE[slot.relation]]
    if not entities:
        return None
    return Extraction(entities=entities, slots=slots, method="model")


async def model_extract_each(questions: list[str], complete: Callable[[list[dict[str, str]]], Awaitable[str]],
                             concurrency: int = 4) -> Extraction:
    """One model call per question, as the evaluation scores it; several questions in one prompt made
    the model keep whole phrases ("회사카드 해외 결제") as entities. Results are merged."""
    import asyncio

    gate = asyncio.Semaphore(concurrency)

    async def one(question: str) -> Extraction:
        async with gate:
            return await model_extract([question], complete)

    parts = await asyncio.gather(*(one(question) for question in questions[:20]))
    methods = {part.method for part in parts}
    return merge(list(parts), "model+rules" if "model+rules" in methods else "rules")


async def model_extract(questions: list[str], complete: Callable[[list[dict[str, str]]], Awaitable[str]]) -> Extraction:
    """`complete(messages) -> text` calls the model. Falls back to the rules on any failure."""
    listing = "\n".join(f"- {question}" for question in questions[:20])
    try:
        text = await complete([{"role": "system", "content": _schema_prompt()},
                               {"role": "user", "content": f"Questions:\n{listing}"}])
    except Exception as error:  # noqa: BLE001 - the model is optional; a pack must still be built
        logger.info("model extraction failed: %s", type(error).__name__)
        return rule_extract(questions)
    extraction = parse_model_output(text, questions)
    if extraction is None:
        return rule_extract(questions)
    return with_rule_relations(extraction, questions)


def rule_relation(question: str) -> str | None:
    return next((name for name, pattern in _SLOT_PATTERNS if pattern.search(question or "")), None)


def with_rule_relations(extraction: Extraction, questions: list[str]) -> Extraction:
    """The model brackets noun phrases well (법인카드 vs 법인카드 해외); the question patterns name the
    missing value more reliably (요청 → 담당, not 신고처). Keep the model's subjects and let the
    patterns decide the relation; add a slot the model missed when a pattern finds one."""
    names = [entity.name for entity in extraction.entities]
    slots: list[Slot] = []
    for question in questions:
        relation = rule_relation(question)
        model_slot = next((slot for slot in extraction.slots if slot.question.strip() == question.strip()), None)
        if model_slot is None and len(questions) == 1 and extraction.slots:
            model_slot = extraction.slots[0]
        subject = model_slot.subject if model_slot else next(
            (name for name in names if name.replace(" ", "") in question.replace(" ", "")), None)
        if relation and subject:
            slots.append(Slot(subject=subject, relation=relation, value_type=VALUE_TYPE[relation], question=question))
        elif model_slot:
            slots.append(model_slot)
    return Extraction(entities=extraction.entities, slots=slots, method="model+rules")


# --- merging with earlier packs ------------------------------------------------------------------

_MERGE_SYSTEM = """You keep one name per business concept across ontology drafts.
For each NEW name, decide whether it is the same real-world thing as one EXISTING name (another word for it,
an abbreviation, or a spacing variant), not merely related. 법인카드 and 회사카드 are the same thing; 법인카드
and 법인카드 한도 are not (the second is an attribute); 연차 and 연차 휴가 are the same thing.
Return JSON only: {"same": [{"new": str, "existing": str}]}. Use names exactly as listed. Leave out names that
have no match."""


def _key(name: str) -> str:
    return re.sub(r"\s+", "", name or "").lower()


def _rename(extraction: Extraction, renames: dict[str, str]) -> Extraction:
    entities: dict[str, Entity] = {}
    for entity in extraction.entities:
        target = renames.get(entity.name)
        if target and target != entity.name:
            merged = entities.get(_key(target)) or Entity(name=target, type=entity.type)
            merged.synonyms = sorted(set(merged.synonyms) | {entity.name})
            entities[_key(target)] = merged
        elif _key(entity.name) not in entities:
            entities[_key(entity.name)] = entity
    slots = [slot.model_copy(update={"subject": renames.get(slot.subject, slot.subject)}) for slot in extraction.slots]
    return Extraction(entities=list(entities.values()), slots=slots, method=extraction.method)


async def resolve_synonyms(extraction: Extraction, existing: list[str],
                           complete: Callable[[list[dict[str, str]]], Awaitable[str]] | None = None) -> Extraction:
    """Name entities the way earlier packs already do, so one concept is not split across packs.

    Spacing variants are merged without a model (연차휴가 → 연차 휴가). With a model, other words for the same
    thing are merged too (회사카드 → 법인카드), following NVIDIA Auto Ontology's merge decisions: the model only
    picks from the listed names, and anything else in its answer is ignored."""
    existing = [name for name in dict.fromkeys(existing) if name.strip()][:80]
    if not existing:
        return extraction
    by_key = {_key(name): name for name in existing}
    renames = {entity.name: by_key[_key(entity.name)] for entity in extraction.entities
               if _key(entity.name) in by_key and by_key[_key(entity.name)] != entity.name}
    pending = [entity.name for entity in extraction.entities if _key(entity.name) not in by_key]
    if complete and pending:
        try:
            text = await complete([{"role": "system", "content": _MERGE_SYSTEM},
                                   {"role": "user", "content": "NEW: " + json.dumps(pending, ensure_ascii=False)
                                    + "\nEXISTING: " + json.dumps(existing, ensure_ascii=False)}])
            match = re.search(r"\{.*\}", text or "", re.S)
            for pair in (json.loads(match.group(0)).get("same", []) if match else []):
                new, old = str(pair.get("new", "")), str(pair.get("existing", ""))
                if new in pending and old in existing and _key(new) != _key(old):
                    renames[new] = old
        except Exception as error:  # noqa: BLE001 - merging is an improvement, never a reason to fail
            logger.info("synonym merge skipped: %s", type(error).__name__)
    return _rename(extraction, renames) if renames else extraction


def pack_structure(extraction: Extraction) -> dict[str, Any]:
    """Pack fields: typed entities, relations (each slot as a relation to its missing value) and slots."""
    return {
        "entities": [{"name": entity.name, "type": entity.type, "synonyms": entity.synonyms, "source": "knowledge_gap"}
                     for entity in extraction.entities],
        "relations": [{"from": slot.subject, "type": slot.relation, "to": f"?{slot.value_type}",
                       "confidence": "missing"} for slot in extraction.slots],
        "slots": [slot.model_dump() | {"status": "open"} for slot in extraction.slots],
        "extraction": extraction.method,
    }
