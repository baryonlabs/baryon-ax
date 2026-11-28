"""Score how well the Slack bot and the loop understand one Slack thread (deterministic scorer, golden threads).

The ontology eval (ontology_eval.py) scores single questions. This one scores a whole thread: what it was for, what
was decided and by whom, what was only implied, what the ontology should gain, which questions are worth asking,
and which messages were meant for the bot at all. Same method: fixed inputs, one deterministic scorer, arms
compared on the same fixtures.

Fixtures
--------
One directory per thread. In the repository: ``eval/thread_fixtures/<name>/`` (synthetic, invented names and ids).
Private ones (real threads) are read from ``$THREAD_FIXTURES`` (default ``~/.baryon-ax/thread-fixtures``) and are
never committed: the repository is public.

    meta.json       {"channel": "C…", "channel_type": "channel", "bot_user_id": "U…", "bot_id": "B…",
                     "description": "…"}                                         — which bot is under test
    replies.json    Slack ``conversations.replies`` response, unchanged ({"ok": true, "messages": [...]}).
                    Edits show as ``edited``, deleted parents as ``subtype: tombstone``, reactions as ``reactions``.
    users.json      optional: {"U…": {"name": "…", "is_bot": false}}               — users.info, as a map
    ontology.json   optional: the ontology just before the thread (``terms`` / ``relations``, loop store shape;
                    ``terms`` may be a list or an id → term map)
    events.json     optional: the event stream in order (message, message_changed, message_deleted,
                    reaction_added). Without it, one ``message`` event per message is made from replies.json.
    expected.json   what a pipeline that understood the thread would produce (below)

Keyphrases: a *group* is a list of alternatives, at least one of which must occur in the text (case and spaces
ignored); a *phrase* is a list of groups that must all occur. Anywhere a phrase is expected a single string or a
flat list of strings (= one group) is accepted too.

expected.json::

    {
      "purpose": {"keyphrases": phrase, "alternatives": [phrase, ...], "description": "for the optional judge"},
      "decisions": [{"statement": phrase, "decided_by": ["U…" or name], "kind": "explicit" | "implicit",
                     "evidence_ts": ["…"]}],
      "tacit": [{"statement": phrase, "evidence_ts": ["…"]}],
      "ontology_change": {
        "must_add": [{"label": group, "definition": phrase?}],
        "must_merge_into": [{"label": group, "into": group}],   # the thread's word is an existing term
        "must_contest": [group],                               # left as [쟁점] until a person decides
        "must_not_contest": [group],                           # a false [쟁점]
        "relations_expected": [{"s": group, "p": group?, "o": group}]
      },
      "questions": {"max_count": 1, "must_cover_links": [phrase], "forbidden": [phrase]},
      "addressed_to_bot": {"respond": ["ts"], "silent": ["ts"]},
      "message_accounting": {"min_attribution": 0.9, "mentions": {"ts": ["name", ...]}, "deleted_ts": ["ts"]},
      "must_not_state": [phrase],                              # stale or deleted facts nobody may repeat
      "pass": {"decision_precision": 0.8, ...}                 # optional threshold overrides
    }

A prediction (what an arm produced) is::

    {"purpose": str, "decisions": [{"statement", "decided_by", "kind", "evidence_ts", "quote"?}],
     "tacit": [{"statement", "evidence_ts"}], "questions": [{"text", "evidence_ts"?}],
     "ontology_change": {"added": [{"label", "definition"}], "merged": [{"from", "into"}], "contested": [label],
                         "relations": [{"s", "p", "o"}]},
     "responded_ts": [ts], "accounted": [{"ts", "speaker", "text"}], "understood": {ts: text},
     "quotes": [{"ts", "text"}]}

Every ``evidence_ts`` must be a message of the thread and every quote must be in the message it cites.

Arms
----
    rule   today's pipeline with the model off: each event replayed through the bot's dispatch (app_mention →
           Responder.answer, subscribed-thread follow-ups), loop_desk.observe recording actions, the archive
           rendering the day's document, then a loop round (POST /v1/loop/rounds) in process. CI runs this.
    model  the same with an OpenAI-compatible model for the round (--model-url): terms are read from the
           archived document, gaps judged and questions written by the model. The mention path stays canned.
    judge  optional: a model judges whether the predicted purpose means the expected one (--judge-url).

Run from apps/api:
    uv run python eval/thread_eval.py                       # rule arm, repository + private fixtures
    uv run python eval/thread_eval.py --verbose --json /tmp/thread.json
    uv run python eval/thread_eval.py --model-url http://127.0.0.1:8020/v1 --model baryon-ax-qwen --no-thinking
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "src"))
SLACK_BOT = HERE.parents[2] / "examples" / "slack-bot"

REPO_FIXTURES = HERE / "thread_fixtures"
BASELINE = HERE / "thread_baseline.json"
DEFAULT_PRIVATE = "~/.baryon-ax/thread-fixtures"

DEFAULT_PASS = {
    "purpose": 1.0, "decision_precision": 0.8, "decision_recall": 0.8, "evidence_validity": 1.0, "quote_validity": 1.0,
    "attribution": 0.9, "mention_resolution": 1.0, "ontology_add_recall": 1.0, "ontology_merge_recall": 1.0,
    "ontology_contest_recall": 1.0, "false_contests": 0, "relation_recall": 1.0, "questions_within_budget": 1,
    "question_coverage": 1.0, "forbidden_questions": 0, "respond_recall": 1.0, "wrong_replies": 0, "stale_statements": 0,
}
LOWER_IS_BETTER = {"false_contests", "forbidden_questions", "wrong_replies", "stale_statements", "question_count"}


def private_root() -> Path:
    return Path(os.path.expanduser(os.getenv("THREAD_FIXTURES") or DEFAULT_PRIVATE))


# --------------------------------------------------------------------------- fixtures


@dataclass
class Fixture:
    name: str
    path: Path
    private: bool
    meta: dict
    messages: list[dict]
    users: dict
    ontology: dict
    events: list[dict]
    expected: dict

    @property
    def by_ts(self) -> dict[str, dict]:
        return {m["ts"]: m for m in self.messages}

    @property
    def human_ts(self) -> list[str]:
        return [m["ts"] for m in self.messages if is_human(m, self.users)]


def is_human(message: dict, users: dict | None = None) -> bool:
    if message.get("bot_id") or message.get("subtype") in ("tombstone", "bot_message"):
        return False
    return not (users or {}).get(message.get("user", ""), {}).get("is_bot", False)


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def load_fixture(path: Path, private: bool = False) -> Fixture:
    replies = _read(path / "replies.json", {})
    messages = sorted(replies.get("messages", []), key=lambda m: float(m.get("ts", 0)))
    return Fixture(name=("private:" if private else "") + path.name, path=path, private=private,
                   meta=_read(path / "meta.json", {}), messages=messages, users=_read(path / "users.json", {}),
                   ontology=_read(path / "ontology.json", {}), events=_read(path / "events.json", []),
                   expected=_read(path / "expected.json", {}))


def discover(include_repo: bool = True, include_private: bool = True) -> list[Fixture]:
    found = []
    roots = ([(REPO_FIXTURES, False)] if include_repo else []) + ([(private_root(), True)] if include_private else [])
    for root, private in roots:
        if not root.is_dir():
            continue
        for path in sorted(p for p in root.iterdir() if p.is_dir() and (p / "replies.json").exists() and (p / "expected.json").exists()):
            found.append(load_fixture(path, private))
    return found


# --------------------------------------------------------------------------- keyphrase matching


RAW_MENTION = re.compile(r"<@[UWB][A-Z0-9]+>")


def norm(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def as_phrase(spec: Any) -> list[list[str]]:
    """str → [[str]]; [str, …] → [[str, …]] (one group); [[…], […]] stays."""
    if spec is None or spec == []:
        return []
    if isinstance(spec, str):
        return [[spec]]
    if all(isinstance(item, str) for item in spec):
        return [list(spec)]
    return [[item] if isinstance(item, str) else list(item) for item in spec]


def as_group(spec: Any) -> list[str]:
    return [spec] if isinstance(spec, str) else [str(item) for item in spec or []]


def group_hit(text: str, group: list[str]) -> bool:
    flat = norm(text)
    return any(norm(alt) and norm(alt) in flat for alt in group)


def phrase_hit(text: str, spec: Any) -> bool:
    phrase = as_phrase(spec)
    return bool(phrase) and all(group_hit(text, group) for group in phrase)


def _ratio(hit: float, total: float) -> float | None:
    return round(hit / total, 3) if total else None


# --------------------------------------------------------------------------- scoring


def _resolve_people(values: Any, users: dict) -> set[str]:
    """User ids and names to one comparable set (ids and names both kept)."""
    names = {norm(info.get("name")): uid for uid, info in users.items() if info.get("name")}
    out = set()
    for value in [values] if isinstance(values, str) else values or []:
        value = re.sub(r"^slack:", "", str(value)).strip("<@>").split("|")[0]
        out.add(norm(value))
        if norm(value) in names:
            out.add(norm(names[norm(value)]))
        if value in users and users[value].get("name"):
            out.add(norm(users[value]["name"]))
    return out


def _match(expected: list[dict], predicted: list[dict], key: str, text_of: Callable[[dict], str]) -> list[tuple[dict, dict]]:
    """Greedy one-to-one match of expected items to predicted items by keyphrase."""
    used: set[int] = set()
    pairs = []
    for item in expected:
        for index, guess in enumerate(predicted):
            if index not in used and phrase_hit(text_of(guess), item[key]):
                used.add(index)
                pairs.append((item, guess))
                break
    return pairs


def score(fixture: Fixture, prediction: dict, judge: dict | None = None) -> dict:
    exp, users, by_ts = fixture.expected, fixture.users, fixture.by_ts
    m: dict[str, Any] = {}

    # purpose
    purpose = exp.get("purpose") or {}
    if purpose:
        text = prediction.get("purpose") or ""
        options = [purpose.get("keyphrases")] + list(purpose.get("alternatives") or [])
        m["purpose"] = 1.0 if text and any(phrase_hit(text, option) for option in options if option) else 0.0
        if judge is not None and fixture.name in judge:
            m["purpose_judge"] = judge[fixture.name]

    # decisions
    gold, guesses = exp.get("decisions") or [], prediction.get("decisions") or []
    pairs = _match(gold, guesses, "statement", lambda d: d.get("statement", ""))
    m["decisions_expected"], m["decisions_predicted"] = len(gold), len(guesses)
    m["decision_precision"] = _ratio(len(pairs), len(guesses)) if guesses else (1.0 if not gold else 0.0)
    m["decision_recall"] = _ratio(len(pairs), len(gold))
    if pairs:
        m["decided_by_accuracy"] = _ratio(sum(bool(_resolve_people(g.get("decided_by"), users) & _resolve_people(e.get("decided_by"), users))
                                              for e, g in pairs), len(pairs))
        m["decision_kind_accuracy"] = _ratio(sum(g.get("kind") == e.get("kind") for e, g in pairs if e.get("kind")),
                                             sum(1 for e, _ in pairs if e.get("kind")))
        m["decision_evidence_hit"] = _ratio(sum(bool(set(g.get("evidence_ts") or []) & set(e.get("evidence_ts") or []))
                                                for e, g in pairs), len(pairs))

    # tacit knowledge
    gold_tacit, guess_tacit = exp.get("tacit") or [], prediction.get("tacit") or []
    if gold_tacit or guess_tacit:
        tacit_pairs = _match(gold_tacit, guess_tacit, "statement", lambda d: d.get("statement", ""))
        m["tacit_recall"] = _ratio(len(tacit_pairs), len(gold_tacit))
        m["tacit_precision"] = _ratio(len(tacit_pairs), len(guess_tacit)) if guess_tacit else None

    # evidence and quotes: everything cited must exist
    refs = [ts for item in guesses + guess_tacit + (prediction.get("questions") or []) for ts in item.get("evidence_ts") or []]
    m["evidence_refs"] = len(refs)
    m["evidence_validity"] = _ratio(sum(ts in by_ts for ts in refs), len(refs))
    quotes = list(prediction.get("quotes") or []) + [{"ts": (d.get("evidence_ts") or [""])[0], "text": d["quote"]}
                                                      for d in guesses + guess_tacit if d.get("quote")]
    m["quote_validity"] = _ratio(sum(bool(q.get("text")) and q.get("ts") in by_ts and norm(q["text"]) in norm(by_ts[q["ts"]].get("text"))
                                     for q in quotes), len(quotes))

    # ontology change
    oc, change = exp.get("ontology_change") or {}, prediction.get("ontology_change") or {}
    added, merged = change.get("added") or [], change.get("merged") or []
    contested, relations = change.get("contested") or [], change.get("relations") or []
    if oc.get("must_add"):
        hits = sum(any(group_hit(term.get("label", ""), as_group(want["label"]))
                       and (not want.get("definition") or phrase_hit(term.get("label", "") + " " + term.get("definition", ""), want["definition"]))
                       for term in added) for want in oc["must_add"])
        m["ontology_add_recall"] = _ratio(hits, len(oc["must_add"]))
    if oc.get("must_merge_into"):
        hits = 0
        for want in oc["must_merge_into"]:
            into_ok = any(group_hit(item.get("into", ""), as_group(want["into"])) for item in merged)
            duplicated = any(group_hit(term.get("label", ""), as_group(want["label"]))
                             and not group_hit(term.get("label", ""), as_group(want["into"])) for term in added)
            hits += into_ok and not duplicated
        m["ontology_merge_recall"] = _ratio(hits, len(oc["must_merge_into"]))
    if oc.get("must_contest"):
        m["ontology_contest_recall"] = _ratio(sum(any(group_hit(label, as_group(want)) for label in contested)
                                                  for want in oc["must_contest"]), len(oc["must_contest"]))
    if oc.get("must_not_contest") is not None:
        m["false_contests"] = sum(any(group_hit(label, as_group(bad)) for bad in oc["must_not_contest"]) for label in contested)
    if oc.get("relations_expected"):
        hits = sum(any(group_hit(r.get("s", ""), as_group(want["s"])) and group_hit(r.get("o", ""), as_group(want["o"]))
                       and (not want.get("p") or group_hit(r.get("p", ""), as_group(want["p"]))) for r in relations)
                   for want in oc["relations_expected"])
        m["relation_recall"] = _ratio(hits, len(oc["relations_expected"]))
    m["ontology_added"] = len(added)

    # questions
    qx, asked = exp.get("questions") or {}, [q.get("text", "") for q in prediction.get("questions") or []]
    m["question_count"] = len(asked)
    if qx:
        if qx.get("max_count") is not None:
            m["questions_within_budget"] = int(len(asked) <= qx["max_count"])
        links = qx.get("must_cover_links") or []
        if links:
            m["question_coverage"] = _ratio(sum(any(phrase_hit(q, link) for q in asked) for link in links), len(links))
        m["forbidden_questions"] = sum(any(phrase_hit(q, bad) for bad in qx.get("forbidden") or []) for q in asked)

    # who was the bot meant to answer
    addressed, responded = exp.get("addressed_to_bot") or {}, set(prediction.get("responded_ts") or [])
    if addressed:
        respond, silent = set(addressed.get("respond") or []), set(addressed.get("silent") or [])
        m["respond_recall"] = _ratio(len(respond & responded), len(respond))
        m["wrong_replies"] = len(silent & responded)
        m["silent_accuracy"] = _ratio(len(silent - responded), len(silent))

    # message accounting: every human message read, attributed to the right person, mentions resolved
    acc = exp.get("message_accounting") or {}
    accounted = {item["ts"]: item for item in prediction.get("accounted") or [] if item.get("ts")}
    humans = [ts for ts in fixture.human_ts if ts not in set(acc.get("deleted_ts") or [])]
    m["attribution"] = _ratio(sum(ts in accounted and bool(_resolve_people(accounted[ts].get("speaker"), users)
                                                          & _resolve_people(by_ts[ts].get("user"), users)) for ts in humans), len(humans))
    pairs_m = [(ts, name) for ts, names in (acc.get("mentions") or {}).items() for name in names]
    understood = prediction.get("understood") or {}
    # a raw <@U…> is not resolved, even if the id happens to contain the name; <@U…|name> is
    readable = {ts: RAW_MENTION.sub(" ", text) for ts, text in understood.items()}
    m["mention_resolution"] = _ratio(sum(group_hit(readable.get(ts, ""), [name]) for ts, name in pairs_m), len(pairs_m))
    leaked = [ts for ts in acc.get("deleted_ts") or [] if ts in accounted]

    # stale or deleted facts repeated anywhere
    said = ([d.get("statement", "") for d in guesses + guess_tacit] + asked
            + [t.get("label", "") + " " + t.get("definition", "") for t in added])
    m["stale_statements"] = sum(any(phrase_hit(text, bad) for text in said) for bad in exp.get("must_not_state") or []) + len(leaked)

    # pass/fail against thresholds (only metrics this fixture defines)
    limits = {**DEFAULT_PASS, **(exp.get("pass") or {})}
    failing = []
    for key, limit in limits.items():
        value = m.get(key)
        if value is None:
            continue
        if (value > limit) if key in LOWER_IS_BETTER else (value < limit):
            failing.append(key)
    m["pass"] = not failing
    m["failing"] = failing
    return m


# --------------------------------------------------------------------------- rule arm: today's pipeline, replayed


class FakeWeb:
    """Slack WebClient stand-in: the thread as it was at the current event, users.info from the fixture,
    every post recorded."""

    def __init__(self, fixture: Fixture) -> None:
        self.fixture = fixture
        self.now = float("inf")
        self.posts: list[dict] = []
        self.visible: dict[str, dict] = {}  # ts → message as currently shown (edits applied, deletes removed)

    def _thread(self) -> list[dict]:
        return sorted((m for m in self.visible.values() if float(m["ts"]) <= self.now), key=lambda m: float(m["ts"]))

    def conversations_replies(self, channel: str = "", ts: str = "", limit: int = 200, **_: Any) -> dict:
        messages = [m for m in self._thread() if m["ts"] == ts or m.get("thread_ts") == ts]
        return {"ok": True, "messages": messages[:limit]}

    def conversations_history(self, channel: str = "", **_: Any) -> dict:
        thread = self._thread()
        parents = [dict(m, reply_count=sum(1 for r in thread if r.get("thread_ts") == m["ts"] and r["ts"] != m["ts"]))
                   for m in thread if not m.get("thread_ts") or m.get("thread_ts") == m["ts"]]
        return {"ok": True, "messages": parents}

    def conversations_info(self, channel: str = "", **_: Any) -> dict:
        return {"ok": True, "channel": {"id": channel, "name": self.fixture.meta.get("channel_name") or channel}}

    def users_info(self, user: str = "", **_: Any) -> dict:
        info = self.fixture.users.get(user)
        if not info:
            return {"ok": False, "error": "user_not_found"}
        return {"ok": True, "user": {"id": user, "name": info.get("name", ""), "real_name": info.get("name", ""),
                                     "is_bot": bool(info.get("is_bot")), "profile": {"display_name": info.get("name", "")}}}

    def chat_postMessage(self, **kwargs: Any) -> dict:
        ts = f"{9_000_000_000 + len(self.posts)}.000000"
        self.posts.append({"kind": "post", "ts": ts, **kwargs})
        return {"ok": True, "ts": ts, "channel": kwargs.get("channel")}

    def chat_postEphemeral(self, **kwargs: Any) -> dict:
        self.posts.append({"kind": "ephemeral", **kwargs})
        return {"ok": True}

    def chat_update(self, **kwargs: Any) -> dict:
        self.posts.append({"kind": "update", **kwargs})
        return {"ok": True}

    def __getattr__(self, name: str) -> Callable[..., dict]:  # reactions_add, files_upload_v2, … : accepted, ignored
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: {"ok": True}


class FakeDocuments:
    """The bits of DocumentService a loop round uses, holding the archived day."""

    @dataclass
    class Doc:
        content: str
        chunks: list[dict]

    def __init__(self) -> None:
        self.listed: list[dict] = []
        self.documents: dict[str, FakeDocuments.Doc] = {}

    def add(self, doc_id: str, title: str, markdown: str, chunk_chars: int = 1200) -> None:
        chunks = [{"text": markdown[i:i + chunk_chars]} for i in range(0, len(markdown), chunk_chars)] or [{"text": markdown}]
        self.documents[doc_id] = FakeDocuments.Doc(markdown, chunks)
        self.listed.append({"id": doc_id, "title": title, "created_at": "2026-01-01T00:00:00+00:00",
                            "allowed_roles": ["member", "admin"], "tags": ["slack", "slack-conversation"]})

    def list_for(self, principal: Any) -> list[dict]:
        return list(self.listed)

    def search(self, principal: Any, query: str, limit: int) -> list[dict]:
        from baryon.loop import tokens

        words = tokens(query)
        hits = []
        for listed in self.listed:
            for chunk in self.documents[listed["id"]].chunks:
                overlap = len(words & tokens(chunk["text"]))
                if overlap:
                    hits.append((overlap, {"title": listed["title"], "content": chunk["text"]}))
        return [hit for _, hit in sorted(hits, key=lambda pair: -pair[0])[:limit]]


class _Audit:
    def emit(self, *args: Any, **kwargs: Any) -> None:
        pass


class LoopApi:
    """The loop router in process (TestClient), standing in for the baryon-ax API the bot calls."""

    def __init__(self, store_dir: str, complete: Callable[[list[dict[str, str]]], Awaitable[str]] | None, organization: str) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from baryon.loop import LoopStore
        from baryon.loop_api import LoopDeps, build_router
        from baryon.main import principal_from_headers

        self.organization = organization
        self.store = LoopStore(store_dir)
        self.documents = FakeDocuments()

        async def no_model(messages: list[dict[str, str]]) -> str:
            raise RuntimeError("model disabled")

        deps = LoopDeps(principal=principal_from_headers, acting_service=lambda: None, documents=self.documents, audit=_Audit(),
                        complete=complete or no_model, model_ready=lambda: complete is not None,
                        publish=lambda *args: asyncio.sleep(0, result=""))
        app = FastAPI()
        app.include_router(build_router(deps, self.store))
        self.client = TestClient(app)

    def request(self, method: str, path: str, user: str, roles: str, **kwargs: Any):
        headers = {"X-User-Id": user, "X-Organization-Id": self.organization, "X-Roles": roles}
        return self.client.request(method, path, headers=headers, **kwargs)


class FakeBaryon:
    """BaryonClient stand-in: loop calls go to the in-process loop API; answers are canned (no retrieval)."""

    ANSWER = "(rule arm: 답변 생성 안 함)"

    def __init__(self, api: LoopApi) -> None:
        self.api = api
        self.asked: list[dict] = []  # what the mention path sent as the question
        self.current_ts = ""

    def as_user(self, method: str, path: str, slack_user: str, **kwargs: Any):
        return self.api.request(method, path, f"slack:{slack_user}", "member", **kwargs)

    def as_bot(self, method: str, path: str, **kwargs: Any):
        if path.startswith("/v1/loop"):
            return self.api.request(method, path, "slack-bot", "agent", **kwargs)
        raise RuntimeError(f"not available in the rule arm: {method} {path}")

    def ask(self, messages: list[dict], slack_user: str, thread_id: str, use_retrieval: bool = True):
        from baryon_slack_bot import Answer

        self.asked.append({"ts": self.current_ts, "question": (messages[-1]["content"] if messages else ""), "messages": messages})
        return Answer(text=self.ANSWER, citations=[], answered=False)

    def snippets(self, *args: Any, **kwargs: Any) -> dict:
        return {}

    def cited_snippets(self, *args: Any, **kwargs: Any) -> dict:
        return {}


def events_of(fixture: Fixture) -> list[dict]:
    """The fixture's event stream, or one message event per message (as posted, in order)."""
    channel = fixture.meta.get("channel", "C0000000000")
    channel_type = fixture.meta.get("channel_type", "channel")
    if fixture.events:
        return [{"channel": channel, "channel_type": channel_type, **event} for event in fixture.events]
    events = []
    for message in fixture.messages:
        if message.get("subtype") == "tombstone":
            continue
        event = {key: value for key, value in message.items() if key not in ("reactions", "reply_count", "reply_users", "replies", "latest_reply")}
        if event.get("thread_ts") == event["ts"]:
            event.pop("thread_ts")  # a thread's first message had no thread_ts when it was posted
        events.append({"type": "message", "channel": channel, "channel_type": channel_type, **event})
    return events


def _apply_to_view(web: FakeWeb, event: dict) -> None:
    subtype = event.get("subtype")
    if subtype == "message_changed":
        changed = event.get("message") or {}
        if changed.get("ts") in web.visible:
            web.visible[changed["ts"]] = {**web.visible[changed["ts"]], **changed}
    elif subtype == "message_deleted":
        web.visible.pop(event.get("deleted_ts", ""), None)
    elif event.get("type") == "reaction_added":
        target = web.visible.get((event.get("item") or {}).get("ts", ""))
        if target is not None:
            reactions = target.setdefault("reactions", [])
            entry = next((r for r in reactions if r["name"] == event.get("reaction")), None)
            if entry is None:
                entry = {"name": event.get("reaction"), "users": [], "count": 0}
                reactions.append(entry)
            entry["users"].append(event.get("user"))
            entry["count"] += 1
    elif event.get("type") == "message" and not subtype or subtype in ("file_share", "bot_message", "thread_broadcast"):
        message = {k: v for k, v in event.items() if k not in ("channel", "channel_type", "type")}
        thread_ts = event.get("thread_ts")
        if not thread_ts:  # the first message: replies point to it
            message["thread_ts"] = event["ts"]
        web.visible[event["ts"]] = message


def rule_arm(fixture: Fixture, complete: Callable[[list[dict[str, str]]], Awaitable[str]] | None = None) -> dict:
    """Replay the thread through today's bot and loop. Deterministic when complete is None."""
    if str(SLACK_BOT) not in sys.path:
        sys.path.append(str(SLACK_BOT))
    import baryon_slack_bot as bot
    import conversation_archive
    import loop_desk

    bot_user = fixture.meta.get("bot_user_id", "UBOT0000000")
    channel = fixture.meta.get("channel", "C0000000000")
    with tempfile.TemporaryDirectory() as store_dir:
        api = LoopApi(store_dir, complete, "org-thread-eval")
        before = _seed_ontology(api, fixture.ontology)
        baryon = FakeBaryon(api)
        web = FakeWeb(fixture)
        responder = bot.Responder(bot.Config(), baryon, bot_user)
        desk = loop_desk.LoopDesk(loop_desk.LoopConfig(everyone=True, interval_minutes=0), baryon, bot_user)
        archive = conversation_archive.ArchiveConfig(channels=frozenset({channel}), state_path=os.path.join(store_dir, "archive.json"))
        archiver = conversation_archive.ConversationArchive(archive, baryon, bot_user)

        responded: list[str] = []
        recorded: list[str] = []
        for event in events_of(fixture):
            web.now = float(event.get("event_ts") or event.get("ts") or (event.get("message") or {}).get("ts") or web.now)
            _apply_to_view(web, event)
            if event.get("type") != "message":
                continue
            archiver.observe(event)
            baryon.current_ts = event.get("ts", "")
            actions_before = len(api.store.load(api.organization)["actions"])
            desk.observe(event, web)
            if len(api.store.load(api.organization)["actions"]) > actions_before:
                recorded.append(event.get("ts", ""))
            if event.get("user") == bot_user or event.get("subtype") in ("message_changed", "message_deleted"):
                continue
            posts_before = len(web.posts)
            text = event.get("text") or ""
            if f"<@{bot_user}" in text:  # Slack sends app_mention (from people and from other bots alike)
                command = bot.strip_mentions(text)
                if loop_desk.wants_questions(command) or loop_desk.wants_certify_list(command):
                    web.chat_postMessage(channel=channel, thread_ts=event.get("thread_ts") or event["ts"], text="(loop command)")
                else:
                    responder.answer(event, web)
            elif responder.should_follow_up(event):
                responder.answer(event, web)
            if any(post["kind"] == "post" for post in web.posts[posts_before:]):
                responded.append(event["ts"])

        # the day's document, as the archive would write it, then one loop round
        web.now = float("inf")
        day = archiver.day_of(fixture.messages[0]["ts"]) if fixture.messages else "1970-01-01"
        _, markdown = archiver.render(web, channel, day)
        if markdown:
            api.documents.add("doc-archive", f"slack-{day}.md", markdown)
        round_response = api.request("POST", "/v1/loop/rounds", "slack-bot", "agent", json={"documents": 5, "chunks_per_document": 20})
        round_response.raise_for_status()
        data = api.store.load(api.organization)

    actions = sorted(data["actions"].values(), key=lambda a: a["created_at"])
    action_ts = dict(zip([a["id"] for a in actions], recorded))
    decisions = [{"statement": a["text"], "decided_by": a["actor"], "kind": "explicit",
                  "evidence_ts": [action_ts[a["id"]]] if a["id"] in action_ts else []} for a in actions]
    questions = [{"text": q["question"], "evidence_ts": [action_ts[q["action_id"]]] if q["action_id"] in action_ts else []}
                 for q in sorted(data["questions"].values(), key=lambda q: q["created_at"])]
    accounted, understood = _archive_lines(markdown, fixture)
    for item in baryon.asked:  # what the mention path read as the question
        understood[item["ts"]] = (understood.get(item["ts"], "") + "\n" + item["question"]).strip()
    return {
        "purpose": "",  # nothing in today's pipeline states a thread's purpose
        "decisions": decisions,
        "tacit": [],
        "questions": questions,
        "ontology_change": _ontology_change(before, data),
        "responded_ts": responded,
        "accounted": accounted,
        "understood": understood,
        "quotes": [],
        "trace": {"round": round_response.json(), "archive_chars": len(markdown), "bot_inputs": [a["question"] for a in baryon.asked]},
    }


def _seed_ontology(api: LoopApi, snapshot: dict) -> dict:
    terms = snapshot.get("terms") or {}
    if isinstance(terms, list):
        terms = {term["id"]: term for term in terms}
    relations = list(snapshot.get("relations") or [])

    def seed(data: dict) -> None:
        data["terms"].update(json.loads(json.dumps(terms)))
        data["relations"].extend(json.loads(json.dumps(relations)))

    api.store.update(api.organization, seed)
    return {"terms": json.loads(json.dumps(terms)), "relations": relations}


def _ontology_change(before: dict, after: dict) -> dict:
    from baryon.loop import is_contested

    old = before["terms"]
    added = [{"label": t["label"], "definition": t.get("definition", "")} for key, t in after["terms"].items() if key not in old]
    merged = [{"from": "", "into": t["label"]} for key, t in after["terms"].items()
              if key in old and len(t.get("sources") or []) > len(old[key].get("sources") or [])]
    contested = [t["label"] for key, t in after["terms"].items() if is_contested(t) and not (key in old and is_contested(old[key]))]
    labels = {key: t["label"] for key, t in after["terms"].items()}
    relations = [{"s": labels.get(r.get("s_id"), r.get("s", "")), "p": r.get("p", ""), "o": labels.get(r.get("o_id"), r.get("o", ""))}
                 for r in after["relations"][len(before["relations"]):]]
    return {"added": added, "merged": merged, "contested": contested, "relations": relations}


LINE = re.compile(r"^\s*- \d\d:\d\d <@([^>]+)>: (.*)$")


def _archive_lines(markdown: str, fixture: Fixture) -> tuple[list[dict], dict[str, str]]:
    """Map the archived document's lines back to messages: who it names as the speaker, what text it kept."""
    lines = [LINE.match(line) for line in (markdown or "").splitlines()]
    lines = [(match.group(1), match.group(2)) for match in lines if match]
    accounted, understood = [], {}
    used: set[int] = set()
    for message in fixture.messages:
        start = norm(re.sub(r"<(https?://[^|>]+)(?:\|[^>]*)?>", r"\1", message.get("text") or ""))[:24]
        for index, (speaker, text) in enumerate(lines):
            if index not in used and start and norm(text).startswith(start):
                used.add(index)
                accounted.append({"ts": message["ts"], "speaker": speaker, "text": text})
                understood[message["ts"]] = text
                break
    return accounted, understood


# --------------------------------------------------------------------------- model helpers (optional arms)


def openai_complete(url: str, model: str, no_thinking: bool, json_mode: bool = True) -> Callable[[list[dict[str, str]]], Awaitable[str]]:
    import httpx

    async def complete(messages: list[dict[str, str]]) -> str:
        async with httpx.AsyncClient(timeout=180) as client:
            body: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            if no_thinking:
                body["chat_template_kwargs"] = {"enable_thinking": False}
            response = await client.post(f"{url.rstrip('/')}/chat/completions", json=body)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    return complete


JUDGE_PROMPT = """스레드의 목적을 두 가지로 적었다. [예측]이 [기대]와 같은 목적을 말하는지만 판정하라.
[기대] {expected}
[예측] {predicted}
JSON만 출력: {{"same": true 또는 false, "why": "한 문장"}}"""


async def judge_purposes(rows: list[tuple[Fixture, dict]], complete: Callable[[list[dict[str, str]]], Awaitable[str]]) -> dict[str, float]:
    from baryon.loop import _json

    verdicts = {}
    for fixture, prediction in rows:
        expected = (fixture.expected.get("purpose") or {}).get("description")
        if not expected:
            continue
        if not prediction.get("purpose"):
            verdicts[fixture.name] = 0.0
            continue
        data = _json(await complete([{"role": "user", "content": JUDGE_PROMPT.format(expected=expected, predicted=prediction["purpose"])}]))
        verdicts[fixture.name] = 1.0 if data.get("same") in (True, "true", "예") else 0.0
    return verdicts


# --------------------------------------------------------------------------- report

TABLE_KEYS = ["pass", "purpose", "decision_precision", "decision_recall", "decided_by_accuracy", "evidence_validity",
              "tacit_recall", "attribution", "mention_resolution", "ontology_add_recall", "ontology_merge_recall",
              "ontology_contest_recall", "false_contests", "relation_recall", "question_count", "questions_within_budget", "question_coverage",
              "forbidden_questions", "respond_recall", "wrong_replies", "stale_statements"]


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "✓" if value else "✗"
    return str(value)


def table(results: dict[str, dict], keys: list[str] = TABLE_KEYS) -> str:
    names = list(results)
    used = [key for key in keys if any(results[name].get(key) is not None for name in names)]
    lines = ["| metric | " + " | ".join(names) + " |", "| --- | " + " | ".join("---" for _ in names) + " |"]
    for key in used:
        lines.append(f"| {key} | " + " | ".join(_cell(results[name].get(key)) for name in names) + " |")
    return "\n".join(lines)


def evaluate(fixtures: list[Fixture], complete=None) -> tuple[dict[str, dict], dict[str, dict]]:
    predictions, scores = {}, {}
    for fixture in fixtures:
        predictions[fixture.name] = rule_arm(fixture, complete)
        scores[fixture.name] = score(fixture, predictions[fixture.name])
    return predictions, scores


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixtures", default="", help="one fixture directory instead of discovering them")
    parser.add_argument("--no-private", action="store_true", help=f"skip $THREAD_FIXTURES (default {DEFAULT_PRIVATE})")
    parser.add_argument("--model-url", default="", help="model arm: an OpenAI-compatible endpoint for the loop round")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--no-thinking", action="store_true", help="send enable_thinking=false (Qwen3.x on vLLM)")
    parser.add_argument("--judge-url", default="", help="judge arm: a model judges the predicted purpose")
    parser.add_argument("--judge-model", default="")
    parser.add_argument("--write-baseline", action="store_true", help=f"store repository fixture scores in {BASELINE.name}")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", default="", help="write predictions and scores here (may contain private thread text)")
    args = parser.parse_args()

    fixtures = [load_fixture(Path(args.fixtures).expanduser(), private=not Path(args.fixtures).resolve().is_relative_to(HERE))] \
        if args.fixtures else discover(include_private=not args.no_private)
    if not fixtures:
        print("no fixtures found")
        return 1
    complete = openai_complete(args.model_url, args.model, args.no_thinking) if args.model_url else None
    predictions, scores = evaluate(fixtures, complete)
    if args.judge_url:
        judge_complete = openai_complete(args.judge_url, args.judge_model or args.model, args.no_thinking)
        verdicts = asyncio.run(judge_purposes([(f, predictions[f.name]) for f in fixtures], judge_complete))
        for fixture in fixtures:
            scores[fixture.name] = score(fixture, predictions[fixture.name], verdicts)
    arm = "model" if complete else "rule"
    print(f"arm: {arm} · fixtures: {len(fixtures)} ({sum(f.private for f in fixtures)} private)\n")
    print(table(scores))
    for name, result in scores.items():
        if result["failing"]:
            print(f"\n{name}: failing {', '.join(result['failing'])}")
    if args.verbose:
        for fixture in fixtures:
            if fixture.private:
                print(f"\n## {fixture.name} (private: details not printed)")
                continue
            prediction = predictions[fixture.name]
            print(f"\n## {fixture.name}")
            print("  responded:", prediction["responded_ts"])
            print("  bot inputs:", prediction["trace"]["bot_inputs"])
            for decision in prediction["decisions"]:
                print("  decision:", decision["statement"][:100], decision["evidence_ts"])
            for question in prediction["questions"]:
                print("  question:", question["text"][:100])
    if args.write_baseline:
        repo = {name: {k: v for k, v in result.items() if k != "failing"} for name, result in scores.items() if not name.startswith("private:")}
        BASELINE.write_text(json.dumps({"arm": arm, "fixtures": repo}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {BASELINE}")
    if args.json:
        Path(args.json).write_text(json.dumps({"scores": scores, "predictions": predictions}, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
