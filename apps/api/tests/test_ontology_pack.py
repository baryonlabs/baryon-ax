"""Starter ontology packs: strict merge v2 schema, idempotent import, and the quality gate.

The numbers in ``STARTER_THRESHOLDS`` are pinned here on purpose (docs/ontology-quality.md): the starter
pack gate must not quietly loosen when the sample or the scorer changes.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from baryon.loop import LoopStore
from baryon.ontology import is_entity_noise
from baryon.ontology_merge import OWNER, Claim, merge, term_id
from baryon.ontology_pack import (
    ENTITY_TYPES,
    RELATION_TYPES,
    PackError,
    PackSchemaError,
    build_pack,
    import_pack,
    load_pack,
    main,
    pack_items,
    parse_draft,
    validate_pack,
)
from baryon.tenants import TenantRegistry

REPO = Path(__file__).resolve().parents[3]
PACK = REPO / "examples" / "ontology" / "starter-pack.ko.json"
EVAL = Path(__file__).resolve().parents[1] / "eval" / "ontology_eval.py"


def sample() -> dict:
    return json.loads(PACK.read_text(encoding="utf-8"))


def eval_module():
    spec = importlib.util.spec_from_file_location("ontology_eval", EVAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sample_pack_is_a_valid_merge_v2_starter_pack() -> None:
    pack = load_pack(PACK)
    assert pack["schema_version"] == 2 and pack["language"] == "ko"
    assert 12 <= len(pack["entities"]) <= 18
    assert 4 <= len(pack["slots"]) <= 6
    assert {entity["type"] for entity in pack["entities"]} <= set(ENTITY_TYPES)
    names = {entity["name"] for entity in pack["entities"]}
    assert all(entity["definition"].strip() for entity in pack["entities"])
    assert all(item["type"] in RELATION_TYPES for item in pack["relations"])
    assert all(slot["relation"] in RELATION_TYPES for slot in pack["slots"])
    assert all(item["from"] in names and item["to"] in names for item in pack["relations"])
    assert all(slot["subject"] in names for slot in pack["slots"])
    assert all(slot["status"] == "open" and slot["question"].strip() for slot in pack["slots"])
    assert "related_to" not in {item["type"] for item in pack["relations"]}
    # Every closed-list relation type is used by the sample, so the diversity gate is meaningful.
    used = {item["type"] for item in pack["relations"]} | {slot["relation"] for slot in pack["slots"]}
    assert used == set(RELATION_TYPES)
    # The pack is a file any organization can import; it must not name a real organization.
    assert "organization_id" not in pack


def test_noise_labels_are_recognised() -> None:
    for noisy in ("분실하면", "신고해", "얼마", "누구에게", "법인카드에서", "재무팀에게"):
        assert is_entity_noise(noisy), noisy
    for noun in ("연차 휴가", "한도", "정보보안 교육 이수", "사내 포털", "법인카드"):
        assert not is_entity_noise(noun), noun


@pytest.mark.parametrize("mutate", [
    lambda pack: pack["entities"][0].update(type="object"),
    lambda pack: pack["entities"][0].update(name="분실하면"),
    lambda pack: pack["entities"][0].update(name="법인카드에서"),
    lambda pack: pack["entities"][0].update(related_to=True),          # unknown field
    lambda pack: pack["relations"][0].update(type="related_to"),
    lambda pack: pack["relations"][0].update(to="없는 개념"),
    lambda pack: pack["slots"][1].update(value_type="금액"),           # 기한 asks for a duration
    lambda pack: pack["slots"][1].update(subject="없는 개념"),
    lambda pack: pack["slots"].append(copy.deepcopy(pack["slots"][0])),  # duplicate slot
    lambda pack: pack["relations"].append({"from": "법인카드", "type": "한도", "to": "재무팀"}),  # same pair as a slot
])
def test_schema_rejects_anything_outside_merge_v2(mutate) -> None:
    pack = sample()
    mutate(pack)
    with pytest.raises(PackSchemaError):
        validate_pack(pack)


def test_a_missing_file_is_a_pack_error_not_a_schema_error() -> None:
    with pytest.raises(PackError):
        load_pack(REPO / "examples" / "ontology" / "does-not-exist.json")


def test_model_draft_is_rejected_whole_not_partially() -> None:
    good = {
        "entities": [
            {"name": "법인카드", "type": "자산·대상", "definition": "업무용 신용카드.", "synonyms": ["회사카드"]},
            {"name": "법인카드 사용규정", "type": "문서·규정", "definition": "사용 범위를 정한 규정.", "synonyms": []},
        ],
        "relations": [{"from": "법인카드", "type": "근거", "to": "법인카드 사용규정"}],
        "slots": [{"subject": "법인카드", "relation": "한도", "value_type": "금액", "question": "한도는 얼마인가요?"}],
    }

    async def answer(_messages):
        return json.dumps(good, ensure_ascii=False)

    built = asyncio.run(build_pack("가상 회사", answer, pack_id="starter_test", name="테스트", domain="테스트"))
    assert len(built["entities"]) == 2 and len(built["slots"]) == 1

    # One bad type must drop the whole answer, including the valid entity next to it.
    broken = copy.deepcopy(good)
    broken["entities"][1]["type"] = "object"
    broken.update(entities=broken["entities"])

    async def half_broken(_messages):
        return json.dumps(broken, ensure_ascii=False)

    with pytest.raises(PackSchemaError):
        asyncio.run(build_pack("가상 회사", half_broken, pack_id="starter_test", name="테스트", domain="테스트"))

    # An unknown field or the old related_to relation is a schema error too, never a silent drop.
    for change in ({"entities": [{"name": "법인카드", "type": "자산·대상", "related_to": "재무팀"}]},
                   {"entities": [{"name": "법인카드", "type": "자산·대상"}],
                    "relations": [{"from": "법인카드", "type": "related_to", "to": "재무팀"}]}):
        with pytest.raises(PackSchemaError):
            parse_draft(json.dumps(change, ensure_ascii=False))


def test_import_is_idempotent_and_stays_inside_the_organization(tmp_path: Path) -> None:
    store = LoopStore(tmp_path / "loop")
    first = import_pack(PACK, "demo", store=store)
    assert first["action"] == "added"
    assert (first["term_count"], first["relation_count"], first["slot_count"], first["pack_count"]) == (15, 12, 5, 1)
    assert first["open_slots"] == 5
    assert {slot["relation"] for slot in first["slots"]} == {"한도", "기한", "신고처", "승인자", "제출처"}

    second = import_pack(PACK, "demo", store=store)
    assert second["action"] == "unchanged" and second["changes"] == 0
    assert (second["term_count"], second["relation_count"], second["slot_count"], second["pack_count"]) == (15, 12, 5, 1)

    data = store.load("demo")
    card = data["terms"][term_id("법인카드")]
    assert card["type"] == "자산·대상" and card["definition"].startswith("회사가 발급해")
    assert card["synonyms"] == ["회사카드"]
    assert {claim["slot"] for claim in card["claims"]} == {"정의"}
    assert any(item["s_id"] == card["id"] and item["p"] == "한도" and item["status"] == "missing"
               for item in data["relations"])
    assert len(data["packs"]) == 1

    before = (tmp_path / "loop" / "demo.json").read_text(encoding="utf-8")
    import_pack(PACK, "other", store=store)
    assert (tmp_path / "loop" / "demo.json").read_text(encoding="utf-8") == before  # no cross-organization write
    assert (tmp_path / "loop" / "other.json").is_file()


def test_dry_run_reports_a_diff_and_writes_nothing(tmp_path: Path) -> None:
    store = LoopStore(tmp_path / "loop")
    result = import_pack(PACK, "demo", dry_run=True, store=store)
    assert result["dry_run"] and result["action"] == "added" and result["changes"] > 0
    assert not (tmp_path / "loop" / "demo.json").exists()

    import_pack(PACK, "demo", store=store)
    before = (tmp_path / "loop" / "demo.json").read_bytes()
    again = import_pack(PACK, "demo", dry_run=True, store=store)
    assert again["action"] == "unchanged" and again["changes"] == 0
    assert (tmp_path / "loop" / "demo.json").read_bytes() == before


def test_import_does_not_reopen_a_slot_the_organization_already_answered(tmp_path: Path) -> None:
    store = LoopStore(tmp_path / "loop")
    data = store.load("demo")
    merge(data, [Claim(subject="법인카드", value="월 300만원", predicate="한도", trust=OWNER,
                       source={"type": "document", "document_id": "policy-1", "chunk": 0})])
    store.save("demo", data)

    result = import_pack(PACK, "demo", store=store)
    card_slot = next(slot for slot in result["slots"] if slot["relation"] == "한도")
    assert card_slot["filled"] and card_slot["value"] == "월 300만원"
    assert result["open_slots"] == 4
    assert result["slot_count"] == 4  # the answered 한도 is not re-added as an open relation
    assert [(r["s"], r["p"]) for r in store.load("demo")["relations"] if r.get("status") == "missing"].count(
        ("법인카드", "한도")) == 0


def test_pooled_mode_requires_an_active_registered_tenant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TENANCY_MODE", "pooled")
    monkeypatch.setenv("TENANT_STORE", str(tmp_path / "tenants"))
    store = LoopStore(tmp_path / "loop")

    with pytest.raises(PackError):
        import_pack(PACK, "demo", store=store)  # not registered: no orphan ontology file
    assert not (tmp_path / "loop" / "demo.json").exists()

    TenantRegistry().create("demo", "데모 주식회사")
    assert import_pack(PACK, "demo", store=store)["action"] == "added"

    TenantRegistry().update("demo", status="suspended")
    with pytest.raises(PackError):
        import_pack(PACK, "demo", store=store)


def test_single_mode_refuses_path_escapes(tmp_path: Path) -> None:
    store = LoopStore(tmp_path / "loop")
    for organization in ("", "  ", "../etc", "a/b"):
        with pytest.raises(PackError):
            import_pack(PACK, organization, store=store)


def test_eval_gate_on_the_starter_pack_is_pinned() -> None:
    module = eval_module()
    assert module.STARTER_THRESHOLDS == {
        "noise_entities": 0,
        "related_to_ratio": 0.0,
        "slot_fill_rate": 1.0,
        "relation_types": 5,
    }
    score = module.score_pack(sample())
    assert score["noise_entities"] == 0
    assert score["related_to_ratio"] == 0.0
    assert score["slot_fill_rate"] == 1.0
    assert score["relation_types"] == len(RELATION_TYPES) == 7
    assert score["relation_type_diversity"] == 1.0
    assert score["entity_types"] == sorted(ENTITY_TYPES)
    assert module.starter_gate(score) == []

    broken = sample()
    broken["entities"][0]["name"] = "분실하면"
    broken["relations"].append({"from": "법인카드", "type": "related_to", "to": "재무팀"})
    broken["slots"][0]["value_type"] = "기간"
    failures = module.starter_gate(module.score_pack(broken))
    assert any(failure.startswith("noise_entities") for failure in failures)
    assert any(failure.startswith("related_to_ratio") for failure in failures)
    assert any(failure.startswith("slot_fill_rate") for failure in failures)


def test_pack_items_are_merge_v2_entities_relations_and_missing_slots() -> None:
    claims, entities, relations = pack_items(sample())
    assert len(entities) == 15 and len(claims) == 15
    assert sum(1 for relation in relations if relation.missing) == 5
    assert {relation.p for relation in relations if relation.missing} == {"한도", "기한", "신고처", "승인자", "제출처"}
    assert all(entity.source["type"] == "starter_pack" and entity.source["pack_id"] == "starter_demo_ko"
               for entity in entities)


def test_cli_validate_and_dry_run_read_like_a_diff(tmp_path: Path, capsys) -> None:
    assert main(["validate", "--pack", str(PACK)]) == 0
    assert "OK · 데모상사" in capsys.readouterr().out

    assert main(["import", "--pack", str(PACK), "--org", "demo", "--dry-run", "--store", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "dry-run · 데모상사" in output
    assert "새로 추가" in output
    assert "법인카드 —한도→ ?금액" in output
    assert "dry-run: 아무것도 쓰지 않았습니다" in output
    assert not (tmp_path / "loop" / "demo.json").exists()

    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 2}', encoding="utf-8")
    assert main(["validate", "--pack", str(bad)]) == 2
    assert "error:" in capsys.readouterr().out
