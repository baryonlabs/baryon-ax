from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baryon_slack_bot import BaryonClient, Config, Responder  # noqa: E402
from proactive import (  # noqa: E402
    ACTION_DISMISS, ACTION_PUBLISH, GapDesk, KnowledgeClient, ProactiveConfig, ProactiveState, ask_blocks,
)

BOT = "UBOT"
GAPS = [
    {"id": "gap_a", "question": "구내식당은 몇 시에 열어?", "user_id": "slack:UASK", "reasons": ["no_citations"],
     "occurrences": 1, "last_seen": "2026-01-02", "thread_id": "slack:CQA:111.1"},
    {"id": "gap_b", "question": "주차 등록은 어디서 해?", "user_id": "web-user", "reasons": ["refusal"],
     "occurrences": 3, "last_seen": "2026-01-01", "thread_id": None},
]


class FakeApi:
    def __init__(self, publish_status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.publish_status = publish_status

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            path = request.url.path
            if path == "/v1/admin/gaps":
                return httpx.Response(200, json={"data": GAPS})
            if path.endswith("/dismiss"):
                return httpx.Response(200, json={"status": "dismissed"})
            if path == "/v1/tacit/entries":
                return httpx.Response(200, json={"id": "tac_1", "status": "draft"})
            if path.endswith("/publish"):
                if self.publish_status != 200:
                    return httpx.Response(self.publish_status, json={"detail": "nope"})
                return httpx.Response(200, json={"id": "tac_1", "status": "published", "published_document_id": "doc_9"})
            if path == "/v1/chat/completions":
                return httpx.Response(200, json={"id": "trace-2", "choices": [{"message": {"content": "오전 11시 30분에 엽니다."}}],
                                                 "citations": [{"document_id": "doc_9", "title": "tacit-tac_1.md", "chunk": 1}]})
            return httpx.Response(200, json={"data": []})
        return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))

    def find(self, path_suffix: str) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.path.endswith(path_suffix)]


class FakeWeb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.counter = 0

    def chat_postMessage(self, **kwargs):
        self.counter += 1
        self.calls.append(("post", kwargs))
        return {"ts": f"900.{self.counter}", "channel": kwargs["channel"]}

    def chat_update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return {"ok": True}

    def chat_postEphemeral(self, **kwargs):
        self.calls.append(("ephemeral", kwargs))
        return {"ok": True}

    def of(self, kind: str) -> list[dict]:
        return [kwargs for name, kwargs in self.calls if name == kind]


def make_desk(tmp_path, api: FakeApi, **overrides) -> GapDesk:
    config = Config(api_url="http://api.test", show_snippets=False)
    baryon = BaryonClient(config, http=api.client())
    responder = Responder(config, baryon, BOT)
    pconfig = ProactiveConfig(channel="CDESK", state_path=str(tmp_path / "state.json"), **overrides)
    baryon.auth.bot_roles = pconfig.service_roles
    return GapDesk(pconfig, responder, KnowledgeClient(baryon), ProactiveState(pconfig.state_path))


def action_body(action_id: str, value: str, user: str = "UANS") -> dict:
    return {"user": {"id": user}, "channel": {"id": "CDESK"}, "message": {"ts": "900.2"},
            "actions": [{"action_id": action_id, "value": value}]}


def test_asks_most_frequent_new_gaps_once_and_persists(tmp_path) -> None:
    api, web = FakeApi(), FakeWeb()
    desk = make_desk(tmp_path, api, max_per_run=5, mention="<!here>")
    assert desk.run_once(web, now=datetime(2026, 1, 5, 10)) == 2
    posts = web.of("post")
    assert [post["channel"] for post in posts] == ["CDESK", "CDESK"]
    assert "주차" in posts[0]["text"]  # 3 occurrences first
    assert posts[0]["blocks"][0]["text"]["text"].startswith("<!here>")
    gaps_call = api.find("/v1/admin/gaps")[0].headers  # the bot reads gaps as itself
    assert gaps_call["x-user-id"] == "slack-bot" and gaps_call["x-roles"] == "admin"
    assert "x-on-behalf-of" not in gaps_call and "x-on-behalf-of-roles" not in gaps_call

    # a restarted bot reads the state file and does not ask again
    again = make_desk(tmp_path, api, max_per_run=5)
    assert again.run_once(web, now=datetime(2026, 1, 5, 11)) == 0
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["asks"]
    assert saved["gap_a"]["origin_thread"] == "slack:CQA:111.1"


def test_quiet_hours_and_weekends_skip(tmp_path) -> None:
    api, web = FakeApi(), FakeWeb()
    desk = make_desk(tmp_path, api, hours="9-18", weekdays_only=True)
    assert desk.run_once(web, now=datetime(2026, 1, 5, 20)) == 0   # Monday evening
    assert desk.run_once(web, now=datetime(2026, 1, 10, 10)) == 0  # Saturday
    assert api.requests == []


def test_thread_reply_becomes_draft_with_publish_buttons(tmp_path) -> None:
    api, web = FakeApi(), FakeWeb()
    desk = make_desk(tmp_path, api, max_per_run=1)
    desk.run_once(web, now=datetime(2026, 1, 5, 10))
    reply = {"channel": "CDESK", "thread_ts": "900.1", "ts": "901.0", "user": "UANS", "text": "평일 11시 30분부터 엽니다."}
    assert desk.answer_gap_id(reply) == "gap_b"
    assert desk.answer_gap_id({**reply, "text": f"<@{BOT}> 이건 질문"}) is None
    assert desk.answer_gap_id({**reply, "thread_ts": "other"}) is None

    desk.on_answer(reply, "gap_b", web)
    saved = json.loads(api.find("/v1/tacit/entries")[0].content)
    assert saved == {"question": "주차 등록은 어디서 해?", "answer": "평일 11시 30분부터 엽니다.", "gap_id": "gap_b", "source": "slack"}
    assert api.find("/v1/tacit/entries")[0].headers["x-on-behalf-of"] == "slack:UANS"
    buttons = web.of("post")[-1]["blocks"][1]["elements"]
    assert buttons[0]["action_id"] == ACTION_PUBLISH and buttons[0]["value"] == "tac_1|gap_b"


def test_publish_updates_desk_and_reanswers_original_slack_thread(tmp_path) -> None:
    api, web = FakeApi(), FakeWeb()
    desk = make_desk(tmp_path, api, max_per_run=5)
    desk.run_once(web, now=datetime(2026, 1, 5, 10))  # gap_b → 900.1, gap_a → 900.2

    desk.on_publish(action_body(ACTION_PUBLISH, "tac_1|gap_a"), web)
    assert api.find("/publish")[0].headers["x-on-behalf-of"] == "slack:UANS"
    assert desk.state.get("gap_a")["status"] == "published"
    updates = web.of("update")
    assert any(update["ts"] == "900.2" and "답이 등록됐어요" in update["blocks"][0]["text"]["text"] for update in updates)
    reanswer = web.of("post")[-1]
    assert reanswer["channel"] == "CQA" and reanswer["thread_ts"] == "111.1"
    assert "<@UASK>" in reanswer["blocks"][0]["text"]["text"]
    asked = json.loads(api.find("/v1/chat/completions")[0].content)
    assert asked["thread_id"] == "slack:CQA:111.1"
    assert api.find("/v1/chat/completions")[0].headers["x-on-behalf-of"] == "slack:UASK"


def test_only_author_can_publish(tmp_path) -> None:
    api, web = FakeApi(publish_status=403), FakeWeb()
    desk = make_desk(tmp_path, api)
    desk.on_publish(action_body(ACTION_PUBLISH, "tac_1|gap_a", user="UOTHER"), web)
    assert web.of("ephemeral")[0]["user"] == "UOTHER"
    assert web.of("update") == []


def test_web_gap_publish_does_not_reanswer(tmp_path) -> None:
    api, web = FakeApi(), FakeWeb()
    desk = make_desk(tmp_path, api, max_per_run=5)
    desk.run_once(web, now=datetime(2026, 1, 5, 10))
    posts_before = len(web.of("post"))
    desk.on_publish(action_body(ACTION_PUBLISH, "tac_1|gap_b"), web)
    assert len(web.of("post")) == posts_before
    assert api.find("/v1/chat/completions") == []


def test_dismiss_closes_gap(tmp_path) -> None:
    api, web = FakeApi(), FakeWeb()
    desk = make_desk(tmp_path, api, max_per_run=1)
    desk.run_once(web, now=datetime(2026, 1, 5, 10))
    desk.on_dismiss(action_body(ACTION_DISMISS, "gap_b", user="UADMIN"), web)
    dismiss = api.find("/v1/admin/gaps/gap_b/dismiss")[0].headers
    assert dismiss["x-roles"] == "admin" and "x-on-behalf-of" not in dismiss
    assert desk.state.get("gap_b")["status"] == "dismissed"
    assert "해당 없음" in web.of("update")[0]["blocks"][0]["text"]["text"]


def test_disabled_without_channel_and_ask_text(tmp_path) -> None:
    assert ProactiveConfig().enabled is False
    blocks = ask_blocks(GAPS[0])
    assert "<@UASK>" in blocks[1]["elements"][0]["text"] and "근거 문서 없음" in blocks[1]["elements"][0]["text"]
    assert blocks[2]["elements"][0]["value"] == "gap_a"


def test_slack_only_skips_gaps_from_other_clients(tmp_path) -> None:
    api, web = FakeApi(), FakeWeb()
    desk = make_desk(tmp_path, api, max_per_run=5, slack_only=True)
    assert desk.run_once(web, now=datetime(2026, 1, 5, 10)) == 1
    assert "구내식당" in web.of("post")[0]["text"]


def test_action_listener_takes_only_bolt_arguments() -> None:
    import inspect

    from baryon_slack_bot import action_listener

    seen = []
    listener = action_listener(lambda body, client: seen.append((body, client)))
    # Bolt injects arguments by name; any extra parameter would arrive as None.
    assert list(inspect.signature(listener).parameters) == ["ack", "body", "client"]
    acked = []
    listener(ack=lambda: acked.append(True), body={"x": 1}, client="web")
    assert acked == [True] and seen == [({"x": 1}, "web")]


def test_second_publish_for_same_gap_does_not_reanswer_again(tmp_path) -> None:
    api, web = FakeApi(), FakeWeb()
    desk = make_desk(tmp_path, api, max_per_run=5)
    desk.run_once(web, now=datetime(2026, 1, 5, 10))
    desk.on_publish(action_body(ACTION_PUBLISH, "tac_1|gap_a"), web)
    desk.on_publish(action_body(ACTION_PUBLISH, "tac_2|gap_a"), web)
    assert len(api.find("/publish")) == 2
    assert len(api.find("/v1/chat/completions")) == 1
    assert desk.state.get("gap_a")["reanswered"] is True


def test_quote_skips_metadata_lines() -> None:
    from baryon_slack_bot import quote_text

    chunk = ("암묵지: 연차는 며칠?\n\n> 답변자: slack:U123\n> 작성일: 2026-01-01T00:00:00+00:00\n\n"
             "입사 1년 후 15일입니다.\n> 인용문은 그대로")
    assert quote_text(chunk) == "암묵지: 연차는 며칠? 입사 1년 후 15일입니다. > 인용문은 그대로"
