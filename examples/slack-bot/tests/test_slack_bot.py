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
from slack_text import UserDirectory  # noqa: E402

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


def test_direct_messages_are_answered_without_mention() -> None:
    requests: list[httpx.Request] = []
    bot, web = responder(requests), FakeWeb()
    dm = {"channel": "D1", "channel_type": "im", "user": "U1", "ts": "100.1", "text": "연차휴가는 며칠이야?"}
    assert bot.is_direct_message(dm)
    assert bot.is_direct_message({**dm, "subtype": "file_share"})
    assert not bot.should_follow_up({**dm, "thread_ts": "100.1"})  # never answered twice
    assert not bot.is_direct_message({**dm, "bot_id": "B"})
    assert not bot.is_direct_message({**dm, "subtype": "message_changed"})
    assert not bot.is_direct_message({**dm, "channel_type": "channel"})

    bot.answer(dm, web)
    assert json.loads(requests[0].content)["thread_id"] == "slack:D1:100.1"
    assert web.named("post")[0]["thread_ts"] == "100.1"

    empty = FakeWeb()
    bot.answer({**dm, "text": "  "}, empty)
    assert "@봇" not in empty.named("post")[0]["text"]

    file_only = FakeWeb()
    bot.answer({**dm, "text": "", "subtype": "file_share", "files": [{"id": "F1", "name": "a.xlsx"}]}, file_only)
    assert not file_only.calls  # waits for the question that follows

    bot.config.direct_messages = False
    assert not bot.is_direct_message(dm)


def long_answer_api(requests: list[httpx.Request], answer: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"id": "trace-2", "choices": [{"message": {"content": answer}}],
                                             "citations": [{"document_id": "rfp", "title": "rfp.md", "chunk": 46}]})
        if request.url.path == "/v1/documents/rfp":
            assert request.url.params["chunk"] == "46"
            return httpx.Response(200, json={"cited_chunk": {"number": 46, "content": "REQ-007 분석 에이전트 (Text-to-SQL 엔진)"}})
        return httpx.Response(200, json={"data": []})  # the follow-up's own words find nothing
    return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))


def test_long_answer_is_split_and_text_stays_under_the_limit() -> None:
    from baryon_slack_bot import SECTION_LIMIT, TEXT_LIMIT, answer_sections

    answer = "\n\n".join(f"{n}) " + "복합 질의 처리 요건 " * 40 for n in range(1, 12))  # about 9,000 characters
    parts = answer_sections(answer)
    assert len(parts) > 2 and all(len(p) <= SECTION_LIMIT for p in parts)
    assert " ".join(parts).split() == answer.split()  # nothing lost, only the breaks moved

    requests: list[httpx.Request] = []
    config = Config(api_url="http://api.test", organization_id="org", roles="member")
    bot, web = Responder(config, BaryonClient(config, http=long_answer_api(requests, answer)), BOT), FakeWeb()
    bot.answer({"channel": "C1", "user": "U1", "ts": "100.1", "text": "<@UBOT> 다시 해봐"}, web)
    update = web.named("update")[0]
    assert len(update["text"]) <= TEXT_LIMIT  # msg_too_long otherwise
    sections = [b for b in update["blocks"] if b["type"] == "section"]
    assert len(sections) == len(parts) + 1  # answer parts + sources
    # the cited chunk is quoted even though a search on "다시 해봐" did not return it
    assert "> REQ-007 분석 에이전트 (Text-to-SQL 엔진)" in sections[-1]["text"]["text"]


def test_question_with_attachment_explains_how_to_read_the_file() -> None:
    web = FakeWeb()
    responder([]).answer({"channel": "C1", "user": "U1", "ts": "100.1", "text": "<@UBOT> 이 문서 안에 설명되어 있는지 확인하라",
                          "files": [{"id": "F1", "name": "rfp.hwp"}]}, web)
    assert "문서로 추가하고 분석해줘" in str(web.named("update")[0]["blocks"])
    plain = FakeWeb()
    responder([]).answer({"channel": "C1", "user": "U1", "ts": "100.1", "text": "<@UBOT> 연차는?"}, plain)
    assert "문서로 추가하고 분석해줘" not in str(plain.named("update")[0]["blocks"])


class LinkWeb(FakeWeb):
    def conversations_replies(self, **kwargs):
        self.calls.append(("replies", kwargs))
        if kwargs["channel"] == "CLOCKED":
            raise RuntimeError("not_in_channel")
        return {"messages": [{"user": "U7", "text": "회의 메모: 일정 점검 필요"}, {"user": "U8", "text": "<@UBOT> 다음 주까지 계획 정리"}]}


def test_slack_links_are_expanded_or_marked_unreadable() -> None:
    from baryon_slack_bot import FOREIGN_LINK_NOTE, UNREADABLE_LINK_NOTE, slack_domain

    assert slack_domain("https://acme.slack.com/") == "acme"
    bot, web = responder([]), LinkWeb()
    bot.team_domain = "acme"
    messages = [
        {"role": "user", "content": "이것 봐 <https://acme.slack.com/archives/C1/p1795410004281229?thread_ts=1795410000.000100|링크>"},
        {"role": "assistant", "content": "https://acme.slack.com/archives/C1/p1795410004281229"},
        {"role": "user", "content": "그리고 <https://other-org.slack.com/archives/C9/p1795407816704509|other> 와 "
                                    "https://acme.slack.com/archives/CLOCKED/p1795410004281229"},
    ]
    expanded, notes = bot.expand_links(web, messages)
    assert "회의 메모: 일정 점검 필요" in expanded[0]["content"] and "다음 주까지 계획 정리" in expanded[0]["content"]
    assert web.named("replies")[0]["ts"] == "1795410000.000100"  # the thread the link points into
    assert expanded[1] == messages[1]  # the bot's own turns are left alone
    assert "다른 Slack 워크스페이스(other-org)" in expanded[2]["content"]
    assert "[링크된 Slack 대화: 읽지 못함]" in expanded[2]["content"]
    assert notes == [FOREIGN_LINK_NOTE, UNREADABLE_LINK_NOTE]

    requests: list[httpx.Request] = []
    linked = responder(requests)
    linked.team_domain = "acme"
    answer_web = LinkWeb()
    linked.answer({"channel": "C1", "user": "U1", "ts": "1.0", "text": "<@UBOT> <https://other-org.slack.com/archives/C9/p1795407816704509|x> 분석해줘"},
                  answer_web)
    sent = json.loads(requests[0].content)["messages"][-1]["content"]
    assert "봇이 읽을 수 없음" in sent
    assert "다른 Slack 워크스페이스의 링크는" in str(answer_web.named("update")[0]["blocks"])


def test_strip_mentions_and_mrkdwn() -> None:
    # the bot's own mention goes; other people stay as "@이름" (slack_text.readable)
    assert strip_mentions("<@UBOT> 연차는 며칠? <@U123|kim>", me="UBOT") == "연차는 며칠? @kim"
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
    directory = UserDirectory(lambda uid: {"U1": {"profile": {"display_name": "kim"}}}.get(uid))
    # earlier turns say who spoke; the last one is the question being answered and stays bare
    assert thread_messages(replies, BOT, 10, directory) == [
        {"role": "user", "content": "kim: 연차는 며칠?"},
        {"role": "assistant", "content": "15일입니다."},
        {"role": "user", "content": "그럼 반차도 돼?"},
    ]
    assert thread_messages(replies, BOT, 2)[0]["role"] == "user"  # never starts with the assistant


def test_thread_messages_keep_other_bots_and_who_is_meant() -> None:
    replies = [
        {"user": "U1", "text": "<@UBOT> 는 <@U_OTHERBOT> 이 어떤일을 하고 있는지 알고 있나?", "ts": "1"},
        {"user": "U_OTHERBOT", "bot_id": "B9", "bot_profile": {"name": "orca-bot"}, "text": "작업 목록을 정리했어요.", "ts": "2"},
        {"user": "U1", "text": "<@U_OTHERBOT> 여기 내용이다", "ts": "3"},
    ]
    people = {"U1": {"profile": {"display_name": "owner"}}, "UBOT": {"profile": {"display_name": "baryon"}, "is_bot": True},
              "U_OTHERBOT": {"profile": {"display_name": "orca-bot"}, "is_bot": True}}
    messages = thread_messages(replies, BOT, 10, UserDirectory(people.get))
    assert messages == [
        {"role": "user", "content": "owner: @baryon 는 @orca-bot 이 어떤일을 하고 있는지 알고 있나?"},
        {"role": "user", "content": "orca-bot (봇): 작업 목록을 정리했어요."},  # another bot is not the assistant
        {"role": "user", "content": "@orca-bot 여기 내용이다"},
    ]


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


def test_guide_questions_are_told_apart_from_questions_about_other_things() -> None:
    from guide import wants_guide

    for question in ("설명서 알려줘", "사용법 자세히 설명해줘", "매뉴얼", "도움말", "뭘 할 수 있어?", "무슨 기능이 있어?",
                     "슬랙에서 파일은 어떻게 올려?", "바리온 비서 사용법", "MCP 어떻게 연결해?", "기능을 모두 설명해봐", "help"):
        assert wants_guide(question), question
    for question in ("법인카드 사용 방법", "프린터 매뉴얼 어디 있어", "연차휴가는 며칠이야?", "출장비 정산 방법 알려줘",
                     "VPN 접속 가이드", "회의실 예약 어떻게 해?", "ERP에 무슨 기능이 있어?"):
        assert not wants_guide(question), question


def test_a_guide_question_is_answered_from_the_whole_guide() -> None:
    import json as _json

    import httpx as _httpx

    from guide import Guide

    guide_text = "# Baryon AX 사용 안내\n## 슬랙 봇 사용법\n파일을 올리고 @baryon 문서로 추가\n## 음성 비서\n전화하듯 말하기"
    seen: list[dict] = []

    def handler(request: _httpx.Request) -> _httpx.Response:
        if request.url.path == "/v1/documents":
            assert request.url.params["tag"] == "guide"
            return _httpx.Response(200, json={"data": [{"id": "old", "created_at": "2026-11-01"}, {"id": "g1", "created_at": "2026-11-28"}]})
        if request.url.path == "/v1/documents/g1":
            return _httpx.Response(200, json={"title": "Baryon AX 사용 안내.md", "content": guide_text})
        if request.url.path == "/v1/chat/completions":
            seen.append(_json.loads(request.content))
            return _httpx.Response(200, json={"id": "t1", "choices": [{"message": {"content": "**슬랙 봇 사용법**\n1. 파일을 올립니다"}}]})
        return _httpx.Response(404)

    config = Config(api_url="http://api.test", organization_id="org", roles="member")
    baryon = BaryonClient(config, http=_httpx.Client(base_url="http://api.test", transport=_httpx.MockTransport(handler)))
    responder = Responder(config, baryon, "UBOT", guide=Guide(baryon))
    web = FakeWeb()
    responder.answer({"channel": "C1", "user": "U1", "ts": "1.0", "text": "<@UBOT> 설명서 자세히 알려줘"}, web)
    body = seen[0]
    assert body["use_retrieval"] is False and guide_text in body["messages"][0]["content"]
    assert "모든 장을 빠짐없이" in body["messages"][0]["content"]
    update = [kwargs for name, kwargs in web.calls if name == "update"][-1]
    rendered = _json.dumps(update["blocks"], ensure_ascii=False)
    assert "슬랙 봇 사용법" in rendered and "Baryon AX 사용 안내.md" in rendered and "사용 안내서 전체" in rendered


def test_long_answers_read_well_in_slack() -> None:
    from baryon_slack_bot import to_mrkdwn

    text = to_mrkdwn("### **1. 소개**\n*   **웹 채팅**: 접속\n- 둘\n| 증상 | 원인 |\n| :--- | :--- |\n| 못 받음 | 권한 |").replace("​", "")
    assert text == "*1. 소개*\n• *웹 채팅*: 접속\n• 둘\n증상 · 원인\n못 받음 · 권한"
