"""Utterance → intent: how often the bot's router (router.py) sends a Slack message where it belongs.

Each case in ``intent_cases_ko.json`` is one Slack message (mrkdwn as Slack sends it) and how it reached the bot:
who wrote it (``owner``: the secretary's owner; ``author: bot``: another bot), where (``thread``, ``dm``), whether it
called the bot (``mention``, default: the text has ``<@UBOT>``), whether the bot follows the thread (``subscribed``),
whether the secretary already works there (``secretary_thread``), files (``files``, ``thread_files``), and the
thread before it (``before``). The expected intent is one of router.INTENTS.

Every case runs through the bot's real decision (baryon_slack_bot.Dispatcher.route): the gate, then the rules, or
the model with ``--model-url``. Messages that stop mid-sentence are held as in the bot (TruncationHold, never
released here), so they count as ``silent``.

    uv run --no-project --with-requirements requirements.txt python eval/intent_eval.py
    uv run --no-project --with-requirements requirements.txt python eval/intent_eval.py --model-url http://127.0.0.1:8020/v1 --model baryon-ax-qwen
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import router as routing  # noqa: E402
from baryon_slack_bot import Config, Dispatcher, Responder  # noqa: E402
from secretary_bridge import BridgeConfig, SecretaryBridge, Thread  # noqa: E402
from slack_text import UserDirectory  # noqa: E402
from thread_snapshot import HoldConfig, ThreadSnapshot, TruncationHold  # noqa: E402

CASES = HERE / "intent_cases_ko.json"
_NO_HTTP = object()  # routing never calls the secretary
ME, OWNER, PERSON, OTHER_BOT = "UBOT", "UOWNER", "U1", "UORCA"
CHANNEL, ROOT, TS = "C1", "50.000000", "100.000000"


class _Web:
    def __init__(self, raw: list[dict]) -> None:
        self.raw = raw

    def conversations_replies(self, **_: Any) -> dict:
        return {"ok": True, "messages": self.raw, "has_more": False}

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: {"ok": True, "messages": []}


class _Snapshots:
    def __init__(self, snapshot: ThreadSnapshot) -> None:
        self.snapshot = snapshot

    def get(self, web, channel: str, thread_ts: str, refresh: bool = False) -> ThreadSnapshot:
        return self.snapshot

    def on_event(self, event: dict) -> None:
        return None


class _Enabled:
    def __init__(self) -> None:
        self.enabled = True
        self.config = self


def directory(users: dict) -> UserDirectory:
    found = UserDirectory()
    for user_id, info in users.items():
        found.remember(user_id, info.get("name", ""), is_bot=bool(info.get("is_bot")))
    return found


def case_event(case: dict) -> tuple[dict, bool, list[dict]]:
    """(the Slack event, whether it is an app_mention, the thread as conversations.replies returns it)."""
    user = OTHER_BOT if case.get("author") == "bot" else OWNER if case.get("owner") else PERSON
    if case.get("before"):
        user = case.get("user") or (OWNER if case.get("owner") else PERSON)
    event: dict[str, Any] = {"type": "message", "channel": "D1" if case.get("dm") else CHANNEL, "user": user, "ts": TS,
                             "text": case["text"], "channel_type": "im" if case.get("dm") else "channel"}
    if case.get("author") == "bot":
        event.update(bot_id="BORCA", bot_profile={"name": "orca-bot"})
    if case.get("thread") or case.get("before"):
        event["thread_ts"] = ROOT
    if case.get("files"):
        event["files"] = [{"id": "F1", "name": "report.docx", "size": 10}]
    mention = case.get("mention", f"<@{ME}" in case["text"])
    if mention:
        event["type"] = "app_mention"
    raw = [{"user": PERSON, "ts": ROOT, "text": "이번 주 진행 상황 공유합니다", "thread_ts": ROOT}]
    if case.get("thread_files"):
        raw[0]["files"] = [{"id": "F9", "name": "sales.xlsx", "size": 10, "url_private_download": "https://files.example/x"}]
    for index, item in enumerate(case.get("before") or []):
        raw.append({**item, "ts": f"{60 + index}.000000", "thread_ts": ROOT})
    if event.get("thread_ts"):
        raw.append({k: v for k, v in event.items() if k not in ("type", "channel", "channel_type")})
    return event, mention, raw


def decide(case: dict, users: dict, router: routing.Router) -> routing.Route:
    event, mention, raw = case_event(case)
    names = directory(users)
    responder = Responder(Config(), baryon=None, bot_user_id=ME, intake=_Enabled(), guide=_Enabled())
    responder.directory = names
    bridge = SecretaryBridge(BridgeConfig(url="http://secretary.invalid", key="k", users=frozenset({OWNER})), http=_NO_HTTP,
                             fetch=lambda item: b"")
    bridge.bot_user_id, bridge.directory = ME, names
    if case.get("secretary_thread"):
        bridge.threads[(event["channel"], event.get("thread_ts") or event["ts"])] = Thread(session="s")
    snapshots = None
    if event.get("thread_ts"):
        snapshot = ThreadSnapshot(event["channel"], ROOT, names, ME)
        snapshot.load(raw)
        snapshots = _Snapshots(snapshot)
        if case.get("subscribed"):
            responder.subscriptions.add(event["channel"], ROOT)
    hold = TruncationHold(lambda *_: None, HoldConfig(wait_seconds=20), timer=lambda *_: None)
    dispatcher = Dispatcher(responder, router, bridge=bridge, loop_desk=object(), snapshots=snapshots, hold=hold, background=False)
    return dispatcher.route(event, _Web(raw), mention)


def evaluate(cases: list[dict], users: dict, router: routing.Router) -> dict:
    rows, confusion = [], Counter()
    for case in cases:
        route = decide(case, users, router)
        ok = route.intent == case["expect"]
        confusion[(case["expect"], route.intent)] += 1
        rows.append({"id": case["id"], "expect": case["expect"], "got": route.intent, "ok": ok, "method": route.method,
                     "reason": route.reason, "handler": route.handler})
    secretary_wrong = sum((r["expect"] == routing.ACTION) != (r["handler"] == routing.SECRETARY) for r in rows)
    target = [r for r, c in zip(rows, cases) if c.get("source") == "target"]
    holdout = [r for r, c in zip(rows, cases) if c.get("split") == "holdout"]
    return {"cases": len(rows), "accuracy": round(sum(r["ok"] for r in rows) / len(rows), 3),
            "holdout_accuracy": round(sum(r["ok"] for r in holdout) / len(holdout), 3) if holdout else None,
            "secretary_misroutes": secretary_wrong,
            "target_ok": f"{sum(r['ok'] for r in target)}/{len(target)}",
            "per_intent": {intent: f"{confusion[(intent, intent)]}/{sum(v for (e, _), v in confusion.items() if e == intent)}"
                           for intent in routing.INTENTS if any(e == intent for e, _ in confusion)},
            "misses": [r for r in rows if not r["ok"]]}


def model_complete(url: str, model: str, no_thinking: bool):
    import httpx

    client = httpx.Client(timeout=60)

    def complete(prompt: str) -> str:
        body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
        if no_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        response = client.post(url.rstrip("/") + "/chat/completions", json=body)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    return complete


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", default=str(CASES))
    parser.add_argument("--model-url", default="", help="an OpenAI-compatible endpoint: the model classifies free-form messages")
    parser.add_argument("--model", default="baryon-ax-qwen")
    parser.add_argument("--no-thinking", action="store_true")
    parser.add_argument("--min-accuracy", type=float, default=0.9)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    router = routing.Router(model_complete(args.model_url, args.model, args.no_thinking) if args.model_url else None)
    result = evaluate(data["cases"], data.get("users") or {}, router)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(f"arm: {'model' if args.model_url else 'rules'} · cases: {result['cases']} · accuracy: {result['accuracy']}"
              f" (held-out {result['holdout_accuracy']}) · secretary misroutes: {result['secretary_misroutes']}"
              f" · target thread: {result['target_ok']}")
        for intent, hit in result["per_intent"].items():
            print(f"  {intent:16} {hit}")
        for miss in result["misses"]:
            print(f"  ✗ {miss['id']}: expected {miss['expect']}, got {miss['got']} ({miss['method']}: {miss['reason']})")
    return 0 if result["accuracy"] >= args.min_accuracy else 1


if __name__ == "__main__":
    raise SystemExit(main())
