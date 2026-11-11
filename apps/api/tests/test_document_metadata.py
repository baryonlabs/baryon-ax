"""Knowledge library classification: categories, tags, filtering, permissions."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("DOCUMENT_STORE", str(Path(tempfile.mkdtemp(prefix="baryon-ax-meta-")) / "documents"))
os.environ.setdefault("AUDIT_LOG", str(Path(tempfile.mkdtemp(prefix="baryon-ax-meta-audit-")) / "events.jsonl"))

from fastapi.testclient import TestClient

from baryon.main import app

client = TestClient(app)


def headers(user: str = "meta-owner", organization: str = "org-meta", roles: str = "member") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}


def upload(organization: str, filename: str, content: str, category: str = "", tags: str = "", user: str = "meta-owner") -> dict:
    response = client.post(
        "/v1/documents",
        files={"file": (filename, content.encode(), "text/markdown")},
        data={"category": category, "tags": tags, "allowed_roles": "member,admin"},
        headers=headers(user=user, organization=organization),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_stores_category_and_normalized_tags() -> None:
    org = "org-meta-tags"
    uploaded = upload(org, "contract.md", "계약 갱신 내용", category="계약", tags="#2026, 예산 , 예산")
    assert uploaded["category"] == "계약"
    assert uploaded["tags"] == ["2026", "예산"]

    listing = client.get("/v1/documents", headers=headers(organization=org)).json()["data"]
    item = next(document for document in listing if document["id"] == uploaded["id"])
    assert item["category"] == "계약"
    assert item["tags"] == ["2026", "예산"]


def test_list_filters_by_category_and_tag() -> None:
    org = "org-meta-filter"
    a = upload(org, "a.md", "인사 문서", category="인사", tags="채용,2026")
    b = upload(org, "b.md", "예산 문서", category="예산", tags="2026")
    upload(org, "c.md", "기타 문서", category="기타", tags="참고")

    by_category = client.get("/v1/documents?category=인사", headers=headers(organization=org)).json()["data"]
    assert [item["id"] for item in by_category] == [a["id"]]

    by_tag = client.get("/v1/documents?tag=2026", headers=headers(organization=org)).json()["data"]
    assert {item["id"] for item in by_tag} == {a["id"], b["id"]}

    both = client.get("/v1/documents?category=인사&tag=2026", headers=headers(organization=org)).json()["data"]
    assert [item["id"] for item in both] == [a["id"]]


def test_owner_can_edit_classification_but_not_acl() -> None:
    org = "org-meta-owner"
    uploaded = upload(org, "owned.md", "소유자 문서", category="기존")

    patched = client.patch(
        f"/v1/documents/{uploaded['id']}",
        json={"category": "계약", "tags": ["갱신", "2027"]},
        headers=headers(organization=org),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["category"] == "계약"
    assert patched.json()["tags"] == ["갱신", "2027"]

    acl_attempt = client.patch(
        f"/v1/documents/{uploaded['id']}",
        json={"allowed_roles": ["admin"]},
        headers=headers(organization=org),
    )
    assert acl_attempt.status_code == 403


def test_non_owner_cannot_edit_classification() -> None:
    org = "org-meta-other"
    uploaded = upload(org, "private.md", "비공개 문서", category="인사")

    response = client.patch(
        f"/v1/documents/{uploaded['id']}",
        json={"category": "탈취"},
        headers=headers(user="meta-other", organization=org),
    )
    assert response.status_code == 403


def test_admin_can_edit_any_classification() -> None:
    org = "org-meta-admin"
    uploaded = upload(org, "team.md", "팀 문서", category="기타")
    response = client.patch(
        f"/v1/documents/{uploaded['id']}",
        json={"category": "연구", "tags": ["온톨로지"]},
        headers=headers(user="meta-admin", organization=org, roles="admin"),
    )
    assert response.status_code == 200
    assert response.json()["category"] == "연구"


def test_filters_are_organization_scoped_and_respect_acls() -> None:
    source = "org-meta-acl-a"
    other = "org-meta-acl-b"
    upload(source, "restricted.md", "제한 문서", category="비밀")
    upload(other, "restricted.md", "다른 조직 문서", category="비밀")

    listing = client.get("/v1/documents?category=비밀", headers=headers(organization=source)).json()["data"]
    assert len(listing) == 1
    assert all(item["category"] == "비밀" for item in listing)
