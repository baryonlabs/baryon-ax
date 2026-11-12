"""Answer feedback endpoint and store."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("DOCUMENT_STORE", str(Path(tempfile.mkdtemp(prefix="baryon-ax-fb-")) / "documents"))
os.environ.setdefault("AUDIT_LOG", str(Path(tempfile.mkdtemp(prefix="baryon-ax-fb-audit-")) / "events.jsonl"))

import pytest
from fastapi.testclient import TestClient

from baryon.feedback import FeedbackStore
from baryon.main import app

client = TestClient(app)


def headers(user: str = "fb-user", organization: str = "org-fb", roles: str = "member") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}


def test_store_keeps_rating_and_summary(tmp_path) -> None:
    store = FeedbackStore(tmp_path / "feedback.jsonl")
    store.add(organization_id="o", user_id="u", trace_id="t1", rating=1, citations=2)
    store.add(organization_id="o", user_id="u", trace_id="t2", rating=-1, reason="wrong number")
    assert store.summary("o") == {"total": 2, "helpful": 1, "not_helpful": 1}
    assert store.list("o")[0].trace_id == "t2"

    with pytest.raises(ValueError):
        store.add(organization_id="o", user_id="u", trace_id="t3", rating=0)


def test_feedback_endpoint_and_admin_list() -> None:
    org = "org-fb-api"
    created = client.post(
        "/v1/feedback",
        json={"trace_id": "trace-1", "rating": 1, "question": "예산은?", "citations": 2, "model": "qwen2.5:7b"},
        headers=headers(organization=org),
    )
    assert created.status_code == 200, created.text
    assert created.json()["rating"] == 1

    denied = client.get("/v1/admin/feedback", headers=headers(organization=org))
    assert denied.status_code == 403

    listing = client.get("/v1/admin/feedback", headers=headers(organization=org, roles="admin")).json()
    assert listing["summary"] == {"total": 1, "helpful": 1, "not_helpful": 0}
    assert listing["data"][0]["trace_id"] == "trace-1"


def test_feedback_is_organization_scoped() -> None:
    client.post("/v1/feedback", json={"trace_id": "a", "rating": -1}, headers=headers(organization="org-fb-a"))
    b = client.get("/v1/admin/feedback", headers=headers(organization="org-fb-b", roles="admin")).json()
    assert b["data"] == []
