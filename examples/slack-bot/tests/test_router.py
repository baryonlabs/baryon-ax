from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import router as routing  # noqa: E402
import thread_state  # noqa: E402
from baryon_slack_bot import BaryonClient, Config, Dispatcher, Responder  # noqa: E402
from intent_eval import CASES, evaluate  # noqa: E402
from thread_snapshot import HoldConfig, TruncationHold  # noqa: E402

BOT = "UBOT"


def test_intent_cases_meet_the_bar() -> None:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    result = evaluate(data["cases"], data["users"], routing.Router())
    assert result["accuracy"] >= 0.9, result["misses"]
    assert result["holdout_accuracy"] >= 0.9, result["misses"]
    assert result["target_ok"].split("/")[0] == result["target_ok"].split("/")[1]  # every case from the target thread


def test_a_description_is_not_work() -> None:
    assert not routing.wants_action("적어도 슬랙에 작성된것을 보고 이해는 해야하는데")
    assert not routing.wants_action("등록된 문서 중에 휴가 규정 있어?")
    assert routing.wants_action("제안서 작성해 줘") and routing.wants_action("비서 오늘 일정 정리")
    assert routing.wants_action("이 업체와 협력 제안 짜서 메일 초안 만들어 줘")
    assert not routing.wants_action("이 스레드 내용 정리해 줘")  # a summary
    assert routing.wants_action("위 내용 정리해서 팀에 메일 보내 줘")


def test_the_model_classifies_free_form_and_falls_back_to_the_rules() -> None:
    owner = routing.Utterance("이 내용 좀 봐 줄래", owner=True, in_thread=True)
    said = routing.Router(lambda prompt: '{"intent": "summary", "reason": "정리 요청"}').classify(owner, "lee: 회의 내용")
    assert (said.intent, said.method) == ("summary", "model")
    # a model saying "action" for someone who is not the secretary's owner is read as a question
    person = routing.Utterance("메일 초안 만들어 줘")
    assert routing.Router(lambda prompt: '{"intent": "action"}').classify(person).intent == routing.DOC_QUESTION

    def down(prompt: str) -> str:
        raise httpx.ConnectError("down")

    for broken in (down, lambda prompt: "모르겠어요", lambda prompt: '{"intent": "dance"}'):
        route = routing.Router(broken).classify(routing.Utterance("제안서 작성해 줘", owner=True))
        assert (route.intent, route.method) == (routing.ACTION, "rules")
    # explicit commands never go to the model
    calls: list[str] = []
    routing.Router(lambda prompt: calls.append(prompt) or '{"intent": "silent"}').classify(routing.Utterance("문서로 추가해줘"))
    assert not calls


class Web:
    def __init__(self, replies: list[dict] | None = None, history: list[dict] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.replies = replies or []
        self.history = history or []

    def chat_postMessage(self, **kwargs):
        self.calls.append(("post", kwargs))
        return {"ts": f"9{len(self.calls)}.0"}

    def chat_update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return {"ok": True}

    def conversations_replies(self, **kwargs):
        return {"messages": self.replies}

    def conversations_history(self, **kwargs):
        self.calls.append(("history", kwargs))
        return {"messages": self.history}

    def reactions_add(self, **kwargs):
        pass

    def reactions_remove(self, **kwargs):
        pass

    def named(self, name: str) -> list[dict]:
        return [kwargs for call, kwargs in self.calls if call == name]


def api(requests: list[dict], thread_answer: str = "NO_ANSWER_IN_THREAD", reading: dict | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append({"path": request.url.path, **body})
        if request.url.path == "/v1/loop/threads/understand":
            return httpx.Response(200, json=reading or {})
        if body.get("use_retrieval") is False:
            return httpx.Response(200, json={"id": "t", "choices": [{"message": {"content": thread_answer}}]})
        return httpx.Response(200, json={"id": "d", "choices": [{"message": {"content": "문서에서 찾지 못했습니다."}}], "citations": [],
                                         "grounding": {"answered": False}})
    return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))


def dispatcher(requests: list[dict], **kwargs) -> Dispatcher:
    config = Config(api_url="http://api.test", organization_id="org")
    reading = kwargs.pop("reading", None)
    responder = Responder(config, BaryonClient(config, http=api(requests, reading=reading, **kwargs)), BOT)
    responder.directory.remember("UORCA", "orca-bot", is_bot=True)
    responder.read_threads = True
    hold = TruncationHold(lambda *_: None, HoldConfig(wait_seconds=20), timer=lambda *_: None)
    return Dispatcher(responder, hold=hold, background=False)


def test_a_question_about_another_bot_reads_the_channel_first_and_records_no_gap() -> None:
    requests: list[dict] = []
    web = Web(history=[{"user": "UORCA", "bot_id": "BORCA", "ts": "90.0", "text": "열려 있는 일은 3건입니다: 출시 마무리, 사이트 개선, 협력안"}])
    route = dispatcher(requests).on_mention(
        {"type": "app_mention", "channel": "C1", "user": "U1", "ts": "100.0", "text": f"<@{BOT}> 는 <@UORCA> 이 어떤일을 하고 있는지 알 알고 있나?"}, web)
    assert route.intent == routing.THREAD_QUESTION and not route.record_gap
    chats = [r for r in requests if r["path"] == "/v1/chat/completions"]
    assert chats[0]["use_retrieval"] is False and "orca-bot (봇): 열려 있는 일은 3건" in chats[0]["messages"][0]["content"]
    assert chats[1].get("record_gap") is False  # the documents after, and still no gap
    assert "지식 공백으로 남겼습니다" not in json.dumps(web.named("update"), ensure_ascii=False)


def test_the_thread_answers_without_a_document_search() -> None:
    requests: list[dict] = []
    web = Web(history=[{"user": "UORCA", "bot_id": "BORCA", "ts": "90.0", "text": "제가 하는 일은 배포 관리입니다"}])
    dispatcher(requests, thread_answer="orca-bot은 스스로 배포 관리를 한다고 적었어요(확인되지 않은 주장).").on_mention(
        {"type": "app_mention", "channel": "C1", "user": "U1", "ts": "100.0", "text": f"<@{BOT}> <@UORCA> 는 무슨 일을 해?"}, web)
    assert [r.get("use_retrieval", True) for r in requests if r["path"] == "/v1/chat/completions"] == [False]
    assert "배포 관리" in web.named("update")[-1]["text"]


def test_a_message_for_another_bot_or_an_unfinished_one_gets_no_answer() -> None:
    requests: list[dict] = []
    d = dispatcher(requests)
    d.responder.subscriptions.add("C1", "50.0")
    web = Web(replies=[{"user": "U1", "ts": "50.0", "text": f"<@{BOT}> 질문"}])
    other = d.on_message({"type": "message", "channel": "C1", "user": "U1", "ts": "60.0", "thread_ts": "50.0", "text": "<@UORCA> 여기 내용이다"}, web)
    assert other.silent and "orca-bot" in other.reason
    cut = {"type": "app_mention", "channel": "C1", "user": "U1", "ts": "61.0", "thread_ts": "50.0",
           "text": f"적어도 슬랙에 작성된것을 보고 이해는 해야하는데 그런다음에 질문을 먼저 하고 <@{BOT}> 가"}
    held = d.on_mention(cut, web)
    assert held.silent and held.reason.startswith("끊긴 글") and not web.named("post")
    # the author finishes it: routed again with the edited text, and it is not work for the secretary
    from slack_text import Message

    finished = Message.from_event({**cut, "text": f"적어도 슬랙에 작성된 것을 보고 이해는 해야 해. <@{BOT}> 기억해 줘"}, d.responder.directory, BOT)
    d.release("C1", finished, "edited")
    assert d.log[-1][1].intent == routing.THREAD_QUESTION and web.named("post")


def test_summary_comes_from_the_thread_state() -> None:
    requests: list[dict] = []
    reading = {"purpose": {"text": "워크숍 장소를 정한다", "evidence_ts": ["50.0"]},
               "decisions": [{"statement": "워크숍은 5층 세미나실", "decided_by": ["yoon"], "confirmed": True, "evidence_ts": ["50.0"]}],
               "owners": [{"who": "park", "what": "예약", "evidence_ts": ["55.0"]}],
               "open_questions": [{"question": "예산은 얼마로 할까", "evidence_ts": ["58.0"]}]}
    d = dispatcher(requests, reading=reading)
    web = Web(replies=[{"user": "U1", "ts": "50.0", "text": "워크숍 장소는 5층 세미나실로 하자"}])
    route = d.on_mention({"type": "app_mention", "channel": "C1", "user": "U1", "ts": "70.0", "thread_ts": "50.0",
                          "text": f"<@{BOT}> 정리해줘"}, web)
    assert route.intent == routing.SUMMARY
    text = web.named("update")[-1]["text"]
    for part in ("워크숍 장소를 정한다", "5층 세미나실", "park: 예약", "예산은 얼마로 할까", "지식으로 남기거나 실행한 것은 없어요"):
        assert part in text, part
    assert not [r for r in requests if r["path"] == "/v1/chat/completions"]  # no model, no search
    # the message the decision came from is edited: the summary no longer states it
    d.on_message({"type": "message", "subtype": "message_changed", "channel": "C1", "ts": "71.0",
                  "message": {"user": "U1", "ts": "50.0", "thread_ts": "50.0", "text": "장소는 다시 정하자"}}, web)
    state = d.responder.states.known("C1", "50.0")
    assert state.stale and not state.decisions and not state.purpose


def test_status_words_need_the_work_state() -> None:
    store = thread_state.ThreadStateStore()
    state = store.get("C1", "1.0")
    said = "아도가 정리한 역할을 지식 베이스에 초안으로 남겨 둘게요. ✅ 진행할게요. 끝났어요."
    assert set(thread_state.unbacked_claims(said, state)) == {"draft", "progress", "done"}
    guarded = thread_state.guard(said, state)
    assert thread_state.unbacked_claims(guarded, state) == [] and "상태: 이 스레드에서 지식으로 남기거나 실행한 것은 없어요" in guarded
    assert not store.set_status("C1", "1.0", thread_state.SAVED)  # saved needs a confirmation first
    assert store.set_status("C1", "1.0", thread_state.PENDING, what="역할 정리 저장")
    assert store.set_status("C1", "1.0", thread_state.SAVED, by="owner")
    assert thread_state.unbacked_claims("저장했어요. 진행됐어요.", state) == []
    assert [h["to"] for h in state.history] == ["pending", "saved"]


def test_thread_states_survive_a_restart(tmp_path) -> None:
    path = str(tmp_path / "states.json")
    store = thread_state.ThreadStateStore(path)
    store.apply_understanding("C1", "1.0", {"purpose": {"text": "목적", "evidence_ts": ["1.0"]}})
    store.set_status("C1", "1.0", thread_state.PENDING, what="메일 발송")
    again = thread_state.ThreadStateStore(path).known("C1", "1.0")
    assert again.purpose == "목적" and again.status == thread_state.PENDING and again.status_what == "메일 발송"
