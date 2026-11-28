"""The question–certification loop in Slack: record decisions, ask first, certify against the sources."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baryon_slack_bot import BaryonClient, Config  # noqa: E402
from loop_desk import (  # noqa: E402
    ACTION_CERTIFY, ACTION_PICK, CHECKS, LoopConfig, LoopDesk, certify_blocks, question_blocks, wants_certify_list, wants_questions,
)

QUESTION = {"id": "q1", "question": "어떤 기준으로 운송비를 빼셨나요?", "asked_to": "slack:U1", "status": "open", "asked_count": 0,
            "action": {"text": "<@U1>님이 슬랙에서 말한 결정·요청: 운송비는 빼고 견적 내자"},
            "expected_answers": [{"perspective": "원칙", "text": "고객이 직접 운송하는 경우"},
                                 {"perspective": "조건", "text": "100km 이내 납품일 때"},
                                 {"perspective": "기대 결과", "text": "견적 경쟁력을 높이려고"}]}
ITEM = {"id": "c1", "label": "기준: 운송비", "definition": "고객이 직접 운송하면 운송비를 뺀다", "status": "verified", "contested": False,
        "sources": [{"type": "action", "text": "운송비는 빼고 견적 내자"}, {"type": "answer", "quote": "고객이 직접 운송하면", "question": "어떤 기준?"}]}


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, body, dict(request.headers)))
        path = request.url.path
        if path == "/v1/loop/actions":
            return httpx.Response(200, json={"id": "a1"})
        if path == "/v1/loop/questions":
            return httpx.Response(200, json={"data": [QUESTION]})
        if path.endswith("/asked") or path.endswith("/skip"):
            return httpx.Response(200, json={})
        if path == "/v1/loop/questions/q1/answer":
            return httpx.Response(200, json={"id": "c1"})
        if path == "/v1/loop/items/c1":
            return httpx.Response(200, json=ITEM)
        if path == "/v1/loop/items/c1/decision":
            if body.get("decision") == "approve" and sorted(body.get("checked") or []) != [0, 1]:
                return httpx.Response(409, json={"detail": {"code": "gate_incomplete", "message": "원문 대조·쟁점 검토를 마치지 않았습니다: source:1"}})
            return httpx.Response(200, json={**ITEM, "status": "certified" if body["decision"] == "approve" else "draft"})
        if path == "/v1/loop/rounds":
            return httpx.Response(200, json={"round": 1, "questions": 1})
        return httpx.Response(404)


class FakeWeb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            return {"ts": "9.9", "messages": [{"ts": "1.0", "text": "견적 어떻게 할까요?"}]}
        return call

    def named(self, name: str) -> list[dict]:
        return [kwargs for call, kwargs in self.calls if call == name]


def desk(api: FakeApi, users: set[str] | None = None) -> LoopDesk:
    config = Config(api_url="http://api.test", organization_id="org", roles="member")
    baryon = BaryonClient(config, http=httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(api.handler)))
    return LoopDesk(LoopConfig(users=frozenset(users or {"U1"})), baryon, "UBOT")


def test_only_consenting_peoples_decisions_are_recorded() -> None:
    api = FakeApi()
    loop = desk(api)
    web = FakeWeb()
    assert loop.observe({"user": "U1", "channel": "C1", "ts": "2.0", "thread_ts": "1.0", "text": "운송비는 빼고 견적 내자"}, web)
    method, path, body, sent = api.calls[-1]
    assert path == "/v1/loop/actions" and "운송비는 빼고" in body["text"] and body["source_ref"] == "slack:C1:1.0"
    assert body["context"] == "견적 어떻게 할까요?" and sent["x-on-behalf-of"] == "slack:U1"
    assert not loop.observe({"user": "U2", "channel": "C1", "ts": "3.0", "text": "운송비는 빼고 견적 내자"}, web)  # no consent
    assert not loop.observe({"user": "U1", "channel": "C1", "ts": "4.0", "text": "오늘 점심 맛있었어요"}, web)  # not a decision
    assert not loop.observe({"user": "U1", "bot_id": "B1", "channel": "C1", "ts": "5.0", "text": "보류하자"}, web)


def test_the_bot_asks_first_with_three_answers_and_an_escape() -> None:
    api = FakeApi()
    web = FakeWeb()
    assert desk(api).ask_pending(web) == 1
    posted = web.named("chat_postMessage")[0]
    assert posted["channel"] == "U1"  # a DM to the person who acted
    actions = [element for block in posted["blocks"] if block["type"] == "actions" for element in block["elements"]]
    assert [a["action_id"] for a in actions[:3]] == [f"{ACTION_PICK}_{i}" for i in range(3)]
    assert [a["text"]["text"] for a in actions[3:]] == ["모두 틀림", "직접 쓰기", "나중에"]
    assert any(path == "/v1/loop/questions/q1/asked" for _, path, _, _ in api.calls)


def test_picking_an_answer_opens_a_form_and_the_answer_becomes_a_card_to_certify() -> None:
    api = FakeApi()
    loop = desk(api)
    web = FakeWeb()
    loop.on_button({"actions": [{"action_id": f"{ACTION_PICK}_1", "value": "q1|1"}], "user": {"id": "U1"}, "trigger_id": "T"}, web)
    view = web.named("views_open")[0]["view"]
    assert "100km 이내 납품일 때" in json.dumps(view, ensure_ascii=False) and view["blocks"][1]["optional"] is True
    submitted = {"user": {"id": "U1"}, "view": {"private_metadata": view["private_metadata"],
                                                "state": {"values": {"text": {"text": {"value": "단, 냉장품은 예외"}}}}}}
    assert loop.on_answer_view(submitted, web) == {"item_id": "c1"}
    assert [c for c in api.calls if c[1] == "/v1/loop/questions/q1/answer"][0][2] == {"choice": 1, "none_correct": False, "text": "단, 냉장품은 예외"}
    loop.after_answer(web, "U1", "c1")
    card = json.dumps(web.named("chat_postMessage")[-1]["blocks"], ensure_ascii=False)
    assert "인증 대기" in card and "원문 1과 대조했어요" in card and "원문 2과 대조했어요" in card and "서명(인증)" in card


def test_signing_sends_the_ticked_sources_and_the_server_gate_is_explained() -> None:
    api = FakeApi()
    loop = desk(api)
    web = FakeWeb()
    base = {"actions": [{"action_id": ACTION_CERTIFY, "value": "c1"}], "user": {"id": "U1"}, "channel": {"id": "D1"}, "message": {"ts": "7.0"}}
    one = {"values": {CHECKS: {CHECKS: {"selected_options": [{"value": "0"}]}}}}
    loop.on_button({**base, "state": one}, web)
    assert "모든 원문을 대조해 체크해야" in web.named("chat_postEphemeral")[-1]["text"]
    both = {"values": {CHECKS: {CHECKS: {"selected_options": [{"value": "0"}, {"value": "1"}]}}}}
    loop.on_button({**base, "state": both}, web)
    assert "인증했어요" in web.named("chat_update")[-1]["text"]


def test_on_demand_commands_and_blocks() -> None:
    assert wants_questions("@baryon 나한테 질문 있어?") and wants_questions("먼저 물어봐") and not wants_questions("연차 며칠이야?")
    assert wants_certify_list("인증할 것 보여줘") and wants_certify_list("인증 대기 목록") and not wants_certify_list("인증서 발급 절차")
    assert len(question_blocks(QUESTION)) == 5
    contested = {**ITEM, "contested": True, "interpretations": [{"definition": "15일"}, {"definition": "20일"}]}
    assert "radio_buttons" in json.dumps(certify_blocks(contested))
