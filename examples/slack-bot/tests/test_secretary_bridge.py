"""The secretary bridge: the owner's work requests in Slack go to the voice secretary."""

from __future__ import annotations

import json

import httpx

from secretary_bridge import ACTION_YES, BridgeConfig, SecretaryBridge, card_mrkdwn

OWNER = "U0OWNER"
REQUEST = ("<@U0BOT> <https://www.gonggong.ai/#aroom|gonggong.ai/#…> 이런업체와 우리가 협력을 한다면? 어떤제안을 가능하고 연락해서 "
           "시나리오를 짜보고 전략을 세워서 연락까지 해봐")
MAIL_CARD = {"kind": "text", "title": "메일 초안: 공공에이아이 협력 제안",
             "body": "받는 사람: contact@gonggong.ai\n제목: 온프레미스 AI 협력 제안\n- 제안 1. 사내 문서 근거 매칭\n- 제안 2. 서류 작성 온프레미스 처리"}


class FakeSecretary:
    def __init__(self) -> None:
        self.turns: list[str] = []
        self.sessions = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/sessions":
            self.sessions += 1
            return httpx.Response(200, json={"id": f"s{self.sessions}"})
        if request.url.path.endswith("/turn"):
            self.turns.append(json.loads(request.content)["text"])
            return httpx.Response(200, json={"used": "answer"})
        if request.url.path.endswith("/events"):
            if len(self.turns) == 1:
                events = [{"type": "activity", "id": "1", "label": "웹 페이지를 읽고 있어요."},
                          {"type": "text", "delta": "협력 제안 세 가지와 메일 초안을 만들었어요."},
                          {"type": "card", "card": MAIL_CARD},
                          {"type": "confirm", "id": "c1", "prompt": "contact@gonggong.ai에게 '온프레미스 AI 협력 제안' 메일을 보낼까요?"}]
            else:
                events = [{"type": "text", "delta": "보냈어요."}, {"type": "done"}]
            body = "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events)
            return httpx.Response(200, text=": connected\n\n" + body, headers={"content-type": "text/event-stream"})
        return httpx.Response(404)


class FakeWeb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def chat_postMessage(self, **kwargs):
        self.calls.append(("post", kwargs))
        return {"ts": f"300.{len(self.calls)}"}

    def chat_update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return {"ok": True}

    def chat_postEphemeral(self, **kwargs):
        self.calls.append(("ephemeral", kwargs))
        return {"ok": True}


def bridge(secretary: FakeSecretary, **overrides) -> SecretaryBridge:
    config = BridgeConfig(url="http://secretary", key="k", users=frozenset({OWNER}), **overrides)
    return SecretaryBridge(config, http=httpx.Client(transport=httpx.MockTransport(secretary.handler)))


def test_only_the_owners_work_requests_are_bridged() -> None:
    work = bridge(FakeSecretary())
    event = {"channel": "C1", "ts": "1.0", "user": OWNER}
    assert work.wants(event, "이 업체와 협력 제안 짜서 메일 초안 만들어 줘")
    assert work.wants(event, "비서 오늘 일정 정리")
    assert not work.wants(event, "연차휴가는 며칠이야?")  # a question: the document bot answers
    assert not work.wants({**event, "user": "U0SOMEONE"}, "메일 보내 줘")  # not the owner
    assert BridgeConfig(url="", key="k", users=frozenset({OWNER})).enabled is False


def test_a_request_runs_on_the_secretary_and_asks_before_sending() -> None:
    secretary = FakeSecretary()
    work = bridge(secretary)
    web = FakeWeb()
    work.handle({"channel": "C1", "ts": "1.0", "user": OWNER, "text": REQUEST}, web)
    first = secretary.turns[0]
    assert first.startswith("[슬랙 메시지로 받은 요청이다.") and "https://www.gonggong.ai/#aroom" in first
    assert "<@U0BOT>" not in first and "|gonggong.ai" not in first  # Slack's link markup unwrapped
    reply = [call for call in web.calls if call[0] == "update"][-1][1]
    assert reply["text"] == "협력 제안 세 가지와 메일 초안을 만들었어요."
    rendered = json.dumps(reply["blocks"], ensure_ascii=False)
    assert "*메일 초안: 공공에이아이 협력 제안*" in rendered and "• 제안 1. 사내 문서 근거 매칭" in rendered
    assert "한 일: 웹 페이지를 읽고 있어요." in rendered
    confirm = web.calls[-1][1]
    assert confirm["thread_ts"] == "1.0" and confirm["blocks"][1]["elements"][0]["action_id"] == ACTION_YES
    assert work.follows({"channel": "C1", "thread_ts": "1.0", "user": OWNER})  # the thread continues on the secretary

    # 진행 by someone else is refused; by the owner it says yes in the same session.
    stranger = {"user": {"id": "U0SOMEONE"}, "channel": {"id": "C1"}, "message": {"ts": "300.3", "thread_ts": "1.0"}}
    work.answer_confirmation(stranger, web, True)
    assert web.calls[-1][0] == "ephemeral" and len(secretary.turns) == 1
    owner = {**stranger, "user": {"id": OWNER}}
    work.answer_confirmation(owner, web, True)
    assert secretary.turns[1] == "응" and secretary.sessions == 1
    assert [call for call in web.calls if call[0] == "update"][-1][1]["text"] == "보냈어요."


def test_cards_read_well_in_slack() -> None:
    assert card_mrkdwn({"kind": "bar", "title": "비용", "unit": "만원", "items": [{"label": "개발", "value": 1200}]}) == "*비용*\n• 개발 — 1,200만원"
    assert card_mrkdwn({"kind": "list", "title": "할 일", "items": [{"label": "PR 리뷰", "badge": "오늘"}]}) == "*할 일*\n1. PR 리뷰 `오늘`"
    assert card_mrkdwn({"kind": "table", "title": "일정", "columns": ["시간", "제목"], "rows": [["15:00", "미팅"]],
                        "note": "캘린더"}) == "*일정*\n*시간* | *제목*\n15:00 | 미팅\n_캘린더_"
