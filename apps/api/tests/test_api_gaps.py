"""Admin knowledge-gap review and ontology-pack generation over HTTP."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("DOCUMENT_STORE", str(Path(tempfile.mkdtemp(prefix="baryon-ax-gap-api-")) / "documents"))
os.environ.setdefault("AUDIT_LOG", str(Path(tempfile.mkdtemp(prefix="baryon-ax-gap-audit-")) / "events.jsonl"))

from fastapi.testclient import TestClient

from baryon.main import app

client = TestClient(app)


def headers(user: str = "gap-admin", organization: str = "org-gap", roles: str = "admin") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}


def ask(organization: str, question: str) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": question}], "stream": False, "use_retrieval": True},
        headers=headers(organization=organization),
    )
    assert response.status_code == 200, response.text


def test_gap_endpoints_require_admin() -> None:
    member = headers(roles="member")
    assert client.get("/v1/admin/gaps", headers=member).status_code == 403
    assert client.get("/v1/admin/gaps/clusters", headers=member).status_code == 403
    assert client.get("/v1/admin/ontology-packs", headers=member).status_code == 403
    assert client.post("/v1/admin/ontology-packs", json={"gap_ids": ["x"]}, headers=member).status_code == 403


def test_ungrounded_chat_becomes_ontology_pack() -> None:
    org = "org-gap-flow"
    ask(org, "2027년 계약 갱신 일정은?")

    listing = client.get("/v1/admin/gaps?status=open", headers=headers(organization=org))
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["summary"]["open"] >= 1
    gap = next(item for item in payload["data"] if "계약" in item["question"])
    assert "no_citations" in gap["reasons"]
    assert gap["occurrences"] == 1

    clusters = client.get("/v1/admin/gaps/clusters", headers=headers(organization=org)).json()["data"]
    assert any(gap["id"] in cluster["gap_ids"] for cluster in clusters)
    cluster = next(item for item in clusters if gap["id"] in item["gap_ids"])
    assert cluster["suggested_pack_name"].endswith("온톨로지")

    created = client.post(
        "/v1/admin/ontology-packs",
        json={"gap_ids": [gap["id"]], "name": "계약 갱신 온톨로지"},
        headers=headers(organization=org),
    )
    assert created.status_code == 200, created.text
    pack = created.json()
    assert pack["id"].startswith("ont_")
    assert pack["name"] == "계약 갱신 온톨로지"
    assert pack["status"] == "draft"
    assert pack["entities"]
    assert pack["required_sources"]

    packs = client.get("/v1/admin/ontology-packs", headers=headers(organization=org)).json()["data"]
    assert any(item["id"] == pack["id"] for item in packs)
    assert client.get(f"/v1/admin/ontology-packs/{pack['id']}", headers=headers(organization=org)).status_code == 200

    converted = client.get("/v1/admin/gaps?status=all", headers=headers(organization=org)).json()["data"]
    assert next(item for item in converted if item["id"] == gap["id"])["status"] == "converted"


def test_streaming_chat_also_records_gap() -> None:
    org = "org-gap-stream"
    with client.stream(
        "POST", "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "스트리밍 계약 담당자는?"}], "stream": True, "use_retrieval": True},
        headers=headers(organization=org),
    ) as response:
        assert response.status_code == 200
        for _ in response.iter_lines():
            pass

    gaps = client.get("/v1/admin/gaps", headers=headers(organization=org)).json()["data"]
    assert any("스트리밍" in item["question"] for item in gaps)


def test_pack_verification_closes_the_loop() -> None:
    org = "org-gap-verify"
    ask(org, "2027년 계약 갱신 일정은?")

    gaps = client.get("/v1/admin/gaps", headers=headers(organization=org)).json()["data"]
    gap = next(item for item in gaps if "계약" in item["question"])
    pack = client.post(
        "/v1/admin/ontology-packs",
        json={"gap_ids": [gap["id"]]},
        headers=headers(organization=org),
    ).json()

    approved = client.post(f"/v1/admin/ontology-packs/{pack['id']}/approve", headers=headers(organization=org))
    assert approved.status_code == 200
    assert approved.json()["status"] == "reviewed"

    # Before the source is available the pack stays reviewed, with the gap open.
    first = client.post(f"/v1/admin/ontology-packs/{pack['id']}/verify", headers=headers(organization=org))
    assert first.status_code == 200
    assert first.json()["status"] == "reviewed"
    assert first.json()["verification"]["coverage"] == 0.0

    # A new source that covers the question triggers automatic re-verification.
    upload = client.post(
        "/v1/documents",
        files={"file": ("contract-2027.md", "2027년 계약 갱신 일정은 3월 15일입니다.".encode(), "text/markdown")},
        headers=headers(organization=org),
    )
    assert upload.status_code == 200, upload.text

    verified = client.get(f"/v1/admin/ontology-packs/{pack['id']}", headers=headers(organization=org)).json()
    assert verified["status"] == "active"
    assert verified["verification"]["coverage"] == 1.0

    resolved = client.get("/v1/admin/gaps?status=all", headers=headers(organization=org)).json()["data"]
    assert next(item for item in resolved if item["id"] == gap["id"])["status"] == "resolved"


def test_open_gap_resolves_when_source_arrives() -> None:
    org = "org-gap-autoresolve"
    ask(org, "2028년 감사 일정은?")

    gaps = client.get("/v1/admin/gaps", headers=headers(organization=org)).json()["data"]
    gap = next(item for item in gaps if "감사" in item["question"])

    upload = client.post(
        "/v1/documents",
        files={"file": ("audit-2028.md", "2028년 감사 일정은 5월 10일입니다.".encode(), "text/markdown")},
        headers=headers(organization=org),
    )
    assert upload.status_code == 200

    resolved = client.get("/v1/admin/gaps?status=all", headers=headers(organization=org)).json()["data"]
    assert next(item for item in resolved if item["id"] == gap["id"])["status"] == "resolved"

    events = client.get("/v1/audit-events?limit=100", headers=headers(organization=org)).json()["data"]
    assert any(event["type"] == "knowledge.gap_resolved" and event.get("gap_id") == gap["id"] for event in events)


def test_verify_rejects_pack_without_questions() -> None:
    org = "org-gap-noquestions"
    assert client.post("/v1/admin/ontology-packs/missing/verify", headers=headers(organization=org)).status_code == 404


def test_gap_can_be_dismissed_and_leaves_open_clusters() -> None:
    org = "org-gap-dismiss"
    ask(org, "존재하지 않는 설비 가동률 보고서는?")

    gaps = client.get("/v1/admin/gaps", headers=headers(organization=org)).json()["data"]
    gap = gaps[0]
    dismissed = client.post(f"/v1/admin/gaps/{gap['id']}/dismiss", headers=headers(organization=org))
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"

    open_gaps = client.get("/v1/admin/gaps", headers=headers(organization=org)).json()["data"]
    assert all(item["id"] != gap["id"] for item in open_gaps)

    clusters = client.get("/v1/admin/gaps/clusters", headers=headers(organization=org)).json()["data"]
    assert all(gap["id"] not in cluster["gap_ids"] for cluster in clusters)


def test_gap_detection_is_organization_scoped() -> None:
    ask("org-gap-scope-a", "A 조직 전용 질문은?")
    ask("org-gap-scope-b", "B 조직 전용 질문은?")

    a = client.get("/v1/admin/gaps?status=all", headers=headers(organization="org-gap-scope-a")).json()["data"]
    b = client.get("/v1/admin/gaps?status=all", headers=headers(organization="org-gap-scope-b")).json()["data"]
    assert a and b
    assert all(item["organization_id"] == "org-gap-scope-a" for item in a)
    assert all(item["organization_id"] == "org-gap-scope-b" for item in b)
    assert {item["question"] for item in a}.isdisjoint({item["question"] for item in b})
