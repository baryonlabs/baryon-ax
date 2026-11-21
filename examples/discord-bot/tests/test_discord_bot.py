from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baryon_discord_bot import (  # noqa: E402
    PENDING_TEXT, BaryonClient, Config, GapDesk, ProactiveConfig, ProactiveState, Responder, ask_text, quote_text,
    sources_embed, strip_mentions, thread_messages, Answer,
)

BOT = 999
CITATIONS = [
    {"document_id": "d1", "title": "취업규칙.md", "chunk": 6},
    {"document_id": "d1", "title": "취업규칙.md", "chunk": 6},
    {"document_id": "d2", "title": "복리후생_안내.md", "chunk": 3},
]
GAPS = [
    {"id": "gap_a", "question": "구내식당은 몇 시에 열어?", "user_id": "discord:111", "reasons": ["no_citations"],
     "occurrences": 1, "last_seen": "2026-01-02", "thread_id": "discord:500:501"},
    {"id": "gap_b", "question": "주차 등록은 어디서 해?", "user_id": "web-user", "reasons": ["refusal"],
     "occurrences": 3, "last_seen": "2026-01-01", "thread_id": None},
]


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- fakes


class FakeApi:
    def __init__(self, fail: bool = False, publish_status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.fail = fail
        self.publish_status = publish_status

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            path = request.url.path
            if self.fail:
                return httpx.Response(502, json={"detail": "model down"})
            if path == "/v1/chat/completions":
                return httpx.Response(200, json={"id": "trace-1", "citations": CITATIONS,
                                                 "choices": [{"message": {"content": "연차휴가는 **15일**입니다."}}]})
            if path == "/v1/documents/search":
                return httpx.Response(200, json={"data": [{"document_id": "d1", "chunk": 6,
                                                           "content": "제목\n\n> 답변자: discord:1\n\n제25조 15일 부여"}]})
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
            return httpx.Response(404)
        return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))

    def find(self, suffix: str) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.path.endswith(suffix)]


class Sent:
    def __init__(self, channel, content, **kwargs) -> None:
        self.channel, self.content, self.kwargs = channel, content, kwargs
        self.initial = content
        self.id = channel.next_id()
        self.edits: list[dict] = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        self.content = kwargs.get("content", self.content)

    async def create_thread(self, name: str):
        thread = FakeChannel(self.id, parent_id=self.channel.id, name=name)
        self.channel.threads.append(thread)
        return thread


class FakeChannel:
    counter = 1000

    def __init__(self, channel_id: int, parent_id: int | None = None, name: str = "", history=None) -> None:
        self.id, self.parent_id, self.name = channel_id, parent_id, name
        self.sent: list[Sent] = []
        self.threads: list[FakeChannel] = []
        self._history = history or []
        self.starter_message = None

    def next_id(self) -> int:
        FakeChannel.counter += 1
        return FakeChannel.counter

    async def send(self, content=None, **kwargs):
        message = Sent(self, content, **kwargs)
        self.sent.append(message)
        return message

    async def fetch_message(self, message_id: int):
        return next(message for message in self.sent if message.id == message_id)

    async def history(self, limit: int = 50):
        for message in reversed(self._history):  # newest first, like discord.py
            yield message


def author(user_id: int, bot: bool = False):
    return SimpleNamespace(id=user_id, bot=bot)


class FakeMessage:
    def __init__(self, content: str, channel: FakeChannel, user: int = 111, mentions=(), message_id: int = 42) -> None:
        self.content, self.channel, self.id = content, channel, message_id
        self.author = author(user)
        self.mentions = [author(m) for m in mentions]
        self.reactions: list[tuple[str, str]] = []
        self.replies: list[dict] = []
        self.thread: FakeChannel | None = None

    async def add_reaction(self, emoji):
        self.reactions.append(("add", emoji))

    async def remove_reaction(self, emoji, _member):
        self.reactions.append(("remove", emoji))

    async def create_thread(self, name: str):
        self.thread = FakeChannel(self.id, parent_id=self.channel.id, name=name)
        return self.thread

    async def reply(self, content, **kwargs):
        self.replies.append({"content": content, **kwargs})


def responder(api: FakeApi, **overrides) -> Responder:
    config = Config(api_url="http://api.test", **overrides)
    return Responder(config, BaryonClient(config, http=api.client()), BOT)


# --------------------------------------------------------------------------- answering


def test_mention_opens_thread_and_answers_with_sources() -> None:
    api = FakeApi()
    bot = responder(api)
    channel = FakeChannel(500)
    message = FakeMessage(f"<@{BOT}> 연차휴가는 며칠이야?", channel, mentions=[BOT])
    assert bot.should_answer(message)
    run(bot.answer(message))

    thread = message.thread
    assert thread is not None and thread.name == "연차휴가는 며칠이야?"
    pending = thread.sent[0]
    assert pending.initial == PENDING_TEXT
    final = pending.edits[-1]
    assert final["content"] == "연차휴가는 **15일**입니다."
    embed = final["embed"]
    assert embed["title"] == "📑 근거" and embed["footer"]["text"] == "baryon-ax · trace trace-1"
    assert "**1. 취업규칙.md** · chunk 6\n> 제목 제25조 15일 부여" in embed["description"]
    assert "복리후생_안내.md" in embed["description"] and embed["description"].count("취업규칙") == 1
    body = json.loads(api.find("/v1/chat/completions")[0].content)
    assert body["thread_id"] == f"discord:500:{thread.id}"
    assert api.find("/v1/chat/completions")[0].headers["x-user-id"] == "discord:111"
    assert message.reactions == [("add", "👀"), ("add", "✅"), ("remove", "👀")]


def test_follow_up_in_subscribed_thread_without_mention_uses_history() -> None:
    api = FakeApi()
    bot = responder(api)
    run(bot.answer(FakeMessage(f"<@{BOT}> 연차휴가는 며칠이야?", FakeChannel(500), mentions=[BOT], message_id=42)))
    thread = FakeChannel(42, parent_id=500, history=[
        SimpleNamespace(author=author(BOT, bot=True), content="연차휴가는 15일입니다."),
        SimpleNamespace(author=author(111), content="그럼 반차는?"),
    ])
    thread.starter_message = SimpleNamespace(author=author(111), content=f"<@{BOT}> 연차휴가는 며칠이야?")
    follow = FakeMessage("그럼 반차는?", thread)
    assert bot.should_answer(follow)
    run(bot.answer(follow))
    sent = json.loads(api.find("/v1/chat/completions")[-1].content)["messages"]
    assert sent == [{"role": "user", "content": "연차휴가는 며칠이야?"},
                    {"role": "assistant", "content": "연차휴가는 15일입니다."},
                    {"role": "user", "content": "그럼 반차는?"}]
    assert follow.thread is None  # answered in place, no new thread


def test_ignores_plain_messages_and_bots() -> None:
    bot = responder(FakeApi())
    assert not bot.should_answer(FakeMessage("안녕", FakeChannel(500)))
    assert not bot.should_answer(FakeMessage("안녕", FakeChannel(77, parent_id=500)))  # thread not subscribed
    other_bot = FakeMessage(f"<@{BOT}> hi", FakeChannel(500), mentions=[BOT])
    other_bot.author = author(5, bot=True)
    assert not bot.should_answer(other_bot)


def test_api_error_is_reported() -> None:
    bot = responder(FakeApi(fail=True))
    message = FakeMessage(f"<@{BOT}> 질문", FakeChannel(500), mentions=[BOT])
    run(bot.answer(message))
    assert message.thread.sent[0].edits[-1]["content"].startswith("❌ 답변을 만들지 못했습니다 (HTTP 502)")
    assert ("add", "❌") in message.reactions


def test_empty_mention_gets_usage_hint() -> None:
    message = FakeMessage(f"<@{BOT}>", FakeChannel(500), mentions=[BOT])
    run(responder(FakeApi()).answer(message))
    assert "@봇 질문" in message.replies[0]["content"]


def test_formatting_helpers() -> None:
    assert strip_mentions("<@123> <@!45> <@&67> 질문") == "질문"
    assert quote_text("t\n> 작성일: 2026\n본문") == "t 본문"
    assert "근거 문서를 찾지 못했습니다" in sources_embed(Answer("x"), {}, 4)["description"]
    history = [SimpleNamespace(author=author(BOT, bot=True), content="첫 인사"),
               SimpleNamespace(author=author(7, bot=True), content="다른 봇"),
               SimpleNamespace(author=author(111), content="질문"),
               SimpleNamespace(author=author(BOT, bot=True), content=PENDING_TEXT)]
    assert thread_messages(history, BOT, 10) == [{"role": "user", "content": "질문"}]


# --------------------------------------------------------------------------- proactive desk


def make_desk(tmp_path, api: FakeApi, **overrides):
    bot = responder(api, show_snippets=False)
    desk_channel = FakeChannel(700)
    origin_thread = FakeChannel(501, parent_id=500)
    channels = {700: desk_channel, 501: origin_thread}

    async def lookup(channel_id: int):
        return channels[channel_id]

    config = ProactiveConfig(channel=700, state_path=str(tmp_path / "state.json"), **overrides)
    return GapDesk(config, bot, ProactiveState(config.state_path), lookup), desk_channel, origin_thread


def test_desk_asks_once_with_thread_and_dismiss_button(tmp_path) -> None:
    api = FakeApi()
    desk, desk_channel, _ = make_desk(tmp_path, api, max_per_run=5, mention="@here")
    assert run(desk.run_once(now=datetime(2026, 1, 5, 10))) == 2
    first = desk_channel.sent[0]
    assert first.content.startswith("@here 🙋") and "주차" in first.content  # most frequent first
    assert first.kwargs["view"] == [{"label": "해당 없음", "custom_id": "baryon:dismiss:gap_b", "style": "secondary"}]
    assert desk_channel.threads[0].name.startswith("답: 주차")
    assert api.find("/v1/admin/gaps")[0].headers["x-roles"] == "admin"
    again, _, _ = make_desk(tmp_path, api, max_per_run=5)
    assert run(again.run_once(now=datetime(2026, 1, 5, 11))) == 0


def test_quiet_hours_and_discord_only(tmp_path) -> None:
    api = FakeApi()
    desk, _, _ = make_desk(tmp_path, api, hours="9-18", weekdays_only=True)
    assert run(desk.run_once(now=datetime(2026, 1, 10, 10))) == 0  # Saturday
    assert api.requests == []
    only, desk_channel, _ = make_desk(tmp_path, api, max_per_run=5, platform_only=True)
    assert run(only.run_once(now=datetime(2026, 1, 5, 10))) == 1
    assert "구내식당" in desk_channel.sent[0].content and "<@111>" in desk_channel.sent[0].content


def test_answer_publish_reanswers_once_in_original_thread(tmp_path) -> None:
    api = FakeApi()
    desk, desk_channel, origin = make_desk(tmp_path, api, max_per_run=5)
    run(desk.run_once(now=datetime(2026, 1, 5, 10)))
    gap_a_thread = desk_channel.threads[1]  # gap_b asked first, then gap_a

    reply = FakeMessage("평일 11시 30분부터 엽니다.", gap_a_thread, user=222)
    assert desk.answer_gap_id(reply) == "gap_a"
    assert desk.answer_gap_id(FakeMessage(f"<@{BOT}> 질문", gap_a_thread, mentions=[BOT])) is None
    run(desk.on_answer(reply, "gap_a"))
    saved = json.loads(api.find("/v1/tacit/entries")[0].content)
    assert saved == {"question": "구내식당은 몇 시에 열어?", "answer": "평일 11시 30분부터 엽니다.", "gap_id": "gap_a", "source": "discord"}
    buttons = reply.replies[0]["view"]
    assert buttons[0]["custom_id"] == "baryon:publish:tac_1:gap_a" and buttons[0]["style"] == "success"

    draft = Sent(gap_a_thread, "draft")
    ephemeral: list[str] = []

    async def respond(text):
        ephemeral.append(text)

    assert run(desk.on_button("baryon:publish:tac_1:gap_a", 222, draft, respond))
    assert api.find("/publish")[0].headers["x-user-id"] == "discord:222"
    assert draft.edits[-1] == {"content": "✅ <@222> 님 답변을 문서로 등록했습니다. 고마워요!", "view": None}
    assert desk_channel.sent[1].edits[-1]["content"].startswith("✅ **답이 등록됐어요**")
    assert origin.sent[-1].content.startswith("<@111> 지난번에 답하지 못했던 질문")
    assert json.loads(api.find("/v1/chat/completions")[0].content)["thread_id"] == "discord:500:501"

    run(desk.on_button("baryon:publish:tac_2:gap_a", 222, Sent(gap_a_thread, "draft2"), respond))
    assert len(origin.sent) == 1  # second publish does not re-answer again
    assert ephemeral == []


def test_only_author_can_publish_and_dismiss(tmp_path) -> None:
    api = FakeApi(publish_status=403)
    desk, desk_channel, _ = make_desk(tmp_path, api, max_per_run=1)
    run(desk.run_once(now=datetime(2026, 1, 5, 10)))
    ephemeral: list[str] = []

    async def respond(text):
        ephemeral.append(text)

    draft = Sent(desk_channel, "draft")
    run(desk.on_button("baryon:publish:tac_1:gap_b", 333, draft, respond))
    assert ephemeral == ["답변을 쓴 사람만 등록할 수 있어요."] and draft.edits == []

    post = desk_channel.sent[0]
    run(desk.on_button("baryon:dismiss:gap_b", 333, post, respond))
    assert api.find("/v1/admin/gaps/gap_b/dismiss")
    assert desk.state.get("gap_b")["status"] == "dismissed"
    assert post.edits[-1]["content"].startswith("➖ <@333>")
    assert run(desk.on_button("other:thing", 1, post, respond)) is False


def test_ask_text_for_non_discord_gap() -> None:
    text = ask_text(GAPS[1])
    assert "누군가 님이 물어봄 · 3번 나온 질문 · 답을 찾지 못함" in text
