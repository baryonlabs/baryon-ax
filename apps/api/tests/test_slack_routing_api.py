"""What the Slack bot's router needs from the API: answers that are not gaps, and actions taken back on edit/delete."""

from __future__ import annotations

from fastapi.testclient import TestClient

from baryon.main import app

client = TestClient(app)


def headers(user: str = "slack:U1", organization: str = "org-route", roles: str = "member") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}


def test_record_gap_false_answers_without_recording_a_gap() -> None:
    org = "org-route-gap"
    question = {"messages": [{"role": "user", "content": "orca-bot은 요즘 무슨 일을 하고 있어?"}], "stream": False}
    body = client.post("/v1/chat/completions", headers=headers(organization=org), json={**question, "record_gap": False}).json()
    assert body["grounding"]["answered"] is False  # the documents still did not answer it
    assert body["knowledge_gap"] is None
    gaps = client.get("/v1/admin/gaps?status=all", headers=headers("boss", org, "admin")).json()["data"]
    assert gaps == []
    recorded = client.post("/v1/chat/completions", headers=headers(organization=org), json=question).json()
    assert recorded["knowledge_gap"]  # the default still records it


def test_an_edited_or_deleted_message_takes_back_its_actions() -> None:
    org = "org-route-retract"
    me, bot = headers(organization=org), headers("slack-bot", org, "agent")
    first = client.post("/v1/loop/actions", headers=me, json={
        "text": "워크숍 장소는 3층 대회의실로 한다", "source": "slack", "source_ref": "slack:C1:1.0", "message_ref": "slack:C1:1.0"}).json()
    other = client.post("/v1/loop/actions", headers=me, json={
        "text": "예산은 1인당 2만원으로 한다", "source": "slack", "source_ref": "slack:C1:1.0", "message_ref": "slack:C1:2.0"}).json()
    assert first["message_ref"] == "slack:C1:1.0"
    client.post("/v1/loop/rounds", headers=bot, json={"documents": 0})
    assert {q["action_id"] for q in client.get("/v1/loop/questions", headers=me).json()["data"]} == {first["id"], other["id"]}

    # the edit still states one thing and no longer the other; the deletion takes back everything
    kept = client.post("/v1/loop/actions", headers=me, json={
        "text": "워크숍 장소는 5층 세미나실로 한다", "source": "slack", "message_ref": "slack:C1:1.0"}).json()
    edited = client.post("/v1/loop/actions/retract", headers=bot, json={
        "message_ref": "slack:C1:1.0", "keep_texts": ["워크숍장소는5층세미나실로한다"], "reason": "edited"}).json()
    assert edited == {"retracted": 1, "ids": [first["id"]]}
    deleted = client.post("/v1/loop/actions/retract", headers=bot, json={"message_ref": "slack:C1:2.0", "reason": "deleted"}).json()
    assert deleted["ids"] == [other["id"]]

    open_now = client.get("/v1/loop/questions", headers=me).json()["data"]
    assert all(q["action_id"] not in (first["id"], other["id"]) for q in open_now)  # their questions are withdrawn
    stats = client.get("/v1/loop/stats", headers=me).json()
    assert stats["actions"]["new"] == 1  # the edited text, waiting for the next round
    assert client.post("/v1/loop/actions/retract", headers=bot, json={"message_ref": "slack:C1:2.0"}).json()["retracted"] == 0
    assert kept["status"] == "new"


def test_a_person_retracts_only_their_own_actions() -> None:
    org = "org-route-own"
    mine = client.post("/v1/loop/actions", headers=headers("slack:U1", org), json={"text": "견적 유효기간은 30일", "message_ref": "slack:C1:9.0"}).json()
    other = client.post("/v1/loop/actions", headers=headers("slack:U2", org), json={"text": "견적 유효기간은 2주", "message_ref": "slack:C1:9.0"}).json()
    result = client.post("/v1/loop/actions/retract", headers=headers("slack:U1", org), json={"message_ref": "slack:C1:9.0"}).json()
    assert result["ids"] == [mine["id"]] and other["id"] not in result["ids"]
