"""Ontology pack extraction: typed entities and open slots (docs/ontology-quality.md)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

from baryon.ontology import (
    model_extract,
    parse_model_output,
    rule_extract,
    rule_extract_question,
)

EVAL = Path(__file__).resolve().parents[1] / "eval" / "ontology_eval.py"


def test_rules_drop_verb_forms_and_name_the_missing_value() -> None:
    card = rule_extract_question("법인카드 분실하면 누구에게 신고해?")
    assert [entity.name for entity in card.entities] == ["법인카드"]          # not 분실하면, 신고해
    assert [(slot.subject, slot.relation, slot.value_type) for slot in card.slots] == [("법인카드", "신고처", "사람·조직")]
    leave = rule_extract_question("연차 휴가 신청은 며칠 전까지 해야 해?")
    assert leave.entities[0].name == "연차 휴가" and leave.slots[0].relation == "기한"


def test_rules_merge_questions_without_duplicates() -> None:
    merged = rule_extract(["법인카드 해외 결제 한도는 얼마야?", "법인카드 분실하면 누구에게 신고해?"])
    assert [slot.relation for slot in merged.slots] == ["한도", "신고처"]
    assert len({entity.name for entity in merged.entities}) == len(merged.entities)


def test_model_output_is_validated_and_relations_come_from_the_question() -> None:
    raw = json.dumps({"entities": ["노트북"],  # small models list bare names
                      "slots": [{"subject": "노트북", "relation": "신고처", "value_type": "사람·조직",
                                 "question": "신규 입사자 노트북은 누구한테 요청해?"}]}, ensure_ascii=False)

    async def complete(messages):
        return raw

    extraction = asyncio.run(model_extract(["신규 입사자 노트북은 누구한테 요청해?"], complete))
    assert extraction.method == "model+rules"
    assert [(slot.subject, slot.relation) for slot in extraction.slots] == [("노트북", "담당")]  # 요청 → 담당


def test_bad_model_output_falls_back_to_rules() -> None:
    assert parse_model_output("not json", []) is None
    assert parse_model_output('{"entities": [{"name": "얼마야"}], "slots": []}', []) is None  # only noise left

    async def broken(messages):
        raise TimeoutError

    assert asyncio.run(model_extract(["야근 식대 한도는 얼마야?"], broken)).method == "rules"


def test_rules_meet_the_quality_bar_on_the_korean_gold_set() -> None:
    spec = importlib.util.spec_from_file_location("ontology_eval", EVAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    gold = json.loads(module.GOLD.read_text(encoding="utf-8"))["questions"]
    rules = module.summarize([module.score_question(item, rule_extract_question(item["q"])) for item in gold])
    legacy = module.summarize([module.score_question(item, module.legacy_extract(item["q"])) for item in gold])
    assert rules["entity_precision"] >= 0.8 and rules["noise_rate"] == 0 and rules["slot_recall"] >= 0.8
    assert rules["entity_precision"] > legacy["entity_precision"] and legacy["noise_rate"] > 0


def test_amounts_are_not_entities_and_unknown_types_are_kept_as_concepts() -> None:
    extraction = parse_model_output(json.dumps({"entities": [{"name": "렌터카 비용", "type": "금액"}],
                                                "slots": []}, ensure_ascii=False), [])
    assert [(entity.name, entity.type) for entity in extraction.entities] == [("렌터카 비용", "개념")]


def test_synonyms_merge_into_the_name_earlier_packs_use() -> None:
    from baryon.ontology import resolve_synonyms

    extraction = rule_extract(["회사카드 해외 결제 한도는 얼마야?", "연차휴가는 누가 승인해?"])
    names = [entity.name for entity in extraction.entities]
    assert "연차휴가" in names

    async def model(messages):  # the model may only pick listed names; the made-up pair is ignored
        return json.dumps({"same": [{"new": "회사카드 해외", "existing": "법인카드"},
                                    {"new": "회사카드", "existing": "법인카드"},
                                    {"new": "없는 이름", "existing": "법인카드"}]}, ensure_ascii=False)

    merged = asyncio.run(resolve_synonyms(extraction, ["법인카드", "연차 휴가"], model))
    by_name = {entity.name: entity for entity in merged.entities}
    assert "연차 휴가" in by_name and "연차휴가" in by_name["연차 휴가"].synonyms   # spacing: no model needed
    assert "법인카드" in by_name and "회사카드 해외" in by_name["법인카드"].synonyms  # model decision
    assert {slot.subject for slot in merged.slots} == {"법인카드", "연차 휴가"}
    assert asyncio.run(resolve_synonyms(extraction, [], model)) is extraction        # nothing earlier: unchanged


def test_each_question_gets_its_own_model_call() -> None:
    from baryon.ontology import model_extract_each

    seen: list[str] = []

    async def model(messages):
        question = messages[-1]["content"].split("- ", 1)[1]
        seen.append(question)
        subject = "법인카드" if "카드" in question else "렌터카"
        return json.dumps({"entities": [subject], "slots": [{"subject": subject, "relation": "한도", "value_type": "금액",
                                                           "question": question}]}, ensure_ascii=False)

    merged = asyncio.run(model_extract_each(["법인카드 한도는 얼마야?", "렌터카 반납이 늦으면 누구에게 연락해?"], model))
    assert sorted(seen) == ["렌터카 반납이 늦으면 누구에게 연락해?", "법인카드 한도는 얼마야?"]
    assert {(slot.subject, slot.relation) for slot in merged.slots} == {("법인카드", "한도"), ("렌터카", "신고처")}
