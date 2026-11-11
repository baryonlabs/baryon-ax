"""Knowledge-gap detection, clustering, and ontology-pack generation."""

import json
import unicodedata

from baryon.gaps import (
    GapStore,
    KnowledgeGap,
    PackStore,
    build_pack,
    cluster_gaps,
    detect_reasons,
    salient_terms,
    suggest_sources,
    verify_pack,
)


def gap(gap_id: str, question: str, reasons: list[str] | None = None) -> KnowledgeGap:
    return KnowledgeGap(
        id=gap_id, organization_id="org_gaps", user_id="user_1", question=question,
        answer_excerpt="", reasons=reasons or ["no_citations"], citations=0,
        terms=salient_terms(question),
    )


def test_detect_reasons_covers_refusal_and_missing_grounding() -> None:
    assert detect_reasons("예산은?", "제공된 문서에서 찾을 수 없습니다.", 0, True) == ["no_citations", "refusal"]
    assert detect_reasons("예산은?", "아마 110 정도일 것 같습니다.", 2, True) == ["vague"]
    assert detect_reasons("예산은?", "인건비는 110입니다.", 2, True) == []
    assert detect_reasons("예산은?", "", 0, True) == ["empty_answer", "no_citations"]
    assert detect_reasons("budget?", "I could not find that in the documents.", 0, False) == ["refusal"]


def test_salient_terms_strips_korean_particles_and_noise() -> None:
    assert salient_terms("2026년 인건비 예산은 얼마인가요?") == ["인건비", "예산"]
    assert salient_terms("What is the vendor invoice amount?") == ["vendor", "invoice", "amount"]


def test_gap_store_deduplicates_and_counts_occurrences(tmp_path) -> None:
    store = GapStore(tmp_path / "gaps.jsonl")
    first = store.observe(organization_id="o", user_id="u", question="계약 갱신 일정은?", answer="모르겠습니다", citations=0)
    second = store.observe(organization_id="o", user_id="u", question="계약 갱신 일정은?", answer="모르겠습니다", citations=0)

    assert first is not None and second is not None
    assert first.id == second.id
    assert second.occurrences == 2
    assert len(store.list("o")) == 1
    assert store.summary("o")["occurrences"] == 2


def test_gap_store_dismiss_and_convert(tmp_path) -> None:
    store = GapStore(tmp_path / "gaps.jsonl")
    first = store.observe(organization_id="o", user_id="u", question="인건비 예산은?", answer="모르겠습니다", citations=0)
    assert first is not None

    assert store.dismiss("o", first.id).status == "dismissed"
    assert store.list("o") == []
    assert store.list("o", "dismissed")[0].id == first.id

    store.mark_converted("o", [first.id], "ont_1")
    assert store.list("o", "converted")[0].pack_id == "ont_1"


def test_gap_store_normalizes_nfd_questions(tmp_path) -> None:
    store = GapStore(tmp_path / "gaps.jsonl")
    nfd = unicodedata.normalize("NFD", "인건비 예산은?")
    first = store.observe(organization_id="o", user_id="u", question=nfd, answer="모르겠습니다", citations=0)
    second = store.observe(organization_id="o", user_id="u", question="인건비 예산은?", answer="모르겠습니다", citations=0)

    assert first is not None and second is not None
    assert first.id == second.id
    assert second.question == "인건비 예산은?"
    assert second.occurrences == 2
    assert all(unicodedata.normalize("NFC", term) == term for term in second.terms)


def test_gap_store_is_organization_scoped(tmp_path) -> None:
    store = GapStore(tmp_path / "gaps.jsonl")
    store.observe(organization_id="a", user_id="u", question="질문", answer="모르겠습니다", citations=0)
    assert store.list("a")
    assert store.list("b") == []


def test_cluster_groups_related_questions_and_separates_topics() -> None:
    clusters = cluster_gaps([
        gap("1", "인건비 예산은 얼마인가요?"),
        gap("2", "예산 편성 기준은?"),
        gap("3", "계약 갱신 일정은?"),
    ])
    sizes = {cluster.label: cluster.size for cluster in clusters}
    assert sizes.get("예산") == 2
    assert any(cluster.size == 1 for cluster in clusters)
    budget = next(cluster for cluster in clusters if cluster.label == "예산")
    assert budget.suggested_pack_name == "예산 온톨로지"
    assert budget.required_sources[0]["kind"] == "system"


def test_suggest_sources_maps_domains() -> None:
    assert "HR" in suggest_sources(["급여", "근태"])[0]["hint"]
    assert "법규" in suggest_sources(["조례", "규정"])[0]["hint"]
    assert suggest_sources(["somethingelse"])[0]["hint"].startswith("내부 문서")
    # Ambiguous terms return both domains, strongest match first.
    both = [item["hint"] for item in suggest_sources(["인건비", "예산"])]
    assert both[0].startswith("HR") and any("ERP" in hint for hint in both)


def test_build_pack_is_reviewable_draft() -> None:
    gaps = [gap("1", "인건비 예산은?"), gap("2", "예산 편성 기준은?")]
    cluster = cluster_gaps(gaps)[0]
    pack = build_pack(organization_id="org_gaps", name="", gaps=gaps, cluster=cluster)

    assert pack["schema_version"] == 1
    assert pack["status"] == "draft"
    assert pack["organization_id"] == "org_gaps"
    assert pack["name"] == "예산 온톨로지"
    assert {entity["name"] for entity in pack["entities"]} >= {"인건비", "예산"}
    assert all(relation["type"] == "related_to" for relation in pack["relations"])
    assert pack["source_gap_ids"] == ["1", "2"]
    assert pack["acceptance_questions"] == ["인건비 예산은?", "예산 편성 기준은?"]


def test_verify_pack_activates_when_coverage_meets_threshold() -> None:
    pack = {
        "id": "ont_1", "status": "reviewed", "source_gap_ids": ["1"],
        "acceptance_questions": ["인건비 예산은?", "계약 갱신 일정은?"],
    }

    def search(question: str):
        return [{"title": "budget.md"}] if "예산" in question else []

    verified = verify_pack(pack, search, threshold=0.5)
    assert verified["verification"]["coverage"] == 0.5
    assert verified["verification"]["passed"] == 1
    assert verified["status"] == "active"
    assert verified["verification"]["results"][1]["missing_terms"]


def test_verify_pack_stays_reviewed_below_threshold() -> None:
    pack = {
        "id": "ont_2", "status": "reviewed", "source_gap_ids": [],
        "acceptance_questions": ["인건비 예산은?", "계약 갱신 일정은?"],
    }
    verified = verify_pack(pack, lambda question: [{"title": "doc"}] if "예산" in question else [], threshold=0.8)
    assert verified["verification"]["coverage"] == 0.5
    assert verified["status"] == "reviewed"


def test_verify_pack_requires_questions() -> None:
    import pytest

    with pytest.raises(ValueError):
        verify_pack({"id": "ont_3", "acceptance_questions": []}, lambda question: [])


def test_pack_store_persists_json_per_organization(tmp_path) -> None:
    store = PackStore(tmp_path)
    pack = build_pack(organization_id="org_gaps", name="테스트 팩", gaps=[gap("1", "인건비 예산은?")])
    store.save(pack)

    loaded = store.get("org_gaps", pack["id"])
    assert loaded is not None and loaded["name"] == "테스트 팩"
    assert store.get("other_org", pack["id"]) is None
    assert json.loads((tmp_path / f"{pack['id']}.json").read_text())["id"] == pack["id"]
    assert len(store.list("org_gaps")) == 1
