"""The secretary bridge: the owner's work requests in Slack go to the voice secretary."""

from __future__ import annotations

import base64
import io
import json
import zipfile

import httpx

from doc_intake import NO_FILES_READ, IntakeError, download_slack_file
from secretary_bridge import ACTION_YES, BridgeConfig, SecretaryBridge, card_mrkdwn, read_files

OWNER = "U0OWNER"
REQUEST = ("<@U0BOT> <https://www.gonggong.ai/#aroom|gonggong.ai/#…> 이런업체와 우리가 협력을 한다면? 어떤제안을 가능하고 연락해서 "
           "시나리오를 짜보고 전략을 세워서 연락까지 해봐")
MAIL_CARD = {"kind": "text", "title": "메일 초안: 공공에이아이 협력 제안",
             "body": "받는 사람: contact@gonggong.ai\n제목: 온프레미스 AI 협력 제안\n- 제안 1. 사내 문서 근거 매칭\n- 제안 2. 서류 작성 온프레미스 처리"}


class FakeSecretary:
    def __init__(self) -> None:
        self.turns: list[str] = []
        self.images: list[list[dict]] = []
        self.sessions = 0
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/sessions":
            if self.down:
                return httpx.Response(500, text="Internal Server Error")
            self.sessions += 1
            return httpx.Response(200, json={"id": f"s{self.sessions}"})
        if request.url.path.endswith("/turn"):
            body = json.loads(request.content)
            self.turns.append(body["text"])
            self.images.append(body.get("images", []))
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
    def __init__(self, replies: list[dict] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.replies = replies or []

    def conversations_replies(self, **kwargs):
        return {"messages": self.replies}

    def chat_postMessage(self, **kwargs):
        self.calls.append(("post", kwargs))
        return {"ts": f"300.{len(self.calls)}"}

    def chat_update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return {"ok": True}

    def chat_postEphemeral(self, **kwargs):
        self.calls.append(("ephemeral", kwargs))
        return {"ok": True}


def bridge(secretary: FakeSecretary, files: dict[str, bytes] | None = None, **overrides) -> SecretaryBridge:
    config = BridgeConfig(url="http://secretary", key="k", users=frozenset({OWNER}), **overrides)

    def fetch(item: dict) -> bytes:
        if item["name"] not in (files or {}):
            raise IntakeError(NO_FILES_READ)
        return (files or {})[item["name"]]

    return SecretaryBridge(config, http=httpx.Client(transport=httpx.MockTransport(secretary.handler)), fetch=fetch)


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def archive(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        for name, data in members.items():
            handle.writestr(name, data)
    return buffer.getvalue()


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


def test_the_thread_before_the_request_goes_with_it() -> None:
    secretary = FakeSecretary()
    work = bridge(secretary)
    thread = [
        {"ts": "10.0", "user": OWNER, "text": "<@U0BOT> 공공에이아이와 협력한다면?"},
        {"ts": "11.0", "bot_id": "B1", "user": "U0BOT", "text": "제안 1. 폐쇄형 AROOM\n제안 2. 문서 이해 엔진 <https://www.gonggong.ai/|gonggong.ai>"},
        {"ts": "12.0", "user": "U0COLLEAGUE", "text": "좋네요. 미팅은 다음 주가 좋겠어요", "files": [{"name": "회사소개서.pdf"}]},
        {"ts": "13.0", "user": OWNER, "text": "<@U0BOT> 위의 내용을 구글 메일 초안으로 올려놔두고 링크 줘"},
    ]
    web = FakeWeb(replies=thread)
    work.handle({"channel": "C1", "ts": "13.0", "thread_ts": "10.0", "user": OWNER, "text": thread[-1]["text"]}, web)
    first = secretary.turns[0]
    assert "[이 슬랙 스레드의 앞선 메시지." in first and "baryon 봇: 제안 1. 폐쇄형 AROOM" in first
    assert "https://www.gonggong.ai/" in first and "|gonggong.ai" not in first
    assert "<@U0COLLEAGUE>: 좋네요. 미팅은 다음 주가 좋겠어요 (첨부: 회사소개서.pdf)" in first
    assert first.count("위의 내용을 구글 메일 초안으로") == 1  # the request itself, not twice
    # Next turn: only what is new in the thread, not the whole history again (and not the bridge's own posts).
    web.replies = thread + [{"ts": "300.1", "bot_id": "B1", "text": ":robot_face: 비서가 일하고 있어요…"},
                            {"ts": "14.0", "user": "U0COLLEAGUE", "text": "받는 분은 partner@gonggong.ai 예요"}]
    work.handle({"channel": "C1", "ts": "15.0", "thread_ts": "10.0", "user": OWNER, "text": "응"}, web)
    second = secretary.turns[1]
    assert "partner@gonggong.ai" in second and "제안 1" not in second and "비서가 일하고" not in second



def test_attached_photos_zips_and_text_go_to_the_secretary() -> None:
    secretary = FakeSecretary()
    files = {"영수증.png": PNG, "회의록.zip": archive({"회의록/요약.md": "# 결정\n다음 주 미팅".encode(), "회의록/화이트보드.jpg": PNG,
                                                       "__MACOSX/._x": b"x"}),
             "계약서.pdf": b"%PDF-1.7"}
    work = bridge(secretary, files=files)
    thread = [{"ts": "10.0", "user": "U0COLLEAGUE", "text": "자료 올려요", "files": [{"id": "F2", "name": "회의록.zip"}]}]
    event = {"channel": "C1", "ts": "11.0", "thread_ts": "10.0", "user": OWNER, "text": "<@U0BOT> 이거 보고 메일 초안 만들어 줘",
             "files": [{"id": "F1", "name": "영수증.png"}, {"id": "F3", "name": "계약서.pdf"}]}
    work.handle(event, FakeWeb(replies=thread + [event]))
    first, images = secretary.turns[0], secretary.images[0]
    assert "[첨부 파일." in first and "영수증.png: 사진 2번" in first and "회의록.zip: 압축 파일, 2개 파일을 풀었다" in first
    assert "--- 회의록.zip/회의록/요약.md ---\n# 결정\n다음 주 미팅" in first and "회의록.zip/회의록/화이트보드.jpg: 사진 1번" in first
    assert "계약서.pdf: .pdf 파일" in first and "문서로 추가" in first and "__MACOSX" not in first
    assert [image["media_type"] for image in images] == ["image/jpeg", "image/png"]
    assert base64.b64decode(images[1]["data"]) == PNG
    # The same files are not sent again on the next turn.
    work.handle({"channel": "C1", "ts": "12.0", "thread_ts": "10.0", "user": OWNER, "text": "응"}, FakeWeb(replies=thread + [event]))
    assert secretary.images[1] == [] and "[첨부 파일." not in secretary.turns[1]


def test_missing_files_read_is_said_not_swallowed() -> None:
    block, images = read_files([{"name": "사진.png"}], lambda item: (_ for _ in ()).throw(IntakeError(NO_FILES_READ)))
    assert "사진.png: 받지 못했다" in block and "files:read" in block and images == []
    # Slack's answer without the scope: a redirect to the sign-in page, then 403.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "files.slack.test":
            return httpx.Response(302, headers={"location": "https://team.slack.test/?redir=%2Ffiles-pri%2Fx"})
        return httpx.Response(403, headers={"content-type": "text/html"}, text="<html>sign in</html>")
    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        download_slack_file(http, "xoxb", {"name": "a.md", "url_private": "https://files.slack.test/files-pri/x/a.md"})
        raise AssertionError("should have refused")
    except IntakeError as error:
        assert "files:read" in str(error) and "다시 설치" in str(error)


def test_a_secretary_that_is_down_is_reported_in_the_thread() -> None:
    secretary = FakeSecretary()
    secretary.down = True
    web = FakeWeb()
    bridge(secretary).handle({"channel": "C1", "ts": "1.0", "user": OWNER, "text": "메일 초안 만들어 줘"}, web)
    assert web.calls[-1][0] == "post" and "비서가 응답하지 않아요" in web.calls[-1][1]["text"]


def test_mentions_are_kept_as_names_not_deleted() -> None:
    from slack_text import UserDirectory

    secretary = FakeSecretary()
    work = bridge(secretary)
    work.bot_user_id = "U0BOT"
    work.directory = UserDirectory({"U0BOT": {"profile": {"display_name": "baryon"}},
                                    "U0KIM": {"profile": {"display_name": "kim"}}}.get)
    work.handle({"channel": "C1", "ts": "1.0", "user": OWNER,
                 "text": "<@U0BOT> <@U0KIM> 에게 보낼 메일 초안 <https://x.test/a|안내서> 참고해서 만들어 줘"}, FakeWeb())
    first = secretary.turns[0]
    assert "너(baryon) @kim 에게 보낼 메일 초안 안내서 (https://x.test/a) 참고해서 만들어 줘" in first
