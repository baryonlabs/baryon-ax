"""Ontology merge v2 (S4): one store, candidates and same-as, claim-level same / refine / contest, source weight,
stub ends, message-level provenance, one change set applied at once, idempotent, and the migration."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from baryon import loop, ontology_migrate
from baryon import ontology_merge as om
from baryon.gaps import PackStore, build_pack
from baryon.loop import LoopStore
from baryon.loop_api import LoopDeps, build_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

EVAL = Path(__file__).resolve().parents[1] / "eval"


# --------------------------------------------------------------------------- values and names


@pytest.mark.parametrize(("old", "new", "expected"), [
    ("30일", "30일로 하죠", om.SAME),
    ("2주", "14일", om.SAME),
    ("1일 15만원", "하루 15만 원", om.SAME),
    ("ryu", "ryu 님(11월 순번)", om.REFINE),
    ("30일", "30일, 원자재 급등 시 15일", om.REFINE),
    ("입사 1년 뒤 15일", "입사 1년 뒤 20일", om.CONTEST),
    ("song 님", "ryu 님", om.CONTEST),
    ("아직 안 정했어", "50만원 이하는 팀장", om.CONTEST),
    ("AI 비서 앱", "아이디어 정리 도구", om.CONTEST),
])
def test_values_are_compared_by_what_they_claim(old: str, new: str, expected: str) -> None:
    assert om.relate(old, new) == expected


def test_labels_are_normalized_and_only_near_names_are_candidates() -> None:
    assert om.clean_label("는 고객 지원") == "고객 지원" and om.clean_label("다음 주 워크숍 장소") == "워크숍 장소"
    assert not om.usable_label("47") and not om.usable_label("2건") and not om.usable_label("담당") and om.usable_label("QA")
    assert om.rule_same_as("견적", "견적서") and om.rule_same_as("납기", "납기일") and om.rule_same_as("연차휴가", "연차 휴가")
    assert not om.rule_same_as("환불", "환불 승인") and not om.rule_same_as("승인", "승인자")
    terms = {"t_견적서": {"id": "t_견적서", "label": "견적서", "synonyms": []}, "t_환불": {"id": "t_환불", "label": "환불", "synonyms": []}}
    assert om.candidates(terms, "견적") == ["t_견적서"] and om.exact_match(terms, "견 적 서") == "t_견적서"


def test_the_model_only_picks_among_the_candidates_and_falls_back_to_rules() -> None:
    async def model(messages):
        return json.dumps({"same": [{"new": "quote", "existing": "견적서"}, {"new": "quote", "existing": "만든 이름"}]})

    async def broken(messages):
        raise RuntimeError("down")

    picked = asyncio.run(om.judge_same_as({"quote": ["견적서"], "견적": ["견적서"]}, model))
    assert picked == {"quote": "견적서", "견적": None}  # the model said nothing about 견적: its answer stands
    assert asyncio.run(om.judge_same_as({"견적": ["견적서"]}, broken)) == {"견적": "견적서"}  # rules


# --------------------------------------------------------------------------- thread readings


def reading() -> dict:
    """An S3 reading (thread_understanding.finalize shape), invented names."""
    ts = ["1700600000.000100", "1700600010.000200", "1700600020.000300", "1700600030.000400"]
    return {
        "version": 1, "method": "rules", "channel": "CTEST000001", "thread_ts": ts[0],
        "entities": [{"name": "helper-bot", "type": "bot", "aliases": []}, {"name": "park", "type": "person", "aliases": []},
                     {"name": "deploy-bot", "type": "bot", "aliases": ["디플로이"]}],
        "decisions": [{"statement": "정산 기한은 2주가 맞아", "decided_by": ["nam"], "kind": "explicit", "confirmed": True, "by_owner": True,
                       "evidence_ts": [ts[3]], "quote": "정산 기한은 2주가 맞아", "agreement_signals": []}],
        "claims": [
            {"subject": "deploy-bot", "aliases": [], "predicate": "역할", "value": "배포 실행", "by": "helper-bot", "author_kind": "other_bot",
             "trust": "needs_verification", "evidence_ts": [ts[1]], "quote": "deploy-bot은 배포 실행 담당", "contest": None},
            {"subject": "출장비 정산 기한", "aliases": [], "predicate": "", "value": "1주", "by": "helper-bot", "author_kind": "other_bot",
             "trust": "needs_verification", "evidence_ts": [ts[2]], "quote": "출장비 정산 기한은 1주", "contest": None},
            {"subject": "승인 경로", "aliases": [], "predicate": "", "value": "홈 화면에서만", "by": "a-bot", "author_kind": "other_bot",
             "trust": "needs_verification", "evidence_ts": [ts[1]], "quote": "승인 경로는 홈 화면에서만", "contest": 0},
            {"subject": "승인 경로", "aliases": [], "predicate": "", "value": "메신저 DM", "by": "b-bot", "author_kind": "other_bot",
             "trust": "needs_verification", "evidence_ts": [ts[2]], "quote": "승인 경로는 메신저 DM", "contest": 0},
        ],
        "contests": [{"subject": "승인 경로", "predicate": "", "evidence_ts": [ts[1], ts[2]]}],
        "relations": [{"s": "park", "p": "담당", "o": "견적서 양식 수정", "evidence_ts": [ts[3]], "quote": "park"}],
    }


def test_a_thread_reading_merges_with_weights_stubs_provenance_and_no_second_change() -> None:
    data = om.upgrade({})
    changes = om.merge_understanding(data, reading())
    labels = {item["label"]: item for item in changes.added}
    # another bot's claim alone is added but only as needs_verification; its entity type is reused
    bot = data["terms"][labels["deploy-bot"]["id"]]
    assert bot["needs_verification"] and bot["type"] == "시스템" and bot["trust"] == "needs_verification"
    # the owner's later word settles the bot's other value: no second term, no [쟁점]
    assert "출장비 정산 기한" not in labels and labels["정산 기한"]["definition"] == "2주"
    settled = data["terms"][labels["정산 기한"]["id"]]
    assert {c["status"] for c in settled["claims"]} == {"current", "superseded"}
    # two bots that disagree: one [쟁점] with both readings
    assert [c["label"] for c in changes.contested] == ["승인 경로"]
    contested = data["terms"][changes.contested[0]["id"]]
    assert loop.is_contested(contested) and len(contested["interpretations"]) == 2
    # relation ends that were not terms are kept as stubs, typed when the thread knows them
    assert {s["label"]: s["type"] for s in changes.stubs} == {"park": "사람·조직", "견적서 양식 수정": "개념"}
    assert changes.relations[0]["s"] == "park" and changes.relations[0]["o_id"]
    # provenance at message level
    source = bot["sources"][0]
    assert source["channel"] == "CTEST000001" and source["ts"] == "1700600010.000200"
    assert source["permalink"] == "/archives/CTEST000001/p1700600010000200?thread_ts=1700600000.000100"
    # the same thread again: nothing
    assert om.merge_understanding(data, reading()).count() == 0


def test_source_weight_decides_a_disagreement_and_only_equals_contest() -> None:
    def claim(value: str, trust: str, ts: str) -> om.Claim:
        return om.Claim(subject="정산 기한", value=value, trust=trust, source=om.slack_source("C1", ts, ts, value))

    data = om.upgrade({})
    om.merge(data, [claim("2주", om.OWNER, "1.1")])
    weaker = om.merge(data, [claim("1주", om.NEEDS_VERIFICATION, "2.1")])
    assert not weaker.contested and weaker.refined[0]["kind"] == "needs_verification"
    stronger = om.upgrade({})
    om.merge(stronger, [claim("1주", om.NEEDS_VERIFICATION, "1.1")])
    override = om.merge(stronger, [claim("2주", om.OWNER, "2.1")])
    assert override.refined[0]["kind"] == "override" and stronger["terms"]["t_정산-기한"]["definition"] == "2주"
    equals = om.upgrade({})
    om.merge(equals, [claim("1주", om.HUMAN, "1.1")])
    assert om.merge(equals, [claim("2주", om.HUMAN, "2.1")]).contested


def test_a_changeset_is_applied_whole_or_not_at_all(tmp_path) -> None:
    store = LoopStore(tmp_path)
    store.update("org", lambda data: om.merge_understanding(data, reading()))
    before = (tmp_path / "org.json").read_text()

    def failing(data: dict) -> None:
        om.merge_understanding(data, {**reading(), "channel": "COTHER00001"})
        raise RuntimeError("stop before saving")

    with pytest.raises(RuntimeError):
        store.update("org", failing)
    assert (tmp_path / "org.json").read_text() == before


def test_documents_use_the_same_merger_and_keep_relations_with_stub_ends() -> None:
    data = om.upgrade({})
    source = {"type": "document", "document_id": "d1", "chunk": 1, "quote": "q"}
    om.merge(data, [om.document_claim("견적서", "고객에게 보내는 가격 제안 문서", source, True)])
    second = om.merge(data, [om.document_claim("견적서", "고객에게 보내는 가격 제안 문서로 유효기간을 적는다", {**source, "document_id": "d2"}, True)],
                      [om.Relation(s="견적서", p="포함한다", o="유효기간", source=source)])
    assert not second.contested and second.refined and second.stubs[0]["label"] == "유효기간"
    assert data["terms"]["t_견적서"]["status"] == om.VERIFIED and data["relations"][0]["o_id"] == second.stubs[0]["id"]


# --------------------------------------------------------------------------- API


def api(tmp_path) -> TestClient:
    principal = lambda user, org, roles: SimpleNamespace(user_id=user or "u", organization_id=org or "o",
                                                         roles=set((roles or "member").split(",")))

    async def no_model(messages):
        raise RuntimeError("off")

    deps = LoopDeps(principal=principal, acting_service=lambda: None, documents=SimpleNamespace(), audit=SimpleNamespace(emit=lambda *a, **k: None),
                    complete=no_model, model_ready=lambda: False, publish=None)
    app = FastAPI()
    app.include_router(build_router(deps, LoopStore(tmp_path)))
    return TestClient(app)


def test_the_merge_endpoint_is_for_agents_dry_runs_and_is_idempotent(tmp_path) -> None:
    client = api(tmp_path)
    agent = {"X-User-Id": "slack-bot", "X-Organization-Id": "org-m", "X-Roles": "agent"}
    member = {**agent, "X-User-Id": "kim", "X-Roles": "member"}
    assert client.post("/v1/loop/threads/merge", headers=member, json={"understanding": reading()}).status_code == 403
    dry = client.post("/v1/loop/threads/merge", headers=agent, json={"understanding": reading(), "dry_run": True}).json()
    assert dry["changes"] > 0 and not (tmp_path / "org-m.json").exists()
    first = client.post("/v1/loop/threads/merge", headers=agent, json={"understanding": reading()}).json()
    assert first["counts"] == dry["counts"] and set(first) >= {"added", "merged", "refined", "contested", "relations", "stubs"}
    assert client.post("/v1/loop/threads/merge", headers=agent, json={"understanding": reading()}).json()["changes"] == 0
    ontology = client.get("/v1/loop/ontology", headers=agent).json()
    assert {t["label"] for t in ontology["terms"]} >= {"deploy-bot", "정산 기한", "승인 경로", "park"}


# --------------------------------------------------------------------------- one store for gap packs


def gap(identifier: str, question: str):
    from baryon.gaps import KnowledgeGap

    return KnowledgeGap(id=identifier, organization_id="org_p", user_id="u", question=question, answer_excerpt="",
                        reasons=["no_results"], citations=0, terms=[])


def test_gap_packs_live_in_the_same_ontology_as_the_loop(tmp_path) -> None:
    store = LoopStore(tmp_path / "loop")
    packs = PackStore(store=store)
    store.update("org_p", lambda data: om.merge(data, [om.Claim(subject="법인카드", value="회사 명의 결제 카드", trust=om.DOCUMENT,
                                                                source={"type": "document", "document_id": "d", "chunk": 1})]))
    pack = build_pack(organization_id="org_p", name="카드", gaps=[gap("g1", "법인카드 한도는 얼마야?")])
    packs.save(pack)
    data = store.load("org_p")
    card = data["terms"]["t_법인카드"]
    assert any(s.get("type") == "knowledge_gap" for s in card["sources"]) and card["definition"] == "회사 명의 결제 카드"
    assert any(r.get("status") == "missing" and r["s_id"] == "t_법인카드" for r in data["relations"])
    assert packs.get("org_p", pack["id"])["name"] == "카드" and packs.list("org_p")[0]["id"] == pack["id"]
    assert packs.purge_organization("org_p") == 1 and packs.list("org_p") == [] and "t_법인카드" in store.load("org_p")["terms"]


# --------------------------------------------------------------------------- migration


def v1_store(root: Path) -> None:
    (root / "loop").mkdir(parents=True)
    (root / "loop" / "org_a.json").write_text(json.dumps({"terms": {"t_연차": {
        "id": "t_연차", "label": "연차", "definition": "입사 1년 뒤 15일", "kind": "term", "status": "verified",
        "sources": [{"type": "document", "document_id": "d1", "chunk": 1}]}}, "relations": [], "actions": {}, "questions": {},
        "rounds": [], "seen_documents": {}}, ensure_ascii=False))
    for pack_id, organization, name in (("ont_aaaa", "org_a", "연차 휴가"), ("ont_bbbb", "org_b", "법인카드")):
        (root / f"{pack_id}.json").write_text(json.dumps({
            "id": pack_id, "organization_id": organization, "name": name, "status": "draft",
            "entities": [{"name": name, "type": "자산·대상", "synonyms": []}], "relations": [{"from": name, "type": "한도", "to": "?금액"}],
            "slots": [], "acceptance_questions": [f"{name} 한도는?"]}, ensure_ascii=False))


def test_the_migration_backs_up_first_is_idempotent_and_can_be_restored(tmp_path) -> None:
    v1_store(tmp_path)
    original = (tmp_path / "loop" / "org_a.json").read_text()
    assert ontology_migrate.migrate(tmp_path, dry_run=True)["status"] == "dry_run"
    assert not (tmp_path / "backups").exists() and (tmp_path / "ont_aaaa.json").exists()

    report = ontology_migrate.migrate(tmp_path, stamp="20261128T000000Z")
    assert report["status"] == "migrated" and report["packs"] == 2
    with tarfile.open(report["backup"]) as archive:
        assert set(archive.getnames()) == {"loop/org_a.json", "packs/ont_aaaa.json", "packs/ont_bbbb.json"}
    assert not list(tmp_path.glob("ont_*.json")) and (tmp_path / "migrated" / "ont_aaaa.json").exists()
    org_a = LoopStore(tmp_path / "loop").load("org_a")
    assert org_a["schema_version"] == 2 and org_a["terms"]["t_연차"]["claims"][0]["value"] == "입사 1년 뒤 15일"
    assert "ont_aaaa" in org_a["packs"] and org_a["terms"]["t_연차-휴가"]["type"] == "자산·대상"  # a word more: another term
    assert PackStore(tmp_path).get("org_b", "ont_bbbb")["name"] == "법인카드"

    snapshot = {path.name: path.read_text() for path in (tmp_path / "loop").glob("*.json")}
    assert ontology_migrate.migrate(tmp_path)["status"] == "nothing_to_do"
    assert {path.name: path.read_text() for path in (tmp_path / "loop").glob("*.json")} == snapshot

    ontology_migrate.restore(tmp_path, report["backup"])
    assert (tmp_path / "loop" / "org_a.json").read_text() == original and (tmp_path / "ont_aaaa.json").exists()


def test_an_interrupted_migration_can_run_again(tmp_path) -> None:
    v1_store(tmp_path)
    store = LoopStore(tmp_path / "loop")
    pack = json.loads((tmp_path / "ont_aaaa.json").read_text())
    store.update("org_a", lambda data: om.merge_pack(data, pack))  # merged, but the file was never moved
    before = len(store.load("org_a")["terms"])
    report = ontology_migrate.migrate(tmp_path)
    assert report["status"] == "migrated" and report["organizations"]["org_a"]["changes"] == 0
    assert len(store.load("org_a")["terms"]) == before


# --------------------------------------------------------------------------- golden pairs


def merge_eval():
    spec = importlib.util.spec_from_file_location("merge_eval", EVAL / "merge_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("gold", ["ontology_merge_gold.json", "ontology_merge_holdout.json"])
def test_the_merger_meets_the_bar_on_the_golden_pairs(gold: str) -> None:
    result, _ = merge_eval().evaluate(None, EVAL / gold)
    assert result["macro_precision"] >= 0.85 and result["macro_recall"] >= 0.85
    assert result["rerun_changes"] == 0 and result["false_contests"] == 0
