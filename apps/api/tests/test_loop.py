"""The question–certification loop: gaps become questions, answers become criteria, people certify."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from baryon import loop
from baryon.loop import CERTIFIED, DRAFT, VERIFIED, LoopError
from baryon.main import app

client = TestClient(app)


def headers(user: str = "slack:U1", organization: str = "org-loop", roles: str = "member") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}


# --------------------------------------------------------------------------- state rules


def item(**overrides) -> dict:
    base = {"id": "c1", "label": "기준", "definition": "숫자는 출판본을 쓴다", "status": VERIFIED,
            "sources": [{"type": "action", "quote": "a"}, {"type": "answer", "quote": "b"}]}
    return {**base, **overrides}


def test_only_people_certify_and_only_after_every_source_is_compared() -> None:
    with pytest.raises(LoopError) as refused:
        loop.decide(item(), "agent", False, "approve", session={"checked": [0, 1]})
    assert refused.value.code == "forbidden_role"
    with pytest.raises(LoopError) as gate:
        loop.decide(item(), "kim", True, "approve", session={"checked": [0]})
    assert gate.value.code == "gate_incomplete" and "source:1" in str(gate.value)
    certified = loop.decide(item(), "kim", True, "approve", session={"checked": [0, 1]})
    assert certified["status"] == CERTIFIED and certified["certified_by"] == "kim"
    with pytest.raises(LoopError):
        loop.decide(certified, "kim", True, "approve", session={"checked": [0, 1]})


def test_hold_and_reject_need_a_reason_and_return_to_draft() -> None:
    with pytest.raises(LoopError) as short:
        loop.decide(item(), "kim", True, "hold", reason="음")
    assert short.value.code == "reason_required"
    held = loop.decide(item(), "kim", True, "reject", reason="원문과 수치가 다름")
    assert held["status"] == DRAFT and held["last_decision"]["decision"] == "reject"


def test_a_contested_term_keeps_both_readings_until_a_person_resolves_it() -> None:
    terms: dict = {}
    first = {"type": "document", "document_id": "d1", "chunk": 1, "quote": "연차는 15일"}
    loop.merge_term(terms, "연차", "입사 1년 뒤 15일", first, 1, True)
    assert terms["t_연차"]["status"] == VERIFIED
    loop.merge_term(terms, "연차", "입사 1년 뒤 20일", {**first, "document_id": "d2"}, 2, True)
    term = terms["t_연차"]
    assert loop.is_contested(term) and term["status"] == DRAFT and len(term["interpretations"]) == 2
    term["status"] = VERIFIED
    with pytest.raises(LoopError):
        loop.decide(term, "kim", True, "approve", session={"checked": [0, 1]})  # [쟁점] not resolved
    done = loop.decide(term, "kim", True, "approve", session={"checked": [0, 1], "contest": {"choice": 1}})
    assert done["status"] == CERTIFIED and done["resolution"]["choice"] == 1 and len(done["interpretations"]) == 2


def test_extraction_verifies_only_quotes_that_are_in_the_source() -> None:
    async def complete(messages):
        return json.dumps({"terms": [{"label": "법인카드", "definition": "회사 비용 결제 카드", "quote": "법인카드는 회사 비용 결제에만 쓴다."},
                                     {"label": "한도", "definition": "월 300만원", "quote": "한도는 월 500만원이다."}],
                           "relations": [{"s": "법인카드", "p": "한도이다", "o": "한도"}]}, ensure_ascii=False)

    text = "법인카드는 회사 비용 결제에만 쓴다. 한도는 월 300만원이다."
    result = asyncio.run(loop.extract_from_chunk({"title": "규정"}, 1, text, complete))
    assert [term["verified"] for term in result["terms"]] == [True, False]  # the second quote was rewritten
    assert result["relations"][0]["p"] == "한도이다"


def test_question_generation_keeps_three_perspectives_or_falls_back() -> None:
    action = {"id": "a1", "text": "팀장은 이번 분기 신규 채용을 미루기로 했다", "context": ""}

    async def good(messages):
        assert "40자 이내" in messages[0]["content"]
        return json.dumps({"question": "어떤 기준으로 채용을 미루셨나요?", "expected_answers": [
            {"perspective": "원칙", "text": "고정비를 늘리지 않는다"}, {"perspective": "조건", "text": "수주가 확정되기 전이라서"},
            {"perspective": "기대 결과", "text": "현금 흐름을 지키려고"}]}, ensure_ascii=False)

    async def broken(messages):
        return "모르겠습니다"

    made = asyncio.run(loop.make_question(action, [], good))
    assert made["generated_by"] == "model" and [a["perspective"] for a in made["expected_answers"]] == ["원칙", "조건", "기대 결과"]
    assert asyncio.run(loop.make_question(action, [], broken))["generated_by"] == "template"


def test_answers_need_words_when_every_expected_answer_is_wrong() -> None:
    question = {"expected_answers": [{"text": "원칙"}, {"text": "조건"}, {"text": "결과"}]}
    assert loop.answer_text(question, 1, False, "") == "조건"
    assert loop.answer_text(question, 0, False, "단, 해외는 예외") == "원칙 — 단, 해외는 예외"
    with pytest.raises(LoopError):
        loop.answer_text(question, None, True, " ")
    assert loop.answer_text(question, None, True, "고객이 요청해서") == "고객이 요청해서"


# --------------------------------------------------------------------------- over HTTP


def test_the_loop_end_to_end_over_http() -> None:
    org = "org-loop-http"
    me = headers(organization=org)
    recorded = client.post("/v1/loop/actions", headers=me, json={
        "text": "홍 대표는 인증을 에이전트에 맡기지 않으면서 매일 자동 라운드는 승인했다",
        "context": "에이전트가 라운드를 자동으로 돌려도 되는지 물었다", "source": "slack", "source_ref": "slack:C1:1.0", "judge": True})
    assert recorded.status_code == 200, recorded.text
    question = recorded.json()["question"]
    assert question and question["asked_to"] == "slack:U1" and len(question["expected_answers"]) == 3

    # Only the person asked sees it; an admin can see everyone's.
    assert [q["id"] for q in client.get("/v1/loop/questions", headers=me).json()["data"]] == [question["id"]]
    assert client.get("/v1/loop/questions", headers=headers("slack:U2", org)).json()["data"] == []
    assert len(client.get("/v1/loop/questions?everyone=true", headers=headers("boss", org, "admin")).json()["data"]) == 1

    # An agent cannot answer; the person picks an expected answer and adds the condition.
    agent = headers("baryon-agent", org, "agent")
    assert client.post(f"/v1/loop/questions/{question['id']}/answer", headers=agent, json={"choice": 0}).status_code == 403
    answered = client.post(f"/v1/loop/questions/{question['id']}/answer", headers=me,
                           json={"choice": 1, "text": "되돌릴 수 있는 일만 맡긴다"})
    assert answered.status_code == 200, answered.text
    criterion = answered.json()
    assert criterion["kind"] == "criterion" and criterion["status"] == VERIFIED and "되돌릴 수 있는 일만" in criterion["definition"]

    # Certification: the agent is refused, a person without comparing every source is refused, then it passes.
    path = f"/v1/loop/items/{criterion['id']}/decision"
    refused = client.post(path, headers=agent, json={"decision": "approve", "checked": [0, 1]})
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "forbidden_role"
    gate = client.post(path, headers=me, json={"decision": "approve", "checked": [0]})
    assert gate.status_code == 409 and gate.json()["detail"]["code"] == "gate_incomplete"
    shown = client.get(f"/v1/loop/items/{criterion['id']}", headers=me).json()
    assert [source["type"] for source in shown["sources"]] == ["action", "answer"] and shown["sources"][0]["text"]
    certified = client.post(path, headers=me, json={"decision": "approve", "checked": [0, 1]})
    assert certified.status_code == 200, certified.text
    assert certified.json()["status"] == CERTIFIED and certified.json().get("document_id")

    # Decisions see certified items only; exports carry the state.
    assert [i["id"] for i in client.get("/v1/loop/decision-kb", headers=me).json()["data"]] == [criterion["id"]]
    ontology = client.get("/v1/loop/ontology", headers=me).json()
    assert ontology["terms"][0]["status"] == CERTIFIED and ontology["questions"][0]["status"] == "answered"
    ossie = client.get("/v1/loop/ossie", headers=me).json()
    assert any(field["description"].startswith("[certified]") for field in ossie["datasets"][0]["fields"])
    stats = client.get("/v1/loop/stats", headers=me).json()
    assert stats["items"][CERTIFIED] == 1 and stats["questions"]["answered"] == 1
    # The certified criterion is now a document the chat can cite.
    titles = [d["title"] for d in client.get("/v1/documents?tag=certified", headers=headers("boss", org, "admin")).json()["data"]]
    assert any("certified-" in title or "기준" in title for title in titles)


def test_rounds_need_an_admin_or_agent_and_judge_waiting_actions() -> None:
    org = "org-loop-round"
    client.post("/v1/loop/actions", headers=headers(organization=org), json={"text": "김 과장은 견적서에서 운송비 항목을 뺐다"})
    assert client.post("/v1/loop/rounds", headers=headers(organization=org), json={}).status_code == 403
    summary = client.post("/v1/loop/rounds", headers=headers("baryon-agent", org, "agent"), json={"documents": 0}).json()
    assert summary["round"] == 1 and summary["questions"] == 1
    assert client.get("/v1/loop/stats", headers=headers(organization=org)).json()["actions"]["asked"] == 1
