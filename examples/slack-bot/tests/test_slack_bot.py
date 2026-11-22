from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baryon_slack_bot import (  # noqa: E402
    PENDING_TEXT, Answer, BaryonClient, Config, Responder, ThreadSubscriptions, build_blocks, strip_mentions,
    thread_messages, to_mrkdwn,
)

BOT = "UBOT"
CITATIONS = [
    {"document_id": "d1", "title": "취업규칙.md", "chunk": 6},
    {"document_id": "d1", "title": "취업규칙.md", "chunk": 6},
    {"document_id": "d2", "title": "복리후생_안내.md", "chunk": 3},
]


def fake_api(requests: list[httpx.Request], fail: bool = False) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if fail:
            return httpx.Response(502, json={"detail": "model down"})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={
                "id": "trace-1", "choices": [{"message": {"role": "assistant", "content": "연차휴가는 **입사 1년 후 15일**입니다."}}],
                "citations": CITATIONS,
            })
        return httpx.Response(200, json={"data": [
            {"document_id": "d1", "chunk": 6, "content": "제25조(연차휴가) 1년간 80% 이상 출근 시   15일"},
        ]})
    return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))


class FakeWeb:
    def __init__(self, replies: list[dict] | None = None, history_error: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.replies = replies or []
        self.history_error = history_error

    def chat_postMessage(self, **kwargs):
        self.calls.append(("post", kwargs))
        return {"ts": "200.1"}

    def chat_update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return {"ok": True}

    def conversations_replies(self, **kwargs):
        self.calls.append(("replies", kwargs))
        if self.history_error:
            raise RuntimeError("missing_scope")
        return {"messages": self.replies}

    def reactions_add(self, **kwargs):
        self.calls.append(("react+", kwargs))

    def reactions_remove(self, **kwargs):
        self.calls.append(("react-", kwargs))

    def named(self, name: str) -> list[dict]:
        return [kwargs for call, kwargs in self.calls if call == name]


def responder(requests, **kwargs) -> Responder:
    config = Config(api_url="http://api.test", organization_id="org", roles="member")
    return Responder(config, BaryonClient(config, http=fake_api(requests, **kwargs)), BOT)


def test_strip_mentions_and_mrkdwn() -> None:
    assert strip_mentions("<@UBOT> 연차는 며칠? <@U123|kim>") == "연차는 며칠?"
    assert to_mrkdwn("## 요약\n[RFP](https://x.test/a)") == "*요약*\n<https://x.test/a|RFP>"
    assert to_mrkdwn("기간은 **15일**입니다") == "기간은 ​*15일*​입니다"


def test_thread_messages_maps_roles_and_skips_status_posts() -> None:
    replies = [
        {"user": "U1", "text": "<@UBOT> 연차는 며칠?", "ts": "1"},
        {"user": BOT, "bot_id": "B1", "text": "15일입니다.", "ts": "2"},
        {"user": BOT, "bot_id": "B1", "text": PENDING_TEXT, "ts": "3"},
        {"user": "U1", "subtype": "channel_join", "text": "joined", "ts": "4"},
        {"user": "U1", "text": "그럼 반차도 돼?", "ts": "5"},
    ]
    assert thread_messages(replies, BOT, 10) == [
        {"role": "user", "content": "연차는 며칠?"},
        {"role": "assistant", "content": "15일입니다."},
        {"role": "user", "content": "그럼 반차도 돼?"},
    ]
    assert thread_messages(replies, BOT, 2)[0]["role"] == "user"  # never starts with the assistant


def test_build_blocks_dedupes_sources_and_quotes_snippets() -> None:
    blocks = build_blocks(Answer("**15일**", CITATIONS, "trace-1"), {("d1", 6): "연차휴가:   15일"}, max_sources=4)
    sources = blocks[2]["text"]["text"]
    assert blocks[0]["text"]["text"] == "​*15일*​"
    assert "*1. 취업규칙.md* · chunk 6\n> 연차휴가: 15일" in sources
    assert "*2. 복리후생_안내.md* · chunk 3" in sources and "3." not in sources
    assert "trace-1" in blocks[-1]["elements"][0]["text"]


def test_build_blocks_warns_without_sources() -> None:
    blocks = build_blocks(Answer("모르겠습니다", [], ""), {}, max_sources=4)
    assert "근거 문서를 찾지 못했습니다" in blocks[-1]["elements"][0]["text"]


def test_mention_answers_in_thread_with_sources() -> None:
    requests: list[httpx.Request] = []
    bot, web = responder(requests), FakeWeb()
    bot.answer({"channel": "C1", "user": "U1", "ts": "100.1", "text": "<@UBOT> 연차휴가는 며칠이야?"}, web)

    chat = json.loads(requests[0].content)
    assert chat["messages"] == [{"role": "user", "content": "연차휴가는 며칠이야?"}]
    assert chat["stream"] is False and chat["thread_id"] == "slack:C1:100.1"
    # dev mode: the Slack user is delegated, the bot itself is X-User-Id (→ acting_service)
    headers = requests[0].headers
    assert headers["X-On-Behalf-Of"] == "slack:U1" and headers["X-On-Behalf-Of-Roles"] == "member"
    assert headers["X-User-Id"] == "slack-bot" and headers["X-Organization-Id"] == "org"
    assert "X-Roles" not in headers and "Authorization" not in headers
    assert requests[1].headers["X-On-Behalf-Of"] == "slack:U1"
    assert requests[1].url.path == "/v1/documents/search"

    assert web.named("post")[0]["thread_ts"] == "100.1"
    update = web.named("update")[0]
    assert update["ts"] == "200.1" and "15일" in update["text"]
    assert "취업규칙.md" in json.dumps(update["blocks"], ensure_ascii=False)
    assert [c["name"] for c in web.named("react+")] == ["eyes", "white_check_mark"]
    assert web.named("react-")[0]["name"] == "eyes"
    assert not web.named("replies")  # top-level mention: no history to read
    assert ("C1", "100.1") in bot.subscriptions


def test_thread_mention_sends_history_and_survives_missing_scope() -> None:
    requests: list[httpx.Request] = []
    replies = [{"user": "U1", "text": "연차는 며칠?", "ts": "1"}, {"user": BOT, "bot_id": "B", "text": "15일", "ts": "2"},
               {"user": "U2", "text": "<@UBOT> 반차도 돼?", "ts": "3"}]
    responder(requests).answer({"channel": "C1", "user": "U2", "ts": "3", "thread_ts": "1", "text": "<@UBOT> 반차도 돼?"}, FakeWeb(replies))
    assert [m["role"] for m in json.loads(requests[0].content)["messages"]] == ["user", "assistant", "user"]

    requests.clear()
    responder(requests).answer({"channel": "C1", "user": "U2", "ts": "3", "thread_ts": "1", "text": "<@UBOT> 반차도 돼?"},
                               FakeWeb(history_error=True))
    assert json.loads(requests[0].content)["messages"] == [{"role": "user", "content": "반차도 돼?"}]


def test_follow_up_only_in_subscribed_threads() -> None:
    bot = responder([])
    bot.subscriptions.add("C1", "100.1")
    assert bot.should_follow_up({"channel": "C1", "user": "U1", "ts": "101", "thread_ts": "100.1", "text": "더 알려줘"})
    assert not bot.should_follow_up({"channel": "C1", "user": "U1", "ts": "101", "thread_ts": "999", "text": "다른 스레드"})
    assert not bot.should_follow_up({"channel": "C1", "user": "U1", "ts": "101", "text": "채널 일반 메시지"})
    assert not bot.should_follow_up({"channel": "C1", "user": "U1", "ts": "101", "thread_ts": "100.1", "text": "<@UBOT> 멘션"})
    assert not bot.should_follow_up({"channel": "C1", "bot_id": "B", "ts": "101", "thread_ts": "100.1", "text": "봇"})
    bot.config.subscribe_threads = False
    assert not bot.should_follow_up({"channel": "C1", "user": "U1", "ts": "101", "thread_ts": "100.1", "text": "더 알려줘"})


def test_api_error_replaces_placeholder() -> None:
    web = FakeWeb()
    responder([], fail=True).answer({"channel": "C1", "user": "U1", "ts": "100.1", "text": "<@UBOT> 질문"}, web)
    assert "HTTP 502" in web.named("update")[0]["text"]
    assert "x" in [c["name"] for c in web.named("react+")]


def test_empty_mention_shows_usage() -> None:
    requests: list[httpx.Request] = []
    web = FakeWeb()
    responder(requests).answer({"channel": "C1", "user": "U1", "ts": "1", "text": "<@UBOT>"}, web)
    assert not requests and "질문" in web.named("post")[0]["text"]


def test_subscriptions_expire() -> None:
    subs = ThreadSubscriptions(ttl_seconds=-1)
    subs.add("C", "1")
    assert ("C", "1") not in subs


def test_unanswered_question_lists_no_sources() -> None:
    """Retrieval returned passages, but the model said the documents do not answer it: no "근거" list."""
    blocks = build_blocks(Answer("문서에서 찾지 못했습니다", CITATIONS, "trace-2", answered=False), {}, max_sources=4)
    text = json.dumps(blocks, ensure_ascii=False)
    assert "근거*" not in text and "지식 공백" in text
