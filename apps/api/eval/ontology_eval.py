"""Score ontology-pack extractors on a Korean gold set (deterministic, no model judge).

Arms, all on the same questions (the method follows NVIDIA auto-ontology-eval: same inputs, one
deterministic scorer, and the list of questions each arm fixed or broke):

    legacy  the old build_pack: regex terms, neighbours linked as related_to, no slots
    rules   baryon.ontology.rule_extract (the default)
    model   baryon.ontology.model_extract (model entities + question-pattern relations) against an
            OpenAI-compatible endpoint (--model-url)

Run from apps/api:
    uv run python eval/ontology_eval.py
    uv run python eval/ontology_eval.py --model-url http://127.0.0.1:8020/v1 --model baryon-ax-qwen --no-thinking
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from baryon.gaps import salient_terms
from baryon.ontology import Entity, Extraction, model_extract, rule_extract_question

GOLD = Path(__file__).with_name("ontology_gold_ko.json")
# A predicted entity that is a verb form or a question word is noise, whatever the gold set says.
NOISE = re.compile(r"(하면|나면|려면|해|해야|해요|했|하나|할까|돼|이야|야|어|지|까|얼마|언제|누구|어디|며칠|무엇|뭐)$")


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def legacy_extract(question: str) -> Extraction:
    return Extraction(entities=[Entity(name=term) for term in salient_terms(question)], slots=[], method="legacy")


def score_question(item: dict, extraction: Extraction) -> dict:
    predicted = [norm(entity.name) for entity in extraction.entities]
    must = [norm(name) for name in item["entities"]]
    allowed = set(must) | {norm(name) for name in item.get("ok", [])}
    correct = [name for name in predicted if name in allowed]
    noise = [name for name in predicted if NOISE.search(name)]
    found = [name for name in must if any(name == p or (name in p and p in allowed) for p in predicted)]
    slots_ok = 0
    for gold in item["slots"]:
        if any(slot.relation == gold["relation"] and slot.value_type == gold["value_type"]
               and any(norm(alt) in norm(slot.subject) or norm(slot.subject) in norm(alt) for alt in gold["subject"])
               for slot in extraction.slots):
            slots_ok += 1
    return {
        "predicted": len(predicted), "correct": len(correct), "noise": len(noise),
        "must": len(must), "found": len(found),
        "gold_slots": len(item["slots"]), "slots_ok": slots_ok, "pred_slots": len(extraction.slots),
        "pass": len(found) == len(must) and slots_ok == len(item["slots"]) and not noise,
        "entities": [entity.name for entity in extraction.entities],
        "slots": [f"{slot.subject}—{slot.relation}→?{slot.value_type}" for slot in extraction.slots],
    }


def summarize(rows: list[dict]) -> dict:
    total = lambda key: sum(row[key] for row in rows)
    return {
        "entity_precision": round(total("correct") / max(1, total("predicted")), 3),
        "entity_recall": round(total("found") / max(1, total("must")), 3),
        "noise_rate": round(total("noise") / max(1, total("predicted")), 3),
        "slot_recall": round(total("slots_ok") / max(1, total("gold_slots")), 3),
        "slot_precision": round(total("slots_ok") / max(1, total("pred_slots")), 3),
        "questions_passed": f"{sum(row['pass'] for row in rows)}/{len(rows)}",
    }


async def run(args: argparse.Namespace) -> int:
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))["questions"]
    arms: dict[str, list[dict]] = {"legacy": [], "rules": []}
    for item in gold:
        arms["legacy"].append(score_question(item, legacy_extract(item["q"])))
        arms["rules"].append(score_question(item, rule_extract_question(item["q"])))
    if args.model_url:
        import httpx

        async def complete(messages: list[dict[str, str]]) -> str:
            async with httpx.AsyncClient(timeout=120) as client:
                body = {"model": args.model, "messages": messages, "temperature": 0,
                        "response_format": {"type": "json_object"}}
                if args.no_thinking:  # Qwen3.x: answer directly instead of a reasoning preamble
                    body["chat_template_kwargs"] = {"enable_thinking": False}
                response = await client.post(f"{args.model_url.rstrip('/')}/chat/completions", json=body)
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]

        arms["model"] = []
        fallbacks = 0
        for item in gold:  # one question per call, as a pack of one gap would be built
            extraction = await model_extract([item["q"]], complete)
            fallbacks += extraction.method == "rules"
            arms["model"].append(score_question(item, extraction))
        print(f"model: {args.model} (fell back to rules on {fallbacks}/{len(gold)} questions)")

    metrics = {arm: summarize(rows) for arm, rows in arms.items()}
    keys = list(next(iter(metrics.values())))
    print("| metric | " + " | ".join(metrics) + " |")
    print("| --- | " + " | ".join("---" for _ in metrics) + " |")
    for key in keys:
        print(f"| {key} | " + " | ".join(str(metrics[arm][key]) for arm in metrics) + " |")

    baseline = arms["legacy"]
    for arm, rows in arms.items():
        if arm == "legacy":
            continue
        fixed = [gold[i]["q"] for i, row in enumerate(rows) if row["pass"] and not baseline[i]["pass"]]
        broke = [gold[i]["q"] for i, row in enumerate(rows) if not row["pass"] and baseline[i]["pass"]]
        print(f"\n{arm}: fixed {len(fixed)}, broke {len(broke)}")
        for question in broke:
            print("  broke:", question)
    if args.verbose:
        for arm, rows in arms.items():
            print(f"\n## {arm}")
            for item, row in zip(gold, rows):
                mark = "✓" if row["pass"] else "✗"
                print(f"{mark} {item['q']}\n    entities {row['entities']}\n    slots {row['slots']}")
    if args.json:
        Path(args.json).write_text(json.dumps({"metrics": metrics, "rows": arms}, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", default=str(GOLD), help="gold set (default ontology_gold_ko.json; held-out: ontology_holdout_ko.json)")
    parser.add_argument("--model-url", default="")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--no-thinking", action="store_true", help="send enable_thinking=false (Qwen3.x on vLLM)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", default="")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
