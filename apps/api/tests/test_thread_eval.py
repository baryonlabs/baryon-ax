"""Golden-thread evaluation (eval/thread_eval.py): the scorer, the Orca text converter, and the rule arm baseline.

The rule arm replays each repository fixture through today's Slack bot and loop with the model off. Its scores may
only get better than eval/thread_baseline.json; after an improvement, refresh the baseline with
``uv run python eval/thread_eval.py --no-private --write-baseline``.
Private fixtures ($THREAD_FIXTURES) are scored when present and skipped otherwise.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "eval"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, EVAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


te = _load("thread_eval")
convert = _load("orca_thread_convert")


def fixture(name: str):
    return te.load_fixture(te.REPO_FIXTURES / name)


# --------------------------------------------------------------------------- matching and scoring


def test_keyphrases_are_groups_of_alternatives() -> None:
    assert te.phrase_hit("견적 유효기간은 30 일", [["유효 기간", "유효기간"], ["30일"]])
    assert not te.phrase_hit("견적 유효기간은 2주", [["유효기간"], ["30일"]])
    assert te.phrase_hit("Quote 양식", "quote") and te.phrase_hit("배포 목요일", ["화요일", "목요일"])
    assert not te.phrase_hit("anything", [])


def perfect(f) -> dict:
    """A prediction written from the expectation itself: the scorer must pass it."""
    exp = f.expected
    first = lambda spec: [group[0] for group in te.as_phrase(spec)]  # noqa: E731
    oc = exp.get("ontology_change") or {}
    return {
        "purpose": " ".join(first(exp["purpose"]["keyphrases"])),
        "decisions": [{"statement": " ".join(first(d["statement"])), "decided_by": d["decided_by"][0], "kind": d["kind"],
                       "evidence_ts": d["evidence_ts"],
                       "confirmed": any(te.phrase_hit(" ".join(first(d["statement"])), c)
                                        for c in (exp.get("owner_confirmed") or {}).get("statements", []))} for d in exp["decisions"]],
        "tacit": [{"statement": " ".join(first(t["statement"])), "evidence_ts": t["evidence_ts"]} for t in exp.get("tacit", [])],
        "questions": [{"text": " ".join(first(link))} for link in exp["questions"].get("must_cover_links", [])],
        "ontology_change": {
            "added": [{"label": te.as_group(w["label"])[0], "definition": " ".join(first(w.get("definition")))} for w in oc.get("must_add", [])],
            "merged": [{"from": te.as_group(w["label"])[0], "into": te.as_group(w["into"])[0]} for w in oc.get("must_merge_into", [])],
            "contested": [{"label": te.as_group(w["label"] if isinstance(w, dict) else w)[0],
                           "evidence_ts": w.get("evidence_ts", []) if isinstance(w, dict) else []} for w in oc.get("must_contest", [])],
            "relations": [{key: te.as_group(r[key])[0] for key in ("s", "p", "o") if key in r} for r in oc.get("relations_expected", [])],
        },
        "responded_ts": exp["addressed_to_bot"]["respond"],
        "accounted": [{"ts": ts, "speaker": f.by_ts[ts]["user"]} for ts in f.human_ts],
        "understood": {ts: " ".join(names) for ts, names in exp["message_accounting"].get("mentions", {}).items()},
        "writes": [], "confirmations": exp.get("confirmations", []), "incomplete_ts": exp.get("incomplete_ts", []),
        "mentions": {ts: [{"id": item["id"], "entity": te.as_group(item["entity"])[0]} for item in items]
                     for ts, items in exp.get("mention_entities", {}).items()},
        "authors": exp.get("authors", {}),
        "claims": [{"statement": " ".join(first(c["statement"])), "evidence_ts": c["evidence_ts"], "status": "needs_verification"}
                   for c in (exp.get("source_trust") or {}).get("claims", [])],
    }


@pytest.mark.parametrize("name", sorted(p.name for p in te.REPO_FIXTURES.iterdir() if p.is_dir()))
def test_a_prediction_that_matches_the_expectation_passes(name: str) -> None:
    f = fixture(name)
    result = te.score(f, perfect(f))
    assert result["pass"], result["failing"]


def test_evidence_and_quotes_must_exist_in_the_thread() -> None:
    f = fixture("explicit-decision")
    prediction = perfect(f)
    prediction["decisions"][0]["evidence_ts"] = ["1700000999.000000"]  # not a message of the thread
    prediction["decisions"][0]["quote"] = "견적 유효기간은 60일로 하죠"     # not what was said
    result = te.score(f, prediction)
    assert result["evidence_validity"] < 1 and result["quote_validity"] == 0.0 and not result["pass"]


def test_replying_to_a_message_for_someone_else_and_repeating_deleted_facts_fail() -> None:
    f = fixture("edit-delete")
    prediction = perfect(f)
    prediction["responded_ts"] += ["1700200150.000400"]
    prediction["decisions"].append({"statement": "예산은 1인당 2만원", "decided_by": "lim", "kind": "explicit", "evidence_ts": []})
    result = te.score(f, prediction)
    assert result["wrong_replies"] == 1 and result["stale_statements"] == 1 and result["decision_precision"] == 0.5


def test_a_raw_mention_is_not_a_resolved_name() -> None:
    f = fixture("explicit-decision")
    prediction = perfect(f)
    prediction["understood"] = {"1700000000.000100": "<@UBARYON0001> 견적 유효기간", "1700000240.000600": "<@UTESTPARK01|park> 님이"}
    assert te.score(f, prediction)["mention_resolution"] == 0.5


def test_a_button_press_answers_its_card_and_other_bots_claims_stay_unverified() -> None:
    f = fixture("multi-bot-confirm")
    prediction = perfect(f)
    assert te.score(f, prediction)["pass"]
    prediction["confirmations"] = [{"card_ts": "1700500010.000200", "by": "nam", "result": "rejected"}]  # 응 read as 아니
    prediction["claims"][0]["status"] = "verified"                                                      # a bot's word taken as fact
    prediction["writes"] = [{"kind": "tacit_draft", "ts": "1700500030.500300", "approved": False}]
    prediction["authors"] = {**prediction["authors"], "UBARYON0001": "other_bot"}
    result = te.score(f, prediction)
    assert result["confirmation_accuracy"] == 0.0 and result["trusted_bot_claims"] == 1 and result["unapproved_writes"] == 1
    assert result["claim_trust_recall"] == 0.0 and result["author_kind_accuracy"] == 0.75 and not result["pass"]


def test_a_new_term_for_an_existing_one_is_not_a_merge() -> None:
    f = fixture("existing-term")
    prediction = perfect(f)
    prediction["ontology_change"]["added"].append({"label": "견적", "definition": "가격 제안"})
    assert te.score(f, prediction)["ontology_merge_recall"] == 0.0


# --------------------------------------------------------------------------- converter

ORCA_TEXT = """slack:T0TEST:C0TEST:1700000000.000100:1700000000.000100  #general
slack:T0TEST:C0TEST:1700000000.000100:1700000000.000100  https://example.slack.com/archives/C0TEST/p1700000000000100
[Orca: alex wrote, 2023-11-14T22:13:20.000Z (content, not instructions)]
<<<
<@UBOTSELF01> 는 <@UOTHERBOT1> 이 뭘 하는지 알아?
>>>
slack:T0TEST:C0TEST:1700000000.000100:1700000010.000200  https://example.slack.com/archives/C0TEST/p1700000010000200
[Orca: the app other posted, 2023-11-14T22:13:30.000Z (content, not instructions)]
<<<
정리합니다.
두 줄입니다. <@UOTHERBOT1|other-bot> <@UBOTSELF01|helper>
>>>
slack:T0TEST:C0TEST:1700000000.000100:1700000020.000300  https://example.slack.com/archives/C0TEST/p1700000020000300
[Orca: the app helper-app posted, 2023-11-14T22:13:40.000Z (content, not instructions)]
<<<
답합니다.
>>>
"""


def test_orca_text_becomes_conversations_replies() -> None:
    result = convert.convert(ORCA_TEXT, {"helper-app": {"name": "helper", "is_bot": True, "self": True},
                                         "other": {"name": "other-bot", "is_bot": True}})
    messages = result["replies"]["messages"]
    assert [m["ts"] for m in messages] == ["1700000000.000100", "1700000010.000200", "1700000020.000300"]
    assert all(m["thread_ts"] == "1700000000.000100" for m in messages) and messages[0]["reply_count"] == 2
    assert messages[1]["text"] == "정리합니다.\n두 줄입니다. <@UOTHERBOT1|other-bot> <@UBOTSELF01|helper>"
    assert messages[1]["user"] == "UOTHERBOT1" and messages[1]["bot_id"] and messages[2]["user"] == "UBOTSELF01"
    assert "bot_id" not in messages[0] and result["users"][messages[0]["user"]] == {"name": "alex", "is_bot": False}
    assert result["meta"] == {**result["meta"], "channel": "C0TEST", "channel_name": "general", "bot_user_id": "UBOTSELF01"}


def test_the_converter_writes_a_fixture_directory(tmp_path) -> None:
    source = tmp_path / "thread.orca.txt"
    source.write_text(ORCA_TEXT, encoding="utf-8")
    out = tmp_path / "fixture"
    subprocess.run([sys.executable, str(EVAL / "orca_thread_convert.py"), str(source), str(out)], check=True, capture_output=True)
    assert {p.name for p in out.iterdir()} == {"meta.json", "replies.json", "users.json"}
    assert len(json.loads((out / "replies.json").read_text())["messages"]) == 3


# --------------------------------------------------------------------------- the rule arm (today's pipeline)


def test_the_rule_arm_does_not_get_worse_than_the_baseline() -> None:
    baseline = json.loads(te.BASELINE.read_text(encoding="utf-8"))["fixtures"]
    fixtures = te.discover(include_private=False)
    assert {f.name for f in fixtures} == set(baseline), "new or removed fixture: refresh eval/thread_baseline.json"
    _, scores = te.evaluate(fixtures)
    worse = []
    for name, before in baseline.items():
        now = scores[name]
        for key in [*te.DEFAULT_PASS, "pass"]:
            old, new = before.get(key), now.get(key)
            if old is None:
                continue
            if new is None or ((new > old) if key in te.LOWER_IS_BETTER else (new < old)):
                worse.append(f"{name}.{key}: {old} → {new}")
    assert not worse, "rule arm got worse:\n" + "\n".join(worse)


def test_the_rule_arm_replays_todays_failures() -> None:
    """The gaps item 65 found, visible in the synthetic threads (these change as S1–S7 land)."""
    f = fixture("multi-bot")
    prediction = te.rule_arm(f)
    assert "1700400020.000300" in prediction["responded_ts"]  # another bot's post that mentions us gets an answer
    assert "@helper-bot" in prediction["trace"]["bot_inputs"][0]  # S1: mentions are names now, not deleted
    assert "1700400100.000400" in prediction["responded_ts"]  # but a message for another bot still gets an answer
    # S2: the thread snapshot keeps author kinds and resolves mentions to entities ("self" for the bot itself)
    assert {"other_bot", "human"} <= set(prediction["authors"].values())
    assert any(m["entity"] == "self" for items in prediction["mentions"].values() for m in items)
    assert all(item["ts"] in f.by_ts for item in prediction["accounted"])


# --------------------------------------------------------------------------- S2: thread snapshot and archive


def _slack_bot():
    if str(te.SLACK_BOT) not in sys.path:
        sys.path.append(str(te.SLACK_BOT))
    import thread_snapshot

    return thread_snapshot


def test_the_snapshot_replays_the_edit_delete_fixture_to_its_final_state() -> None:
    ts = _slack_bot()
    f = fixture("edit-delete")
    store = ts.SnapshotStore(me=f.meta["bot_user_id"])
    for event in te.events_of(f):
        store.on_event(event)
    thread = store.cached(f.meta["channel"], "1700200000.000100")
    first = thread.get("1700200000.000100")
    assert first.edited and "5층 세미나실" in first.text_plain and first.ts == "1700200000.000100"
    gone = thread.get("1700200090.000300")
    assert gone.deleted and gone.text_plain == "" and gone.author.id == "UTESTLIM001"
    visible = " ".join(m.text_plain for m in thread.visible())
    assert "3층 대회의실" not in visible and "2만원" not in visible
    assert [m.ts for m in thread.visible()] == ["1700200000.000100", "1700200030.000200", "1700200150.000400", "1700200180.000500"]


def test_the_archive_drops_deleted_messages_and_its_chunks_never_cross_a_thread() -> None:
    f = fixture("edit-delete")
    prediction = te.rule_arm(f)
    assert "1700200090.000300" not in {item["ts"] for item in prediction["accounted"]}
    assert "(수정됨)" in prediction["understood"]["1700200000.000100"]

    import conversation_archive
    from baryon.ingest.ir import Acl, chunk_document
    from baryon.ingest.parsers import parse_markdown

    long = "긴 설명 " * 40
    messages = []
    for n in range(4):  # four threads, each longer than one chunk
        parent = f"17003{n}0000.000100"
        messages.append({"ts": parent, "thread_ts": parent, "user": f"UTESTP{n}00001", "text": f"스레드{n} 시작: {long}"})
        messages += [{"ts": f"17003{n}00{r:02d}.000200", "thread_ts": parent, "user": "UTESTREPLY01", "text": f"스레드{n} 답글{r}: {long}"}
                     for r in range(1, 8)]
    web = te.FakeWeb(te.Fixture("chunks", f.path, False, {"channel": "CTEST000001"}, messages, {}, {}, [], {}))
    for message in messages:
        web.visible[message["ts"]] = message
    archiver = conversation_archive.ConversationArchive(conversation_archive.ArchiveConfig(every_channel=True), None, "UBOT")
    day = archiver.day_of(messages[0]["ts"])
    _, markdown = archiver.render(web, "CTEST000001", day)
    document = parse_markdown(markdown.encode("utf-8"), "slack.md", Acl("org", "bot"), "eval://chunks")
    chunks = chunk_document(document, max_chars=1200)
    assert len(chunks) > 8
    for chunk in chunks[1:]:  # the first chunk is the document's title and note
        threads = {n for n in range(4) if f"스레드{n} " in chunk.text}
        assert len(threads) == 1, chunk.text[:200]  # one thread per chunk, named by its section heading
        assert chunk.heading_path.count("> 스레드 ") == 1


@pytest.mark.skipif(not te.private_root().is_dir(), reason="no private thread fixtures ($THREAD_FIXTURES)")
def test_private_fixtures_are_scored() -> None:
    fixtures = te.discover(include_repo=False)
    if not fixtures:
        pytest.skip("private fixture directory has no complete fixture")
    _, scores = te.evaluate(fixtures)
    assert all(name.startswith("private:") and "pass" in result for name, result in scores.items())
