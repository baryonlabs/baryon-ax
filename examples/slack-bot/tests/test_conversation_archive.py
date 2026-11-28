"""Slack conversations become one document per channel per day."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baryon_slack_bot import BaryonClient, Config  # noqa: E402
from conversation_archive import ArchiveConfig, ConversationArchive  # noqa: E402

DAY_TS = "1795830000.000100"  # 2026-11-28 KST


class FakeWeb:
    def __init__(self) -> None:
        self.history = [
            {"ts": "1795830000.000100", "user": "U1", "text": "견적 유효기간은 30일로 하죠", "reply_count": 2, "thread_ts": "1795830000.000100"},
            {"ts": "1795830060.000100", "user": "UBOT", "bot_id": "B1", "text": "문서를 찾아 답변을…"},
            {"ts": "1795830120.000100", "user": "U3", "subtype": "channel_join", "text": "joined"},
            {"ts": "1795830180.000100", "user": "U2", "text": "자료는 <https://example.com/a|여기> 있어요", "files": [{"name": "단가표.xlsx"}]},
        ]

    def conversations_history(self, **kwargs):
        return {"messages": list(reversed(self.history))}

    def conversations_replies(self, **kwargs):
        return {"messages": [self.history[0], {"ts": "1795830030.0", "user": "U2", "text": "운송비는 별도로요", "thread_ts": "1795830000.000100"},
                             {"ts": "1795830040.0", "user": "UBOT", "bot_id": "B1", "text": "근거: 견적 절차"}]}

    def conversations_info(self, **kwargs):
        return {"channel": {"name": "sales"}}


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.content.decode("utf-8", "replace")))
        if request.method == "POST":
            return httpx.Response(200, json={"id": f"doc{len(self.calls)}"})
        return httpx.Response(200, json={"deleted": True})


def archive(api: FakeApi, tmp_path, **overrides) -> ConversationArchive:
    config = Config(api_url="http://api.test", organization_id="org", roles="member")
    baryon = BaryonClient(config, http=httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(api.handler)))
    settings = ArchiveConfig(every_channel=True, idle_minutes=30, state_path=str(tmp_path / "state.json"), **overrides)
    return ConversationArchive(settings, baryon, "UBOT")


def test_a_quiet_channel_day_becomes_one_document_with_threads_and_no_bot_posts(tmp_path) -> None:
    api = FakeApi()
    keep = archive(api, tmp_path)
    assert keep.observe({"channel": "C1", "channel_type": "channel", "user": "U1", "ts": DAY_TS, "text": "hi"})
    assert keep.flush(FakeWeb()) == 0  # not quiet for 30 minutes yet
    assert keep.flush(FakeWeb(), force=True) == 1
    method, path, body = api.calls[0]
    assert path == "/v1/documents" and 'filename="slack-sales-2026-11-28.md"' in body
    assert "# 슬랙 대화: #sales · 2026-11-28" in body and "<@U1>: 견적 유효기간은 30일로 하죠" in body
    assert "  - " in body and "운송비는 별도로요" in body  # the thread reply, under its message
    assert "문서를 찾아" not in body and "근거: 견적 절차" not in body and "joined" not in body
    assert "https://example.com/a" in body and "(첨부: 단가표.xlsx)" in body
    assert "member,admin" in body and "channel:C1" in body


def test_a_new_version_replaces_the_days_earlier_document(tmp_path) -> None:
    api = FakeApi()
    keep = archive(api, tmp_path)
    for _ in range(2):
        keep.observe({"channel": "C1", "channel_type": "channel", "user": "U1", "ts": DAY_TS})
        keep.flush(FakeWeb(), force=True)
    assert [(m, p) for m, p, _ in api.calls] == [("POST", "/v1/documents"), ("POST", "/v1/documents"), ("DELETE", "/v1/documents/doc1")]


def test_dms_bots_and_unlisted_channels_are_never_archived_and_private_channels_stay_admin_only(tmp_path) -> None:
    api = FakeApi()
    keep = archive(api, tmp_path)
    assert not keep.observe({"channel": "D1", "channel_type": "im", "user": "U1", "ts": DAY_TS})
    assert not keep.observe({"channel": "C1", "channel_type": "channel", "bot_id": "B1", "ts": DAY_TS})
    listed = archive(FakeApi(), tmp_path)
    listed.config = ArchiveConfig(channels=frozenset({"C9"}), state_path=str(tmp_path / "s2.json"))
    assert not listed.observe({"channel": "C1", "channel_type": "channel", "user": "U1", "ts": DAY_TS})
    assert keep.observe({"channel": "G1", "channel_type": "group", "user": "U1", "ts": DAY_TS})
    keep.flush(FakeWeb(), force=True)
    assert 'name="allowed_roles"\r\n\r\nadmin\r\n' in api.calls[0][2]
