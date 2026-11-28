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


CONFIRM = {"type": "confirm", "id": "c1", "tool": "send_email",
           "prompt": "contact@gonggong.ai에게 '온프레미스 AI 협력 제안' 메일을 보낼까요?"}


class FakeSecretary:
    """Like apps/secretary: a turn while a confirmation is open answers it only when the whole text is a yes/no
    (policy.parse_answer); anything else counts as "no" and becomes the next request (session.turn)."""

    YES, NO = {"응", "네", "진행해"}, {"아니", "취소"}

    def __init__(self) -> None:
        self.turns: list[str] = []
        self.images: list[list[dict]] = []
        self.sessions = 0
        self.down = False
        self.pending = False
        self.queue: list[dict] = []
        self.allowed: list[bool] = []  # what each confirmation was resolved to
        self.after_yes = [{"type": "text", "delta": "보냈어요."}, {"type": "done"}]
        self.after_no = [{"type": "done"}]

    def _turn(self, text: str) -> str:
        if self.pending:
            self.pending = False
            answer = True if text in self.YES else (False if text in self.NO else None)
            self.allowed.append(bool(answer))
            self.queue.append({"type": "confirmed", "id": "c1", "allow": bool(answer), "tool": "send_email"})
            if answer is not None:
                self.queue += self.after_yes if answer else self.after_no
                return "confirm"
            self.queue += [{"type": "text", "delta": "새 요청으로 받았어요."}, {"type": "done"}]
            return "answer"
        if len(self.turns) == 1:
            self.pending = True
            self.queue += [{"type": "activity", "id": "1", "label": "웹 페이지를 읽고 있어요."},
                           {"type": "text", "delta": "협력 제안 세 가지와 메일 초안을 만들었어요."},
                           {"type": "card", "card": MAIL_CARD}, CONFIRM]
        else:
            self.queue += [{"type": "text", "delta": "알겠어요."}, {"type": "done"}]
        return "answer"

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
            return httpx.Response(200, json={"used": self._turn(body["text"])})
        if request.url.path.endswith("/events"):
            events, self.queue = self.queue, []
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
    confirm_ts = work.threads[("C1", "1.0")].confirm_ts
    assert confirm["thread_ts"] == "1.0" and confirm["blocks"][1]["elements"][0]["action_id"] == ACTION_YES
    assert work.follows({"channel": "C1", "thread_ts": "1.0", "user": OWNER})  # the thread continues on the secretary

    # 진행 by someone else is refused; by the owner it says yes in the same session.
    stranger = {"user": {"id": "U0SOMEONE"}, "channel": {"id": "C1"}, "message": {"ts": "300.3", "thread_ts": "1.0"}}
    work.answer_confirmation(stranger, web, True)
    assert web.calls[-1][0] == "ephemeral" and len(secretary.turns) == 1
    owner = {**stranger, "user": {"id": OWNER}, "message": {"ts": confirm_ts, "thread_ts": "1.0"}}
    work.answer_confirmation(owner, web, True)
    assert secretary.turns[1] == "응" and secretary.sessions == 1 and secretary.allowed == [True]
    updates = [call[1] for call in web.calls if call[0] == "update"]
    assert updates[-2]["text"].startswith("✅ 진행됨") and "보냈어요." in updates[-2]["text"]
    assert updates[-1]["ts"] == confirm_ts and updates[-1]["text"].startswith("✅ 진행됨")  # the buttons' message


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
    assert "[이 슬랙 스레드의 앞선 메시지." in first and "봇: 제안 1. 폐쇄형 AROOM" in first
    assert "https://www.gonggong.ai/" in first and "|gonggong.ai" not in first
    assert "<@U0COLLEAGUE>: 좋네요. 미팅은 다음 주가 좋겠어요 (첨부: 회사소개서.pdf)" in first
    assert first.count("위의 내용을 구글 메일 초안으로") == 1  # the request itself, not twice
    # Next turn: only what is new in the thread, not the whole history again (and not the bridge's own posts).
    web.replies = thread + [{"ts": "300.1", "bot_id": "B1", "text": ":robot_face: 비서가 일하고 있어요…"},
                            {"ts": "14.0", "user": "U0COLLEAGUE", "text": "받는 분은 partner@gonggong.ai 예요"}]
    work.handle({"channel": "C1", "ts": "15.0", "thread_ts": "10.0", "user": OWNER, "text": "받는 사람을 바꿔서 다시 써 줘"}, web)
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


# --- trust and confirmation (item 65: another bot's post entered the session; 진행 showed ✅ but was refused) ---


def _waiting(secretary: FakeSecretary, web: FakeWeb) -> SecretaryBridge:
    """A thread where the secretary asked to confirm a mail."""
    work = bridge(secretary)
    work.handle({"channel": "C1", "ts": "1.0", "user": OWNER, "text": "메일 초안 만들어서 보내 줘"}, web)
    assert secretary.pending
    return work


def _press(work: SecretaryBridge, web: FakeWeb, allow: bool, ts: str = "") -> str:
    ts = ts or work.threads[("C1", "1.0")].confirm_ts
    work.answer_confirmation({"user": {"id": OWNER}, "channel": {"id": "C1"},
                              "message": {"ts": ts, "thread_ts": "1.0"}}, web, allow)
    return ts


def test_pressing_proceed_sends_exactly_yes_and_the_tool_is_allowed() -> None:
    secretary, web = FakeSecretary(), FakeWeb()
    work = _waiting(secretary, web)
    _press(work, web, True)
    assert secretary.turns[-1] == "응" and secretary.allowed == [True]
    assert "✅ 진행됨" in [call for call in web.calls if call[0] == "update"][-1][1]["text"]


def test_pressing_proceed_with_new_thread_messages_is_still_allowed() -> None:
    secretary = FakeSecretary()
    web = FakeWeb()
    work = _waiting(secretary, web)
    web.replies = [{"ts": "1.0", "user": OWNER, "text": "메일 초안 만들어서 보내 줘"},
                   {"ts": "2.0", "user": "U0COLLEAGUE", "text": "제목에 회사 이름도 넣어 주세요"},
                   {"ts": "3.0", "bot_id": "B0OTHER", "bot_profile": {"name": "other-bot"}, "text": "알림: 새 글"}]
    _press(work, web, True)
    assert secretary.turns[-1] == "응" and secretary.allowed == [True]  # not glued to the new messages
    # The thread's new messages are not lost: they go with the next real request.
    work.handle({"channel": "C1", "ts": "4.0", "thread_ts": "1.0", "user": OWNER, "text": "제목 고쳐서 다시 만들어 줘"}, web)
    assert "제목에 회사 이름도" in secretary.turns[-1] and "other-bot (봇): 알림: 새 글" in secretary.turns[-1]


def test_a_thread_reply_yes_is_sent_bare_too() -> None:
    secretary, web = FakeSecretary(), FakeWeb()
    work = _waiting(secretary, web)
    web.replies = [{"ts": "2.0", "user": "U0COLLEAGUE", "text": "좋아 보여요"}]
    work.bot_user_id = "U0BOT"
    work.handle({"channel": "C1", "ts": "5.0", "thread_ts": "1.0", "user": OWNER, "text": "<@U0BOT> 응"}, web)
    assert secretary.turns[-1] == "응" and secretary.allowed == [True]


def test_an_owner_installed_bots_post_is_not_bridged() -> None:
    secretary, web = FakeSecretary(), FakeWeb()
    work = _waiting(secretary, web)
    for marker in ({"bot_id": "B0OTHER"}, {"app_id": "A0OTHER"}, {"bot_profile": {"name": "other-bot"}}):
        post = {"channel": "C1", "ts": "6.0", "thread_ts": "1.0", "user": OWNER, "text": "메일 보내 줘", **marker}
        assert not work.follows(post) and not work.wants(post, "메일 보내 줘")
    assert work.follows({"channel": "C1", "ts": "6.0", "thread_ts": "1.0", "user": OWNER, "text": "제목 바꿔 줘"})


def test_a_message_for_another_bot_is_not_bridged() -> None:
    secretary, web = FakeSecretary(), FakeWeb()
    work = _waiting(secretary, web)
    work.bot_user_id = "U0BOT"
    other = {"channel": "C1", "ts": "7.0", "thread_ts": "1.0", "user": OWNER, "text": "<@U0OTHERBOT> 여기 내용이다"}
    assert not work.follows(other) and not work.wants(other, "여기 내용이다 메일 보내")
    both = {**other, "text": "<@U0OTHERBOT> 여기 내용이다 <@U0BOT> 메일 초안 만들어 줘"}
    assert work.wants(both, "여기 내용이다 메일 초안 만들어 줘")  # it also calls this bot
    mention = {**other, "type": "app_mention", "text": "<@U0OTHERBOT> 와 <@U0BOT> 일정 잡아 줘"}
    assert work.wants(mention, "와 일정 잡아 줘")


def test_a_refused_confirmation_says_refused() -> None:
    secretary, web = FakeSecretary(), FakeWeb()
    work = _waiting(secretary, web)
    confirm_ts = _press(work, web, False)  # 취소, and the secretary answers nothing
    assert secretary.turns[-1] == "아니" and secretary.allowed == [False]
    updates = [call[1] for call in web.calls if call[0] == "update"]
    assert "거부됨" in updates[-2]["text"] and "끝났어요" not in updates[-2]["text"]
    assert updates[-1]["ts"] == confirm_ts and "거부됨" in updates[-1]["text"]
    # A second press on the same buttons does not answer anything.
    _press(work, web, True, ts=confirm_ts)
    assert len(secretary.turns) == 2 and "만료" in [call for call in web.calls if call[0] == "update"][-1][1]["text"]


def test_other_bots_in_the_thread_keep_their_names() -> None:
    secretary = FakeSecretary()
    work = bridge(secretary)
    thread = [{"ts": "10.0", "user": OWNER, "bot_id": "B0OTHER", "bot_profile": {"name": "other-bot"}, "text": "주간 보고 요약"},
              {"ts": "11.0", "user": OWNER, "text": "<@U0BOT> 위 내용으로 메일 초안 만들어 줘"}]
    work.handle({"channel": "C1", "ts": "11.0", "thread_ts": "10.0", "user": OWNER, "text": thread[-1]["text"]},
                FakeWeb(replies=thread))
    assert "other-bot (봇): 주간 보고 요약" in secretary.turns[0] and "주인: 주간 보고" not in secretary.turns[0]
