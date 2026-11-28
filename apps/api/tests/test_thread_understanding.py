"""Thread understanding (baryon.thread_understanding): one thread snapshot → one checked JSON reading.

Every name and id here is invented. The golden-thread scores of this step are in eval/thread_baseline_understand.json
(tests/test_thread_eval.py); these tests pin the rules item by item.
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from baryon import loop
from baryon.main import app
from baryon.thread_understanding import (
    NEEDS_VERIFICATION,
    OWNER,
    anchor,
    parse_thread,
    question_bundle,
    understand,
    understand_rules_payload,
)

ME = {"id": "UBOT", "name": "baryon"}


def msg(ts: str, author: str, text: str, kind: str = "human", **extra) -> dict:
    """A message as thread_snapshot.message_payload writes it (text already readable, segments = plain pieces)."""
    pieces = [p for p in text.replace("너(baryon)", "\x00").split("\x00")]
    for mention in extra.get("mentions", []):
        pieces = [q for p in pieces for q in p.split("@" + mention["name"])]
    return {"ts": ts, "author_id": "U" + author.upper(), "author_name": author, "author_kind": kind, "text": text, "raw": text,
            "segments": [p for p in pieces if p.strip()], "mentions": [], "mentions_me": "너(baryon)" in text,
            "addressees": [], "reactions": [], "edited": False, "deleted": False, "incomplete": False, **extra}


def thread(*messages: dict, **extra) -> dict:
    return {"channel": "C1", "thread_ts": messages[0]["ts"], "me": ME, "messages": list(messages), **extra}


OTHER = {"id": "UHELPER", "name": "helper-bot", "is_me": False, "is_bot": True, "kind": "user"}


# --------------------------------------------------------------------------- decisions and requests


def test_only_people_decide_and_requests_are_not_decisions() -> None:
    result = understand_rules_payload(thread(
        msg("1.0", "kim", "너(baryon) 이 스레드 내용 정리해 줘"),
        msg("2.0", "notes", "정리: 배포는 목요일로 하자고 했습니다.", kind="other_bot"),
        msg("3.0", "kim", "@helper-bot 지난달 표 다시 올려줘", mentions=[OTHER], addressees=["UHELPER"], addressed_to_me=False),
        msg("4.0", "lee", "견적 유효기간은 30일로 하죠. 원자재 단가가 한 달 단위로 바뀌니까요."),
    ))
    assert [d["statement"] for d in result["decisions"]] == ["견적 유효기간은 30일로 하죠."]
    decision = result["decisions"][0]
    assert decision["confirmed"] and decision["kind"] == "explicit" and decision["decided_by"] == ["lee"]
    assert decision["evidence_ts"] == ["4.0"] and decision["quote"] == "견적 유효기간은 30일로 하죠"
    assert [r["statement"] for r in decision["reasons"]] == ["원자재 단가가 한 달 단위로 바뀌니까요."]
    assert {r["to"] for r in result["requests"]} == {"너", "helper-bot"}  # "해 줘" is asked, not decided


def test_a_proposal_agreed_by_reactions_is_implicit_and_not_confirmed() -> None:
    proposal = msg("1.0", "choi", "배포를 목요일 4시로 고정하면 어떨까요?",
                   reactions=[{"name": "+1", "users": ["UJUNG", "UHAN"], "count": 2}, {"name": "white_check_mark", "users": ["UHAN"], "count": 1}])
    result = understand_rules_payload(thread(proposal, msg("2.0", "jung", "좋아요")))
    decision = result["decisions"][0]
    assert decision["kind"] == "implicit" and not decision["confirmed"]
    assert {s["by_id"] for s in decision["agreement_signals"]} == {"UJUNG", "UHAN"}


def test_an_unfinished_message_is_not_a_confirmed_decision_and_is_not_answered() -> None:
    result = understand_rules_payload(thread(
        msg("1.0", "kim", "너(baryon) 는 무엇을 하나?"),
        msg("2.0", "kim", "주간 보고는 금요일마다 해야하는데 그 전에 너(baryon) 가", incomplete=True),
    ))
    assert [d["evidence_ts"] for d in result["decisions"]] == [["2.0"]] and not result["decisions"][0]["confirmed"]
    assert [i["ts"] for i in result["incomplete"]] == ["2.0"]
    plan = {r["ts"]: r["action"] for r in result["addressed_to_bot"]}
    assert plan == {"1.0": "respond", "2.0": "skip"}
    assert "주간 보고" in result["purpose"]["text"]  # what the owner says the thread must achieve


def test_who_the_bot_answers() -> None:
    dev = {"id": "UDEV", "name": "dev bot", "is_me": False, "is_bot": True, "kind": "user"}
    result = understand_rules_payload(thread(
        msg("1.0", "kim", "너(baryon) 회의록에서 정한 것 남길 수 있어?"),
        msg("2.0", "baryon", "'정산은 2주 안에'를 남길까요?", kind="self_bot"),
        msg("3.0", "kim", "응 그렇게 해"),
        msg("4.0", "notes", "정리합니다 너(baryon)", kind="other_bot", mentions_me=True),
        msg("5.0", "kim", "@dev bot 여기 내용이다", mentions=[dev], addressees=["UDEV"], addressed_to_me=False),
        msg("6.0", "lee", "저는 점심 먹고 올게요"),
    ))
    plan = {r["ts"]: (r["action"], r["reason"]) for r in result["addressed_to_bot"]}
    assert [ts for ts, (action, _) in plan.items() if action == "respond"] == ["1.0", "3.0"]  # 3.0 answers my question
    assert "다른 봇" in plan["4.0"][1] and "dev bot" in plan["5.0"][1]
    handover = next(d for d in result["decisions"] if d["evidence_ts"] == ["5.0"])
    assert handover["kind"] == "implicit" and handover["handover_to"] == "dev bot"


# --------------------------------------------------------------------------- sources, claims and contests


def test_other_bots_claims_need_verification_and_disagreements_are_contests() -> None:
    result = understand_rules_payload(thread(
        msg("1.0", "kim", "너(baryon) 토끼가 뭐야?"),
        msg("2.0", "ado", "토끼(Tokki)는 사내 일정 관리 도구입니다. run-bot은 배포 실행 담당입니다.", kind="other_bot"),
        msg("3.0", "orca", "```\n누구      맡은 일\n────      ──────\n토끼      회의록 요약\nrun-bot   테스트, 실행 관리\n```", kind="other_bot"),
    ))
    assert {c["trust"] for c in result["claims"]} == {NEEDS_VERIFICATION}
    assert [c["subject"] for c in result["contests"]] == ["토끼"]  # run-bot: both say 실행 — a refinement, not a contest
    contest = result["contests"][0]
    assert contest["evidence_ts"] == ["2.0", "3.0"] and contest["status"] == "needs_owner"
    assert len(result["ask"]) == 1 and "토끼" in result["ask"][0]["text"]  # one bundled question


def test_a_person_settles_a_contest_after_it() -> None:
    result = understand_rules_payload(thread(
        msg("1.0", "jo", "너(baryon) 이번 달 지원 담당이 누구야?"),
        msg("2.0", "helper", "고객 지원은 song 님 담당입니다.", kind="other_bot"),
        msg("3.0", "notes", "고객 지원 담당은 ryu 님입니다.", kind="other_bot"),
        msg("4.0", "jo", "맞아 이번 달은 ryu 담당이야. 환불 승인 기준은 아직 안 정했어."),
    ))
    assert result["contests"] == []
    decision = result["decisions"][0]
    assert decision["confirmed"] and decision["confirms"][0]["ts"] == "3.0"  # it confirms what notes said
    assert [q["ask"] for q in result["open_questions"]] == [True]


def test_the_owner_outranks_people() -> None:
    result = understand_rules_payload(thread(msg("1.0", "kim", "배포는 목요일입니다."), msg("2.0", "lee", "QA는 수요일입니다."), owners=["UKIM"]))
    assert {c["by"]: c["trust"] for c in result["claims"]} == {"kim": OWNER, "lee": "human"}


# --------------------------------------------------------------------------- evidence, the model path and its fallback


def test_quotes_are_anchored_in_the_plain_text_between_mentions() -> None:
    parsed = parse_thread(thread(msg("1.0", "kim", "@park 님이 견적서 양식 수정 담당해 주세요.",
                                     mentions=[{"id": "UPARK", "name": "park"}], segments=[" 님이 견적서 양식 수정 담당해 주세요."])))
    assert anchor("@park 님이 견적서 양식 수정 담당해 주세요.", parsed.messages[0]) == "님이 견적서 양식 수정 담당해 주세요"


def model_answer(data: dict):
    async def complete(messages):
        assert "[1.0] kim (owner)" in messages[0]["content"]
        return json.dumps(data, ensure_ascii=False)
    return complete


def test_the_model_reading_is_checked_and_the_rules_still_decide_who_to_answer() -> None:
    payload = thread(msg("1.0", "kim", "정산 기한은 2주가 맞아"), msg("2.0", "notes", "정리: 정산 기한은 1주입니다.", kind="other_bot"))
    result = asyncio.run(understand(payload, model_answer({
        "purpose": {"text": "정산 기한 확인", "evidence_ts": ["1.0"], "quote": "정산 기한"},
        "decisions": [
            {"statement": "정산 기한 2주", "decided_by": "kim", "kind": "explicit", "evidence_ts": ["1.0"], "quote": "2주가 맞아"},
            {"statement": "정산 기한 1주", "kind": "explicit", "evidence_ts": ["2.0"], "quote": "1주입니다"},       # a bot's summary
            {"statement": "정산은 3주", "kind": "explicit", "evidence_ts": ["1.0"], "quote": "3주로 하자"},          # not in the text
            {"statement": "정산은 2주", "kind": "explicit", "evidence_ts": ["9.0"], "quote": "2주"},                # not in the thread
        ],
        "claims": [{"subject": "정산 기한", "predicate": "", "value": "1주", "evidence_ts": ["2.0"], "quote": "정산 기한은 1주"}],
    })))
    assert result["method"] == "model" and result["dropped"] == 3
    assert [d["statement"] for d in result["decisions"]] == ["정산 기한 2주"] and result["decisions"][0]["confirmed"]
    assert result["claims"][0]["trust"] == NEEDS_VERIFICATION and result["claims"][0]["author_kind"] == "other_bot"
    assert [r["action"] for r in result["addressed_to_bot"]] == ["skip", "skip"]


def test_a_failed_or_empty_model_answer_falls_back_to_the_rules() -> None:
    payload = thread(msg("1.0", "kim", "견적 유효기간은 30일로 하죠."))

    async def broken(messages):
        raise RuntimeError("endpoint down")

    for complete in (broken, model_answer({"decisions": [{"statement": "x", "evidence_ts": ["1.0"], "quote": "없는 말"}]})):
        result = asyncio.run(understand(payload, complete))
        assert result["method"] == "rules" and result["decisions"][0]["statement"] == "견적 유효기간은 30일로 하죠."


def test_the_question_bundle_keeps_to_its_budget() -> None:
    result = {"contests": [{"subject": "baryon", "predicate": "역할", "values": [{"by": "a", "value": "문서 담당"}, {"by": "b", "value": "없음"}],
                            "evidence_ts": ["1.0", "2.0"]}],
              "open_questions": [{"question": "예산은 아직 안 정했어", "about": "예산", "ask": True, "evidence_ts": ["3.0"]},
                                 {"question": "다음 회의에서 정하겠습니다", "about": "장소", "ask": False, "evidence_ts": ["4.0"]}]}
    bundle = question_bundle(result)
    assert len(bundle) == 1 and "baryon의 역할" in bundle[0]["text"] and "예산" in bundle[0]["text"] and "장소" not in bundle[0]["text"]
    assert bundle[0]["evidence_ts"] == ["1.0", "2.0", "3.0"] and question_bundle(result, budget=0) == []


# --------------------------------------------------------------------------- the API and the loop


def test_the_api_reads_a_thread_without_storing_anything(monkeypatch) -> None:
    monkeypatch.setenv("ONTOLOGY_EXTRACTOR", "rules")
    client = TestClient(app)
    headers = {"X-User-Id": "slack-bot", "X-Organization-Id": "org-thread", "X-Roles": "agent"}
    response = client.post("/v1/loop/threads/understand", headers=headers, json=thread(msg("1.0", "kim", "운송비는 빼고 견적 내자")))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["method"] == "rules" and body["version"] == 1 and body["decisions"][0]["confirmed"]
    assert client.get("/v1/loop/stats", headers={**headers, "X-Roles": "admin"}).json()["actions"]["new"] == 0


def test_a_decision_the_thread_explains_is_not_a_gap() -> None:
    action = {"text": "견적 유효기간은 30일로 하죠", "context": f"kim: 며칠로 할까요?\n{loop.THREAD_REASON} 원자재 단가가 한 달 단위로 바뀌니까요."}
    verdict = asyncio.run(loop.judge_gap(action, [], [], None))
    assert verdict["explained"] and verdict["by"] == "스레드"
    assert not asyncio.run(loop.judge_gap({"text": "견적 유효기간은 30일로 하죠", "context": ""}, [], [], None))["explained"]
