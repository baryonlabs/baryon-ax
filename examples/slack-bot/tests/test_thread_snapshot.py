"""Thread snapshots: every page, edits in place, deletions as tombstones, reactions, held unfinished messages,
and the archive written from them. Every id and name here is invented."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baryon_slack_bot import BaryonClient, Config  # noqa: E402
from conversation_archive import ArchiveConfig, ConversationArchive  # noqa: E402
from slack_text import HUMAN, OTHER_BOT, SELF_BOT, UserDirectory  # noqa: E402
from thread_snapshot import (  # noqa: E402
    EDITED, READY, TIMEOUT, HoldConfig, SnapshotStore, ThreadSnapshot, TruncationHold, fetch_replies,
)

ME, MY_BOT = "UTESTME0001", "BTESTME0001"
CHANNEL = "CTEST000001"
PARENT = "1795830000.000100"  # 2026-11-28 KST
USERS = {"UTESTKIM001": "kim", "UTESTLEE001": "lee", "UTESTPARK01": "park"}


def reply(n: int, user: str = "UTESTLEE001", text: str = "") -> dict:
    return {"ts": f"{1795830000 + n}.000200", "thread_ts": PARENT, "user": user, "text": text or f"답글 {n}번"}


class Slack:
    """conversations.replies with real paging (cursor, has_more), history, users.info and reactions.get."""

    def __init__(self, messages: list[dict], page_size_cap: int = 1000) -> None:
        self.messages = messages
        self.cap = page_size_cap
        self.reply_calls: list[dict] = []
        self.reactions: dict[str, list[dict]] = {}

    def conversations_replies(self, channel: str = "", ts: str = "", limit: int = 100, cursor: str = "", **_):
        self.reply_calls.append({"ts": ts, "limit": limit, "cursor": cursor})
        thread = [m for m in self.messages if m["ts"] == ts or m.get("thread_ts") == ts]
        parent, rest = thread[:1], thread[1:]
        start = int(cursor or 0)
        size = min(limit, self.cap)
        page = rest[start:start + size]
        more = start + size < len(rest)
        return {"ok": True, "messages": parent + page, "has_more": more,
                "response_metadata": {"next_cursor": str(start + size) if more else ""}}

    def conversations_history(self, **_):
        parents = [dict(m, reply_count=sum(1 for r in self.messages if r.get("thread_ts") == m["ts"] and r["ts"] != m["ts"]))
                   for m in self.messages if not m.get("thread_ts") or m.get("thread_ts") == m["ts"]]
        return {"ok": True, "messages": list(reversed(parents))}

    def conversations_info(self, **_):
        return {"ok": True, "channel": {"name": "plans"}}

    def users_info(self, user: str = "", **_):
        if user not in USERS:
            return {"ok": False}
        return {"ok": True, "user": {"id": user, "name": USERS[user], "profile": {"display_name": USERS[user]}}}

    def reactions_get(self, channel: str = "", timestamp: str = "", **_):
        return {"ok": True, "message": {"ts": timestamp, "reactions": self.reactions.get(timestamp, [])}}


def thread(n_replies: int = 3) -> list[dict]:
    return [{"ts": PARENT, "thread_ts": PARENT, "user": "UTESTKIM001", "text": "워크숍 장소는 3층으로 하죠"}] + [
        reply(i + 1) for i in range(n_replies)]


def snapshot_of(messages: list[dict], **kwargs) -> ThreadSnapshot:
    web = Slack(messages, **kwargs)
    return ThreadSnapshot.fetch(web, CHANNEL, PARENT, UserDirectory.from_web(web), ME, MY_BOT)


# --------------------------------------------------------------------------- pages


def test_a_thread_longer_than_one_page_is_read_to_the_end_without_repeating_its_first_message() -> None:
    web = Slack(thread(120))
    found = fetch_replies(web, CHANNEL, PARENT, page_size=50)
    assert len(found) == 121 and found[0]["ts"] == PARENT and found[-1]["text"] == "답글 120번"
    assert [c["cursor"] for c in web.reply_calls] == ["", "50", "100"]
    assert len({m["ts"] for m in found}) == 121


def test_a_server_that_caps_the_page_size_is_still_read_in_full() -> None:
    snapshot = snapshot_of(thread(51), page_size_cap=20)  # asked for 200, Slack sends 20 at a time
    assert len(snapshot) == 52 and snapshot.messages[-1].text_plain == "답글 51번" and snapshot.complete


# --------------------------------------------------------------------------- edits, deletions


def test_an_edit_replaces_the_message_at_its_original_ts_and_keeps_its_author() -> None:
    snapshot = snapshot_of(thread(2))
    changed = snapshot.apply({
        "type": "message", "subtype": "message_changed", "channel": CHANNEL, "ts": "1795920000.000000",
        "message": {"ts": PARENT, "user": "UTESTKIM001", "text": "워크숍 장소는 5층으로 하죠", "edited": {"ts": "1795920000.000000"}},
        "previous_message": {"ts": PARENT, "user": "UTESTKIM001", "text": "워크숍 장소는 3층으로 하죠"}})
    assert changed is not None and changed.ts == PARENT and changed.edited
    assert snapshot.get(PARENT).text_plain == "워크숍 장소는 5층으로 하죠"
    assert snapshot.get(PARENT).author.kind == HUMAN and snapshot.get(PARENT).author.name == "kim"
    assert [m.ts for m in snapshot.messages][0] == PARENT  # still first: the edit time does not move it
    assert "3층" not in " ".join(m.text_plain for m in snapshot.messages)


def test_an_edit_event_without_bot_id_does_not_turn_a_bot_post_into_a_persons() -> None:
    messages = thread(0) + [
        {"ts": "1795830010.000200", "thread_ts": PARENT, "user": "UTESTOTHBOT", "bot_id": "BTESTOTHER1",
         "bot_profile": {"name": "helper-bot"}, "text": "정리했어요"},
        {"ts": "1795830020.000200", "thread_ts": PARENT, "user": ME, "bot_id": MY_BOT, "text": "확인할게요"}]
    snapshot = snapshot_of(messages)
    for ts, user in (("1795830010.000200", "UTESTOTHBOT"), ("1795830020.000200", ME)):
        snapshot.apply({"type": "message", "subtype": "message_changed", "channel": CHANNEL,
                        "message": {"ts": ts, "thread_ts": PARENT, "user": user, "text": "고친 글"}})
    assert snapshot.get("1795830010.000200").author.kind == OTHER_BOT
    assert snapshot.get("1795830010.000200").author.name == "helper-bot"
    mine = snapshot.get("1795830020.000200")
    assert mine.author.kind == SELF_BOT and mine.from_me and mine.edited


def test_a_deleted_message_stays_as_an_empty_tombstone_with_its_author() -> None:
    snapshot = snapshot_of(thread(2))
    gone_ts = reply(1)["ts"]
    snapshot.apply({"type": "message", "subtype": "message_deleted", "channel": CHANNEL, "ts": "1795920000.0",
                    "deleted_ts": gone_ts, "previous_message": reply(1)})
    gone = snapshot.get(gone_ts)
    assert gone.deleted and gone.text_plain == "" and gone.text_for_me == "" and gone.tokens == []
    assert gone.author.name == "lee" and gone.author.kind == HUMAN
    assert gone_ts not in [m.ts for m in snapshot.visible()] and len(snapshot) == 3
    # an edit that arrives after the delete changes nothing
    assert snapshot.apply({"type": "message", "subtype": "message_changed", "channel": CHANNEL,
                           "message": {"ts": gone_ts, "thread_ts": PARENT, "user": "UTESTLEE001", "text": "부활"}}) is None
    assert snapshot.get(gone_ts).deleted


def test_a_parent_deleted_while_it_has_replies_arrives_as_a_tombstone_edit() -> None:
    snapshot = snapshot_of(thread(1))
    snapshot.apply({"type": "message", "subtype": "message_changed", "channel": CHANNEL,
                    "message": {"ts": PARENT, "thread_ts": PARENT, "subtype": "tombstone", "text": "This message was deleted."}})
    assert snapshot.get(PARENT).deleted and snapshot.get(PARENT).text_plain == ""
    assert snapshot.get(PARENT).author.name == "kim"


def test_a_new_reply_is_added_and_a_late_duplicate_does_not_undo_an_edit() -> None:
    snapshot = snapshot_of(thread(1))
    new = reply(5, text="새 답글")
    assert snapshot.apply({"type": "message", "channel": CHANNEL, **new}).text_plain == "새 답글"
    snapshot.apply({"type": "message", "subtype": "message_changed", "channel": CHANNEL,
                    "message": {**new, "text": "고친 답글"}})
    assert snapshot.apply({"type": "message", "channel": CHANNEL, **new}) is None
    assert snapshot.get(new["ts"]).text_plain == "고친 답글"
    other_thread = {"type": "message", "channel": CHANNEL, "ts": "1795839999.0", "thread_ts": "1795839000.0", "user": "UTESTLEE001"}
    assert snapshot.apply(other_thread) is None


# --------------------------------------------------------------------------- reactions


def test_reactions_count_people_and_disappear_at_zero() -> None:
    messages = thread(0)
    messages[0]["reactions"] = [{"name": "+1", "users": ["UTESTLEE001"], "count": 1}]
    snapshot = snapshot_of(messages)

    def react(kind: str, name: str, user: str) -> None:
        snapshot.apply({"type": kind, "user": user, "reaction": name, "item": {"type": "message", "channel": CHANNEL, "ts": PARENT}})

    react("reaction_added", "+1", "UTESTPARK01")
    react("reaction_added", "+1", "UTESTPARK01")  # replayed: counted once
    react("reaction_added", "white_check_mark", "UTESTKIM001")
    first = snapshot.get(PARENT)
    assert [(r.name, r.count, r.users) for r in first.reactions] == [
        ("+1", 2, ("UTESTLEE001", "UTESTPARK01")), ("white_check_mark", 1, ("UTESTKIM001",))]
    react("reaction_removed", "white_check_mark", "UTESTKIM001")
    react("reaction_removed", "+1", "UTESTLEE001")
    assert [(r.name, r.count, r.users) for r in snapshot.get(PARENT).reactions] == [("+1", 1, ("UTESTPARK01",))]
    assert snapshot.get(PARENT).author.kind == HUMAN and snapshot.get(PARENT).text_plain.startswith("워크숍")


def test_reactions_get_reads_them_again() -> None:
    web = Slack(thread(0))
    snapshot = ThreadSnapshot.fetch(web, CHANNEL, PARENT, UserDirectory.from_web(web), ME)
    web.reactions[PARENT] = [{"name": "eyes", "users": ["UTESTLEE001", "UTESTPARK01"], "count": 2}]
    assert [(r.name, r.count) for r in snapshot.refresh_reactions(web, PARENT).reactions] == [("eyes", 2)]


# --------------------------------------------------------------------------- the store


def test_the_store_routes_every_event_kind_to_its_thread_and_ignores_threads_it_never_read() -> None:
    web = Slack(thread(2))
    store = SnapshotStore(UserDirectory.from_web(web), ME, MY_BOT)
    snapshot = store.get(web, CHANNEL, PARENT)
    assert store.get(web, CHANNEL, PARENT) is snapshot and len(web.reply_calls) == 1  # cached
    target = reply(2)["ts"]
    assert store.on_event({"type": "reaction_added", "user": "UTESTKIM001", "reaction": "+1",
                           "item": {"channel": CHANNEL, "ts": target}}) is snapshot  # found by its ts alone
    assert store.on_event({"type": "message", "subtype": "message_deleted", "channel": CHANNEL,
                           "deleted_ts": target, "previous_message": reply(2)}) is snapshot
    assert snapshot.get(target).deleted
    assert store.on_event({"type": "message", "channel": CHANNEL, "ts": "1795839999.0", "thread_ts": "1795839000.0",
                           "user": "UTESTLEE001", "text": "다른 스레드"}) is None
    started = store.on_event({"type": "message", "channel": CHANNEL, "ts": "1795840000.0", "user": "UTESTLEE001", "text": "새 글"})
    assert started is not None and started.complete and started.thread_ts == "1795840000.0"


def test_the_store_forgets_the_least_recently_used_thread() -> None:
    store = SnapshotStore(max_threads=2)
    for n in range(3):
        store.on_event({"type": "message", "channel": CHANNEL, "ts": f"17958400{n}0.0", "user": "UTESTLEE001", "text": "글"})
    assert store.cached(CHANNEL, "1795840000.0") is None and store.cached(CHANNEL, "1795840020.0") is not None
    assert store.thread_of(CHANNEL, "1795840000.0") == ""


# --------------------------------------------------------------------------- unfinished messages


class Timers:
    def __init__(self) -> None:
        self.started: list[list] = []  # [seconds, callback, cancelled]

    def __call__(self, seconds, callback):
        entry = [seconds, callback, False]
        self.started.append(entry)

        class Handle:
            def cancel(self_inner) -> None:
                entry[2] = True

        return Handle()

    def fire(self) -> None:
        for entry in self.started:
            if not entry[2]:
                entry[2] = True
                entry[1]()


def held_store(wait: float = 20.0):
    calls: list[tuple[str, str, str]] = []
    timers = Timers()
    hold = TruncationHold(lambda channel, message, reason: calls.append((message.ts, message.text_plain, reason)),
                          HoldConfig(wait_seconds=wait), timer=timers)
    return SnapshotStore(me=ME, my_bot_id=MY_BOT, hold=hold), hold, timers, calls


def test_a_person_s_unfinished_message_waits_for_an_edit_and_is_handed_on_edited() -> None:
    store, hold, timers, calls = held_store()
    store.on_event({"type": "message", "channel": CHANNEL, "ts": PARENT, "user": "UTESTKIM001", "text": "이 기능은 baryon 가"})
    assert hold.held(CHANNEL, PARENT) and calls == [] and timers.started[0][0] == 20.0
    store.on_event({"type": "message", "subtype": "message_changed", "channel": CHANNEL,
                    "message": {"ts": PARENT, "user": "UTESTKIM001", "text": "이 기능은 baryon 가 먼저 읽어야 해요"}})
    assert calls == [(PARENT, "이 기능은 baryon 가 먼저 읽어야 해요", EDITED)] and timers.started[0][2]
    timers.fire()
    assert len(calls) == 1  # the cancelled timer does not hand it on twice


def test_an_unfinished_message_nobody_edits_is_handed_on_after_the_wait_and_a_deleted_one_never() -> None:
    store, hold, timers, calls = held_store()
    store.on_event({"type": "message", "channel": CHANNEL, "ts": PARENT, "user": "UTESTKIM001", "text": "그리고"})
    later = reply(1, user="UTESTLEE001", text="일정은 내일까지")
    store.on_event({"type": "message", "channel": CHANNEL, **{**later, "text": "일정은 내일까지,"}})
    store.on_event({"type": "message", "subtype": "message_deleted", "channel": CHANNEL, "deleted_ts": later["ts"],
                    "previous_message": later})
    timers.fire()
    assert calls == [(PARENT, "그리고", TIMEOUT)]


def test_finished_messages_bot_posts_and_a_zero_wait_are_handed_on_at_once() -> None:
    store, hold, timers, calls = held_store()
    store.on_event({"type": "message", "channel": CHANNEL, "ts": PARENT, "user": "UTESTKIM001", "text": "회의는 3시에 해요"})
    store.on_event({"type": "message", "channel": CHANNEL, "ts": "1795830001.0", "thread_ts": PARENT,
                    "user": "UTESTOTHBOT", "bot_id": "BTESTOTHER1", "text": "정리하면 baryon 가"})
    assert [reason for *_, reason in calls] == [READY, READY] and not timers.started
    store, hold, timers, calls = held_store(wait=0)
    store.on_event({"type": "message", "channel": CHANNEL, "ts": PARENT, "user": "UTESTKIM001", "text": "이건 baryon 가"})
    assert calls == [(PARENT, "이건 baryon 가", READY)]


def test_hold_wait_comes_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_TRUNCATION_WAIT_SECONDS", "45")
    assert HoldConfig.from_env().wait_seconds == 45.0
    monkeypatch.setenv("SLACK_TRUNCATION_WAIT_SECONDS", "x")
    assert HoldConfig.from_env().wait_seconds == 20.0


# --------------------------------------------------------------------------- the archive, from snapshots


class Api:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.content.decode("utf-8", "replace")))
        if request.method == "POST":
            return httpx.Response(200, json={"id": f"doc{len(self.calls)}"})
        return httpx.Response(200, json={"deleted": True})

    @property
    def posted(self) -> list[str]:
        return [body for method, _, body in self.calls if method == "POST"]


def archive(api: Api, tmp_path) -> ConversationArchive:
    config = Config(api_url="http://api.test", organization_id="org", roles="member")
    baryon = BaryonClient(config, http=httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(api.handler)))
    return ConversationArchive(ArchiveConfig(every_channel=True, state_path=str(tmp_path / "state.json")), baryon, ME)


def test_the_archive_holds_one_section_per_thread_with_every_page_and_no_deleted_messages(tmp_path) -> None:
    messages = thread(60) + [
        {"ts": "1795833600.000100", "user": "UTESTPARK01", "text": "점심 메뉴 정했어요"},
        {"ts": "1795833700.000100", "user": "UTESTLEE001", "text": "좋아요"},
        {"ts": "1795837200.000100", "thread_ts": "1795837200.000100", "user": "UTESTPARK01", "text": "예산은 2만원"},
        {"ts": "1795837260.000200", "thread_ts": "1795837200.000100", "user": "UTESTKIM001", "text": "동의합니다",
         "reactions": [{"name": "+1", "users": ["UTESTLEE001", "UTESTPARK01"], "count": 2}]},
    ]
    gone = messages.pop(10)  # deleted reply: Slack no longer returns it
    messages[3] = {**messages[3], "text": "고친 답글", "edited": {"ts": "1795930000.0"}}
    web = Slack(messages, page_size_cap=25)
    api = Api()
    keep = archive(api, tmp_path)
    assert keep.write_day(web, CHANNEL, "2026-11-28", private=False)
    body = api.posted[0]
    assert body.count("## 스레드 ") == 2 and body.count("## 채널 대화 ") == 1
    assert "## 스레드 10:40 · kim: 워크숍 장소는 3층으로 하죠" in body
    assert "답글 60번" in body and "답글 59번" in body and gone["text"] == "답글 10번" and "답글 10번" not in body
    assert "고친 답글 (수정됨)" in body and "동의합니다 (반응: :+1: 2)" in body
    assert "## 채널 대화 11:40–11:41" in body and body.index("점심 메뉴") < body.index("예산은 2만원")


def test_a_deletion_rewrites_the_day_it_was_posted_and_an_emptied_day_loses_its_document(tmp_path) -> None:
    messages = [{"ts": PARENT, "user": "UTESTKIM001", "text": "견적은 30일 유효"}]
    web = Slack(messages)
    api = Api()
    keep = archive(api, tmp_path)
    assert keep.observe({"channel": CHANNEL, "channel_type": "channel", "user": "UTESTKIM001", "ts": PARENT})
    assert keep.flush(web, force=True) == 1
    messages.clear()  # the person deleted it two days later
    deleted = {"type": "message", "subtype": "message_deleted", "channel": CHANNEL, "channel_type": "channel",
               "ts": "1796000000.000000", "deleted_ts": PARENT, "previous_message": {"ts": PARENT, "user": "UTESTKIM001"}}
    assert keep.observe(deleted) and list(keep.pending) == [(CHANNEL, "2026-11-28")]
    keep.flush(web, force=True)
    assert api.calls[-1][:2] == ("DELETE", "/v1/documents/doc1") and f"{CHANNEL}:2026-11-28" not in keep.state


def test_an_edit_counts_for_the_day_the_message_was_posted_and_bot_edits_are_ignored(tmp_path) -> None:
    keep = archive(Api(), tmp_path)
    edit = {"type": "message", "subtype": "message_changed", "channel": CHANNEL, "channel_type": "channel",
            "ts": "1796000000.000000", "message": {"ts": reply(1)["ts"], "thread_ts": PARENT, "user": "UTESTLEE001", "text": "x"}}
    assert keep.observe(edit) and list(keep.pending) == [(CHANNEL, "2026-11-28")]
    keep.pending.clear()
    bot_edit = {**edit, "message": {**edit["message"], "user": ME, "bot_id": MY_BOT}}
    assert not keep.observe(bot_edit) and not keep.pending


def test_a_reaction_rewrites_a_day_already_archived_with_its_privacy(tmp_path) -> None:
    keep = archive(Api(), tmp_path)
    reaction = {"type": "reaction_added", "user": "UTESTLEE001", "reaction": "+1", "item": {"channel": CHANNEL, "ts": reply(1)["ts"]}}
    assert not keep.observe_reaction(reaction, PARENT)  # never archived: nothing to rewrite
    keep.state[f"{CHANNEL}:2026-11-28"] = {"document_id": "doc9", "private": True}
    assert keep.observe_reaction(reaction, PARENT) and keep.pending[(CHANNEL, "2026-11-28")].private
