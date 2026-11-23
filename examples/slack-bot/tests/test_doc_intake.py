from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baryon_slack_bot import BaryonClient, Config, Responder, strip_mentions  # noqa: E402
from doc_intake import (  # noqa: E402
    DocumentIntake, IntakeConfig, expand, member_name, result_text, thread_files, wants_analysis, wants_intake,
)

BOT = "UBOT"


class Cp949Info(zipfile.ZipInfo):
    def _encodeFilenameFlags(self):  # noqa: N802 - zipfile's name
        return self.filename.encode("cp949"), self.flag_bits  # no UTF-8 flag


def korean_zip() -> bytes:
    """An archive as Windows Explorer makes it: CP949 names, no UTF-8 flag."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in (("산출물/설계서.md", "# 설계서\n본문"), ("산출물/~$임시.docx", "x"), ("__MACOSX/._a", "x"),
                           ("안쪽.zip", "PK"), ("산출물/", "")):
            archive.writestr(Cp949Info(name), data)
    return buffer.getvalue()


class FakeApi:
    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.chats: list[dict] = []

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/v1/documents" and request.method == "POST":
                body = request.content.decode("utf-8", "replace")
                name = body.split('filename="', 1)[1].split('"', 1)[0]
                self.uploads.append({"name": name, "body": body, "headers": dict(request.headers)})
                if name.endswith(".exe"):
                    return httpx.Response(422, json={"detail": f"Could not parse {name}: no parser for '{name}'"})
                return httpx.Response(200, json={"id": f"doc{len(self.uploads)}", "title": name, "chunks": 3, "parser": "kordoc"})
            if path.startswith("/v1/documents/doc"):
                return httpx.Response(200, json={"content": "감리 지적사항: 요구사항 추적표 누락 " * 50})
            if path == "/v1/chat/completions":
                self.chats.append(json.loads(request.content))
                return httpx.Response(200, json={"id": "trace-9", "choices": [{"message": {"content": "**지적사항 1건**: 추적표 누락"}}]})
            return httpx.Response(404)
        return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))


def slack_files(files: dict[str, bytes]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer xoxb-test"
        name = request.url.path.rsplit("/", 1)[-1]
        if name == "nologin":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>sign in</html>")
        return httpx.Response(200, content=files[name])
    return httpx.Client(transport=httpx.MockTransport(handler))


class FakeWeb:
    def __init__(self, replies: list[dict] | None = None, history: list[dict] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.replies = replies or []
        self.history = history or []

    def chat_postMessage(self, **kwargs):
        self.calls.append(("post", kwargs))
        return {"ts": f"300.{len(self.calls)}"}

    def chat_update(self, **kwargs):
        self.calls.append(("update", kwargs))

    def conversations_replies(self, **kwargs):
        return {"messages": self.replies}

    def conversations_history(self, **kwargs):
        self.calls.append(("history", kwargs))
        return {"messages": self.history}

    def reactions_add(self, **kwargs):
        self.calls.append(("react+", kwargs))

    def reactions_remove(self, **kwargs):
        pass

    def named(self, name: str) -> list[dict]:
        return [kwargs for call, kwargs in self.calls if call == name]


def bot(api: FakeApi, files: dict[str, bytes] | None = None) -> Responder:
    config = Config(api_url="http://api.test", organization_id="org", roles="member")
    baryon = BaryonClient(config, http=api.client())
    intake = DocumentIntake(IntakeConfig(), baryon, "xoxb-test", http=slack_files(files or {}))
    return Responder(config, baryon, BOT, intake=intake)


def test_intent_detection() -> None:
    for text in ("문서로 추가 하고 분석하자", "문서로 추가 하자 위의 것을", "이거 자료로 등록해줘", "add this to the docs"):
        assert wants_intake(text), text
    for text in ("연차 추가로 받을 수 있어?", "문서 어디 있어?", "파일 형식이 뭐야?"):
        assert not wants_intake(text), text
    assert wants_analysis("문서로 추가 하고 분석하자")
    assert not wants_analysis("문서로 추가 하자 위의 것을")


def test_zip_members_with_korean_names() -> None:
    info = zipfile.ZipFile(io.BytesIO(korean_zip())).infolist()[0]
    assert member_name(info) == "산출물/설계서.md"
    members, skipped = expand("최종.zip", korean_zip(), max_bytes=1000)
    assert [name for name, _ in members] == ["산출물/설계서.md"]
    assert [item.reason for item in skipped] == ["압축 안의 압축 파일은 건너뜁니다"]
    assert expand("a.md", b"x", 10) == ([("a.md", b"x")], [])


def test_thread_files_skip_bot_posts_and_duplicates() -> None:
    messages = [{"user": "U1", "ts": "1", "files": [{"id": "F1", "name": "a.docx"}, {"id": "F2", "mode": "tombstone"}]},
                {"user": BOT, "bot_id": "B", "ts": "2", "files": [{"id": "F3", "name": "bot.png"}]},
                {"user": "U2", "ts": "3", "files": [{"id": "F1", "name": "a.docx"}]}]
    assert [item["id"] for item in thread_files(messages, BOT)] == ["F1"]


def test_thread_files_are_added_and_analyzed() -> None:
    api = FakeApi()
    files = {"report.docx": b"PK-docx", "all.zip": korean_zip(), "tool.exe": b"MZ"}
    parent = {"user": "U1", "ts": "100.0", "text": "감리 보고서 공유드립니다", "files": [
        {"id": "F1", "name": "report.docx", "size": 7, "url_private_download": "https://files.slack.test/report.docx"},
        {"id": "F2", "name": "all.zip", "size": 900, "url_private_download": "https://files.slack.test/all.zip"},
        {"id": "F3", "name": "tool.exe", "size": 2, "url_private_download": "https://files.slack.test/tool.exe"},
    ]}
    request = {"channel": "C1", "user": "U9", "ts": "101.0", "thread_ts": "100.0", "text": "<@UBOT> 문서로 추가 하고 분석하자"}
    web = FakeWeb(replies=[parent, request])
    bot(api, files).answer(request, web)

    assert [item["name"] for item in api.uploads] == ["report.docx", "설계서.md", "tool.exe"]
    first = api.uploads[0]
    assert first["headers"]["x-on-behalf-of"] == "slack:U9"
    assert 'name="category"' in first["body"] and "slack-channel:C1" in first["body"]

    summary = web.named("update")[0]["text"]
    assert "문서 2건으로 등록" in summary and "report.docx" in summary
    assert "all.zip/안쪽.zip" in summary and "tool.exe — 지원하지 않는 형식입니다" in summary

    chat = api.chats[0]
    assert chat["use_retrieval"] is False
    assert "### 문서: report.docx" in chat["messages"][0]["content"]
    analysis = web.named("update")[1]
    assert "추적표 누락" in analysis["text"]
    blocks = json.dumps(analysis["blocks"], ensure_ascii=False)
    assert "방금 등록한 문서 원문" in blocks and "trace-9" in blocks
    assert web.named("react+")[-1]["name"] == "white_check_mark"


def test_thread_text_becomes_a_markdown_document() -> None:
    api = FakeApi()
    replies = [{"user": "U1", "ts": "100.0", "text": "관리자 메뉴 초안\n1. 사용자 관리\n2. 문서 관리"},
               {"user": BOT, "bot_id": "B", "ts": "100.5", "text": "답변"},
               {"user": "U1", "ts": "101.0", "text": "<@UBOT> 문서로 추가 하자 위의 것을"}]
    web = FakeWeb(replies=replies)
    bot(api).answer({**replies[-1], "channel": "C1", "thread_ts": "100.0"}, web)

    upload = api.uploads[0]
    assert upload["name"].startswith("slack-") and upload["name"].endswith(".md")
    assert "# 관리자 메뉴 초안" in upload["body"] and "2. 문서 관리" in upload["body"]
    assert "답변" not in upload["body"].split("2. 문서 관리")[1][:20]
    assert "스레드 글을 문서 1건으로 등록" in web.named("update")[0]["text"]
    assert not api.chats  # no analysis was asked for


def test_top_level_request_uses_the_message_above() -> None:
    api = FakeApi()
    web = FakeWeb(history=[{"user": BOT, "bot_id": "B", "ts": "99.5", "text": "봇 글"},
                           {"user": "U2", "ts": "99.0", "text": "회의록: 다음 주 배포"}])
    bot(api).answer({"channel": "C1", "user": "U1", "ts": "100.0", "text": "<@UBOT> 이거 문서로 저장해줘"}, web)
    assert "회의록: 다음 주 배포" in api.uploads[0]["body"]


def test_missing_scope_and_nothing_to_add_are_explained() -> None:
    api = FakeApi()
    request = {"channel": "C1", "user": "U1", "ts": "101.0", "thread_ts": "100.0", "text": "<@UBOT> 문서로 추가"}
    parent = {"user": "U1", "ts": "100.0", "files": [{"id": "F1", "name": "a.docx", "size": 1,
                                                      "url_private": "https://files.slack.test/nologin"}]}
    web = FakeWeb(replies=[parent, request])
    bot(api).answer(request, web)
    assert "files:read" in web.named("update")[0]["text"] and not api.uploads

    empty = FakeWeb(replies=[request])
    bot(api).answer(request, empty)
    assert "찾지 못했습니다" in empty.named("update")[0]["text"]


def test_result_text_without_documents() -> None:
    from doc_intake import IntakeResult, Skipped

    text = result_text(IntakeResult(skipped=[Skipped("a.exe", "지원하지 않는 형식입니다")]))
    assert "등록한 문서가 없습니다" in text and "a.exe" in text
    assert strip_mentions("<@UBOT> 문서로 추가") == "문서로 추가"
