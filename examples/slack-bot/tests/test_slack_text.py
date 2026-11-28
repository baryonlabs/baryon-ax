"""Slack mrkdwn normalizer (slack_text.py). All ids and names here are made up."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slack_text import Message, UserDirectory, looks_truncated, readable, tokenize  # noqa: E402

ME = "U_SELF"
PEOPLE = {
    "U_SELF": {"profile": {"display_name": "baryon"}, "is_bot": True},
    "U_OTHERBOT": {"profile": {"display_name": "orca-bot"}, "is_bot": True},
    "U_OWNER": {"profile": {"display_name": "", "real_name": "오너"}},
    "U_KIM": {"name": "kim.dev", "profile": {}},
}


def directory() -> UserDirectory:
    return UserDirectory(PEOPLE.get)


# --------------------------------------------------------------------------- tokens

TOKEN_CASES = [
    ("plain", "그냥 글", [("text", "", "", "")]),
    ("user", "<@U123>", [("user", "U123", "", "")]),
    ("user with label", "<@U123|kim>", [("user", "U123", "kim", "")]),
    ("enterprise user", "<@W0ABC>", [("user", "W0ABC", "", "")]),
    ("channel", "<#C0AB|general>", [("channel", "C0AB", "general", "")]),
    ("channel without name", "<#C0AB>", [("channel", "C0AB", "", "")]),
    ("usergroup", "<!subteam^S0DEV|@dev-team>", [("usergroup", "S0DEV", "dev-team", "")]),
    ("here", "<!here>", [("special", "here", "", "")]),
    ("channel broadcast", "<!channel>", [("special", "channel", "", "")]),
    ("everyone with label", "<!everyone|everyone>", [("special", "everyone", "", "")]),
    ("link", "<https://x.test/a>", [("link", "", "", "https://x.test/a")]),
    ("link with label", "<https://x.test/a|문서>", [("link", "", "문서", "https://x.test/a")]),
    ("mailto", "<mailto:a@x.test|a@x.test>", [("link", "", "a@x.test", "mailto:a@x.test")]),
    ("emoji", ":eyes:", [("emoji", "", "", "")]),
    ("skin tone emoji", ":+1::skin-tone-2:", [("emoji", "", "", "")]),
    ("code", "`<@U1>`", [("code", "", "", "")]),
    ("code block", "```a\n<@U1>\n```", [("block", "", "", "")]),
    ("quote", "&gt; 인용", [("quote", "", "", ""), ("text", "", "", "")]),
]


@pytest.mark.parametrize("name,text,expected", TOKEN_CASES, ids=[c[0] for c in TOKEN_CASES])
def test_tokenize(name, text, expected) -> None:
    assert [(t.kind, t.id, t.label, t.url) for t in tokenize(text)] == expected


def test_time_is_not_an_emoji() -> None:
    assert [t.kind for t in tokenize("10:30:00 에 만나요")] == ["text"]


# --------------------------------------------------------------------------- readable text

READABLE_CASES = [
    # the real case (anonymized): the bot's own mention is part of the sentence, the other bot is the topic
    ("self is subject", "<@U_SELF> 는 <@U_OTHERBOT> 이 어떤일을 하고 있는지 알 알고 있나?",
     "너(baryon) 는 @orca-bot 이 어떤일을 하고 있는지 알 알고 있나?"),
    ("self only calls", "<@U_SELF> 연차는 며칠?", "너(baryon) 연차는 며칠?"),
    ("self at the end", "연차는 며칠? <@U_SELF>", "연차는 며칠? 너(baryon)"),
    ("other person kept", "<@U_SELF> <@U_KIM> 한테 물어봐", "너(baryon) @kim.dev 한테 물어봐"),
    ("self unknown name", "<@U_SELF> 확인", "너 확인"),
    ("real name fallback", "<@U_OWNER> 님 확인 부탁", "@오너 님 확인 부탁"),
    ("label when unknown", "<@U_NOBODY|park> 참고", "@park 참고"),
    ("id when nothing known", "<@U_NOBODY> 참고", "@U_NOBODY 참고"),
    ("channel name", "<#C0AB|sales> 에 올렸어요", "#sales 에 올렸어요"),
    ("usergroup", "<!subteam^S0DEV|@dev-team> 확인", "@dev-team 확인"),
    ("here", "<!here> 회의 시작", "@here 회의 시작"),
    ("link label kept", "자료는 <https://x.test/a|여기> 있어요", "자료는 여기 (https://x.test/a) 있어요"),
    ("bare link", "<https://x.test/a>", "https://x.test/a"),
    ("label equal to url", "<https://x.test/a|x.test/a>", "https://x.test/a"),
    ("mail", "<mailto:a@x.test|a@x.test> 로 보내", "a@x.test 로 보내"),
    ("entities", "a &lt; b &amp;&amp; c &gt; d", "a < b && c > d"),
    ("quote", "&gt; 원문\n답", "> 원문\n답"),
    ("code untouched", "`<@U_KIM>` 그대로", "`<@U_KIM>` 그대로"),
    ("date fallback", "<!date^1795865320^{date}|2026-11-28> 까지", "2026-11-28 까지"),
    ("emoji kept", "좋아요 :+1:", "좋아요 :+1:"),
]


@pytest.mark.parametrize("name,text,expected", READABLE_CASES, ids=[c[0] for c in READABLE_CASES])
def test_readable(name, text, expected) -> None:
    names = UserDirectory() if name == "self unknown name" else directory()
    assert readable(text, me=ME, directory=names) == expected


ROUTING_CASES = [  # drop_me: only for keyword routing; a mention inside the sentence still stays
    ("<@U_SELF> 문서로 추가", "문서로 추가"),
    ("<@U_SELF>", ""),
    ("<@U_SELF> 는 <@U_OTHERBOT> 을 알아?", "너(baryon) 는 @orca-bot 을 알아?"),
]


@pytest.mark.parametrize("text,expected", ROUTING_CASES)
def test_routing_text_drops_only_the_call(text, expected) -> None:
    assert readable(text, me=ME, directory=directory(), drop_me=True) == expected


def test_readable_without_me_keeps_every_mention() -> None:
    assert readable("<@U_SELF> 연차는?", directory=directory()) == "@baryon 연차는?"


# --------------------------------------------------------------------------- addressee

ADDRESS_CASES = [
    ("other bot only", "<@U_OTHERBOT> 여기 내용이다", False, False),
    ("me", "<@U_SELF> 연차는?", True, True),
    ("me and other", "<@U_SELF> <@U_KIM> 같이 봐줘", True, True),
    ("no mention", "그럼 반차도 돼?", False, True),
    ("here only", "<!here> 공지", False, True),
    ("usergroup only", "<!subteam^S0DEV|@dev> 확인", False, False),
    ("other person only", "<@U_KIM|kim> 이거 봐줘", False, False),
    ("two others first", "<@U_KIM> <@U_OTHERBOT> 확인해 주세요", False, False),
    ("other later in sentence", "그건 <@U_KIM> 한테 물어봐", False, True),
    ("me later than other", "<@U_OTHERBOT> 말고 <@U_SELF> 가 해줘", True, True),
]


@pytest.mark.parametrize("name,text,mentions_me,addressed", ADDRESS_CASES, ids=[c[0] for c in ADDRESS_CASES])
def test_addressed_to_me(name, text, mentions_me, addressed) -> None:
    message = Message.from_event({"user": "U_OWNER", "ts": "1.0", "text": text}, directory(), ME)
    assert (message.mentions_me, message.addressed_to_me) == (mentions_me, addressed)


# --------------------------------------------------------------------------- truncation hint

TRUNCATION_CASES = [
    ("ends with standalone particle", "<@U_SELF> 이 스레드를 읽고 baryon 가", True),
    ("latin word with particle", "이 기능은 API는", True),
    ("object particle", "우선 계약서를", True),
    ("conjunction", "견적은 보냈고 그리고", True),
    ("comma", "첫째,", True),
    ("mention after particle", "이 내용은 <@U_KIM>", True),
    ("mentions joined", "<@U_KIM> 랑 <@U_OWNER>", True),
    ("question", "연차는 며칠이야?", False),
    ("polite ending", "확인했습니다", False),
    ("casual ending", "정리해줘", False),
    ("trailing ping", "확인 부탁해요 <@U_KIM>", False),
    ("bare ping", "<@U_KIM>", False),
    ("noun ending", "내일 회의", False),
    ("question ending 가", "이게 맞는 건가", False),
    ("emoji end", "좋아요 :+1:", False),
    ("empty", "", False),
]


@pytest.mark.parametrize("name,text,expected", TRUNCATION_CASES, ids=[c[0] for c in TRUNCATION_CASES])
def test_looks_truncated(name, text, expected) -> None:
    assert looks_truncated(text) is expected


# --------------------------------------------------------------------------- Message


def test_message_from_event_collects_everything() -> None:
    event = {
        "user": "U_OWNER", "ts": "2.0", "thread_ts": "1.0",
        "text": "<@U_SELF> <https://x.test/a|안내서> 보고 <@U_KIM> 한테 전달해줘",
        "files": [{"id": "F1", "name": "단가표.xlsx", "mimetype": "application/vnd.ms-excel", "filetype": "xlsx"}],
        "reactions": [{"name": "eyes", "count": 2, "users": ["U_KIM", "U_OWNER"]}],
        "edited": {"user": "U_OWNER", "ts": "3.0"},
    }
    message = Message.from_event(event, directory(), ME)
    assert message.author.name == "오너" and not message.author.is_bot and not message.from_me
    assert message.text_plain == "@baryon 안내서 (https://x.test/a) 보고 @kim.dev 한테 전달해줘"
    assert message.text_for_me == "너(baryon) 안내서 (https://x.test/a) 보고 @kim.dev 한테 전달해줘"
    assert [m.id for m in message.addressees] == ["U_SELF"]
    assert [(m.id, m.name, m.is_bot, m.is_me) for m in message.mentions] == [
        ("U_SELF", "baryon", True, True), ("U_KIM", "kim.dev", False, False)]
    assert [(link.url, link.label) for link in message.links] == [("https://x.test/a", "안내서")]
    assert message.files[0].name == "단가표.xlsx" and message.reactions[0].count == 2
    assert message.edited and not message.deleted and message.thread_ts == "1.0"
    assert message.line() == "오너: 너(baryon) 안내서 (https://x.test/a) 보고 @kim.dev 한테 전달해줘"
    assert message.author.kind == "human"


AUTHOR_CASES = [
    ("person", {"user": "U_OWNER"}, "", "human", "오너"),
    ("self by user id", {"user": "U_SELF", "bot_id": "B_SELF"}, "", "self_bot", "baryon"),
    ("self by bot id", {"bot_id": "B_SELF", "subtype": "bot_message"}, "B_SELF", "self_bot", "B_SELF"),
    ("other bot keeps bot_profile name", {"user": "U_OTHERBOT", "bot_id": "B9", "app_id": "A9",
                                          "bot_profile": {"name": "orca-bot (app)", "app_id": "A9"}}, "", "other_bot", "orca-bot (app)"),
    ("bot user without bot_id", {"user": "U_OTHERBOT"}, "", "other_bot", "orca-bot"),
    ("webhook", {"subtype": "bot_message", "bot_id": "B8", "username": "deploy"}, "", "other_bot", "deploy"),
    ("app with a user token", {"user": "U_KIM", "app_id": "A7"}, "", "user_token_app", "kim.dev"),
]


@pytest.mark.parametrize("name,event,my_bot_id,kind,author", AUTHOR_CASES, ids=[c[0] for c in AUTHOR_CASES])
def test_author_kind(name, event, my_bot_id, kind, author) -> None:
    message = Message.from_event({"ts": "1", "text": "글", **event}, directory(), ME, my_bot_id=my_bot_id)
    assert (message.author.kind, message.author.name) == (kind, author)
    assert message.author.is_bot is (kind in ("self_bot", "other_bot"))
    assert message.from_me is (kind == "self_bot")


def test_bot_post_and_edit_and_delete() -> None:
    bot = Message.from_event({"user": "U_ANON", "bot_id": "B9", "bot_profile": {"name": "helper-bot"}, "ts": "1", "text": "정리했어요"},
                             UserDirectory(), ME)
    assert bot.author.is_bot and bot.author.name == "helper-bot" and bot.speaker == "helper-bot (봇)"
    hook = Message.from_event({"subtype": "bot_message", "bot_id": "B8", "username": "deploy", "ts": "1", "text": "배포 끝"})
    assert hook.author.is_bot and hook.author.id == "B8" and hook.line() == "deploy (봇): 배포 끝"
    changed = Message.from_event({"subtype": "message_changed", "ts": "5",
                                  "message": {"user": "U_KIM", "ts": "4", "text": "고친 글", "edited": {"ts": "5"}}})
    assert changed.edited and changed.ts == "4" and changed.text_plain == "고친 글"
    gone = Message.from_event({"subtype": "message_deleted", "ts": "6", "previous_message": {"user": "U_KIM", "ts": "4", "text": "지운 글"}})
    assert gone.deleted and gone.text_plain == "" and gone.ts == "4"
    tomb = Message.from_event({"subtype": "tombstone", "user": "USLACKBOT", "ts": "7", "text": "This message was deleted."})
    assert tomb.deleted


def test_message_truncation_hint_uses_mentions() -> None:
    message = Message.from_event({"user": "U_OWNER", "ts": "1", "text": "이 내용은 <@U_KIM>"}, directory(), ME)
    assert message.looks_truncated


# --------------------------------------------------------------------------- directory


def test_directory_caches_and_expires() -> None:
    calls: list[str] = []
    now = [0.0]

    def lookup(user_id):
        calls.append(user_id)
        return PEOPLE.get(user_id)

    names = UserDirectory(lookup, ttl=100, clock=lambda: now[0])
    assert names.name("U_KIM") == "kim.dev" and names.name("U_KIM") == "kim.dev"
    assert calls == ["U_KIM"]
    now[0] = 101
    assert names.name("U_KIM") == "kim.dev" and calls == ["U_KIM", "U_KIM"]


def test_directory_failure_keeps_the_id_or_the_label() -> None:
    def broken(user_id):
        raise RuntimeError("missing_scope")

    names = UserDirectory(broken)
    assert names.name("U_KIM") == "U_KIM" and not names.is_bot("U_KIM")
    assert readable("<@U_KIM|kim> 확인", directory=UserDirectory(broken)) == "@kim 확인"
    assert readable("<@U_KIM> 확인", directory=None) == "@U_KIM 확인"
