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
        self.summaries: list[dict] = []  # one-line summary calls for the attached sheet

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
                body = json.loads(request.content)
                if body["messages"][0]["content"].startswith("아래 문서가 무엇에 관한"):
                    self.summaries.append(body)
                    title = body["messages"][0]["content"].split("문서 이름: ", 1)[1].split("\n", 1)[0]
                    return httpx.Response(200, json={"id": "s", "choices": [{"message": {"content": f"- {title} 요약 한 줄"}}]})
                self.chats.append(body)
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
    def __init__(self, replies: list[dict] | None = None, history: list[dict] | None = None, upload_error: str = "") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.replies = replies or []
        self.history = history or []
        self.upload_error = upload_error

    def files_upload_v2(self, **kwargs):
        self.calls.append(("file", kwargs))
        if self.upload_error:
            raise RuntimeError(f"The request to the Slack API failed. (error: {self.upload_error})")
        return {"ok": True}

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


def test_read_request_reads_the_threads_files_once() -> None:
    from doc_intake import IntakeLedger, wants_file_reading

    for text in ("분석하라", "엑셀파일은 읽어보아라 위의 쓰레드를", "이거 살펴봐줘", "please read this"):
        assert wants_file_reading(text), text
    assert not wants_file_reading("연차는 며칠이야?")

    api = FakeApi()
    files = {"plan.xlsx": b"PK-xlsx", "shot.png": b"\x89PNG"}
    shared = {"user": "U1", "ts": "100.5", "text": "", "files": [
        {"id": "F9", "name": "plan.xlsx", "size": 7, "url_private_download": "https://files.slack.test/plan.xlsx"},
    ]}
    link = {"user": "U1", "ts": "100.0", "text": "<https://other-org.slack.com/archives/C9/p1795407816704509|other-org.slack.com/archives/…>"}
    ask = {"channel": "C1", "user": "U1", "ts": "101.0", "thread_ts": "100.0", "text": "<@UBOT> 분석하라"}
    responder = bot(api, files)
    responder.team_domain = "acme"
    responder.intake.ledger = IntakeLedger(None)
    web = FakeWeb(replies=[link, shared, ask])
    responder.answer(ask, web)
    assert [u["name"] for u in api.uploads] == ["plan.xlsx"]
    assert "slack-file:F9" in api.uploads[0]["body"]
    assert api.chats and api.chats[0]["use_retrieval"] is False  # analyzed although "분석" came without "문서로 추가"
    summary = web.named("update")[0]["text"]
    assert "다른 Slack 워크스페이스" in summary

    again = FakeWeb(replies=[link, shared, {**ask, "ts": "102.0", "text": "<@UBOT> 엑셀 다시 읽어봐"}])
    responder.answer({**ask, "ts": "102.0", "text": "<@UBOT> 엑셀 다시 읽어봐"}, again)
    assert len(api.uploads) == 1  # the same Slack file is not uploaded twice
    assert "이미 문서 1건으로 등록" in again.named("update")[0]["text"]
    assert len(api.chats) == 2


def test_thread_without_files_answers_normally() -> None:
    api = FakeApi()
    ask = {"channel": "C1", "user": "U1", "ts": "101.0", "thread_ts": "100.0", "text": "<@UBOT> 분석하라"}
    web = FakeWeb(replies=[{"user": "U1", "ts": "100.0", "text": "주간 보고 초안"}, ask])
    bot(api).answer(ask, web)
    assert not api.uploads and api.chats and api.chats[0].get("use_retrieval", True) is True


def read_sheet(content: bytes) -> dict[str, list[tuple]]:
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(content))
    return {name: [tuple(row) for row in book[name].iter_rows(values_only=True)] for name in book.sheetnames}


def test_added_documents_are_attached_as_a_sheet() -> None:
    api = FakeApi()
    files = {"report.docx": b"PK-docx", "all.zip": korean_zip(), "tool.exe": b"MZ"}
    parent = {"user": "U1", "ts": "100.0", "files": [
        {"id": "F1", "name": "report.docx", "size": 7, "url_private_download": "https://files.slack.test/report.docx"},
        {"id": "F2", "name": "all.zip", "size": 900, "url_private_download": "https://files.slack.test/all.zip"},
        {"id": "F3", "name": "tool.exe", "size": 2, "url_private_download": "https://files.slack.test/tool.exe"},
    ]}
    request = {"channel": "C1", "user": "U9", "ts": "101.0", "thread_ts": "100.0", "text": "<@UBOT> 문서로 추가해줘"}
    web = FakeWeb(replies=[parent, request])
    bot(api, files).answer(request, web)

    upload = web.named("file")[0]
    assert upload["channel"] == "C1" and upload["thread_ts"] == "100.0"
    assert upload["filename"].startswith("추가된_문서_") and upload["filename"].endswith(".xlsx")
    assert "엑셀로 첨부" in upload["initial_comment"]
    sheet = read_sheet(upload["content"])
    rows = sheet["추가된 문서"]
    assert rows[0] == ("번호", "파일", "문서 제목", "한 줄 내용", "조각 수", "파서", "상태", "문서 ID")
    assert [(r[1], r[3], r[6]) for r in rows[1:]] == [
        ("report.docx", "report.docx 요약 한 줄", "새로 등록"),
        ("all.zip/산출물/설계서.md", "설계서.md 요약 한 줄", "새로 등록"),
    ]
    assert sheet["건너뛴 파일"][1:] == [(1, "all.zip/안쪽.zip", "압축 안의 압축 파일은 건너뜁니다"),
                                     (2, "tool.exe", "지원하지 않는 형식입니다")]
    assert all(s["use_retrieval"] is False for s in api.summaries) and len(api.summaries) == 2
    assert not api.chats  # no analysis was asked for

    # Asking again: nothing is uploaded twice, and the sheet marks the rows as registered earlier
    again = FakeWeb(replies=[parent, {**request, "ts": "102.0"}])
    responder = bot(api, files)
    responder.intake.ledger.items = {"F1": [{"name": "report.docx", "document_id": "doc1", "title": "report.docx"}]}
    responder.answer({**request, "ts": "102.0"}, again)
    first = read_sheet(again.named("file")[0]["content"])["추가된 문서"][1]
    assert first[6] == "이전에 등록됨"


def test_sheet_without_files_write_says_what_to_add() -> None:
    api = FakeApi()
    replies = [{"user": "U1", "ts": "100.0", "text": "관리자 메뉴 초안\n1. 사용자 관리"},
               {"user": "U1", "ts": "101.0", "text": "<@UBOT> 문서로 추가"}]
    web = FakeWeb(replies=replies, upload_error="missing_scope")
    bot(api).answer({**replies[-1], "channel": "C1", "thread_ts": "100.0"}, web)
    assert any("files:write" in p["text"] for p in web.named("post"))
    assert web.named("react+")[-1]["name"] == "white_check_mark"  # the documents were still added


def test_one_line_falls_back_to_the_documents_first_line() -> None:
    from intake_sheet import _clean, first_line

    # short headings, "작성자:" metadata and table rules are skipped
    assert first_line("# 제목\n\n> 작성자: 홍길동\n|---|---|\n감리 지적사항 3건과 조치 계획을 정리한 보고서") == "감리 지적사항 3건과 조치 계획을 정리한 보고서"
    assert _clean("<think>음</think>\n한 줄 요약: **설계 단계** 감리 결과 보고서") == "설계 단계 감리 결과 보고서"


def test_result_text_without_documents() -> None:
    from doc_intake import IntakeResult, Skipped

    text = result_text(IntakeResult(skipped=[Skipped("a.exe", "지원하지 않는 형식입니다")]))
    assert "등록한 문서가 없습니다" in text and "a.exe" in text
    assert strip_mentions("<@UBOT> 문서로 추가") == "문서로 추가"
