"""Score the ontology merger (baryon.ontology_merge) on golden pairs: same, different, refine, contest.

Each pair is an existing statement in the ontology and a new one (label, value, source trust). The merger gets the
existing one first, then the new one, and its change set says what it did:

    contested            → contest   ([쟁점]: equals that disagree)
    added (a new term)   → different (the new label is another thing)
    refined              → refine    (more detail, a new slot, or a disagreement the source weight settled)
    merged / nothing new → same

Precision and recall per class, and their macro averages (the bar is 0.85 for both). ``false_contests`` counts
pairs that should not be a [쟁점] but became one. Rules only unless --model-url is given (then the model judges
same-as among the candidates, falling back to the rules on any failure).

    uv run python eval/merge_eval.py
    uv run python eval/merge_eval.py --verbose
    uv run python eval/merge_eval.py --model-url http://127.0.0.1:8020/v1 --model baryon-ax-qwen --no-thinking
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "src"))

from baryon import ontology_merge as om

GOLD = HERE / "ontology_merge_gold.json"
CLASSES = ("same", "different", "refine", "contest")
BAR = 0.85


def _claim(side: dict, ts: str) -> om.Claim:
    source = om.slack_source("CGOLD000000", "1700000000.000000", ts, side["value"], "someone", "human", side["trust"])
    return om.Claim(subject=side["label"], value=side["value"], predicate=side.get("slot", ""), trust=side["trust"], source=source)


def outcome(changes: om.ChangeSet) -> str:
    if changes.contested:
        return "contest"
    if changes.added:
        return "different"
    if changes.refined:
        return "refine"
    return "same"


async def predict(pair: dict, complete=None) -> tuple[str, int]:
    data: dict = om.upgrade({})
    om.merge(data, [_claim(pair["existing"], "1700000001.000100")])
    new = _claim(pair["new"], "1700000002.000200")
    same_as = await om.same_as_for(data, [new.subject], complete)
    changes = om.merge(data, [new], same_as=same_as)
    rerun = om.merge(data, [new], same_as=same_as).count()  # the same statement again: nothing
    return outcome(changes), rerun


def score(rows: list[tuple[str, str]]) -> dict:
    out: dict = {"pairs": len(rows), "accuracy": round(sum(g == p for g, p in rows) / len(rows), 3) if rows else 0.0}
    precisions, recalls = [], []
    for name in CLASSES:
        predicted = sum(p == name for _, p in rows)
        gold = sum(g == name for g, _ in rows)
        hit = sum(g == p == name for g, p in rows)
        precision = round(hit / predicted, 3) if predicted else 1.0
        recall = round(hit / gold, 3) if gold else 1.0
        out[f"{name}_precision"], out[f"{name}_recall"] = precision, recall
        precisions.append(precision)
        recalls.append(recall)
    out["macro_precision"] = round(sum(precisions) / len(precisions), 3)
    out["macro_recall"] = round(sum(recalls) / len(recalls), 3)
    out["false_contests"] = sum(p == "contest" and g != "contest" for g, p in rows)
    return out


def evaluate(complete=None, gold_path: Path = GOLD) -> tuple[dict, list[dict]]:
    pairs = json.loads(gold_path.read_text(encoding="utf-8"))["pairs"]
    details, rows, reruns = [], [], 0
    for pair in pairs:
        predicted, rerun = asyncio.run(predict(pair, complete))
        reruns += rerun
        rows.append((pair["expected"], predicted))
        details.append({"id": pair["id"], "expected": pair["expected"], "predicted": predicted, "why": pair.get("why", "")})
    result = score(rows)
    result["rerun_changes"] = reruns
    result["pass"] = result["macro_precision"] >= BAR and result["macro_recall"] >= BAR and reruns == 0
    return result, details


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", default=str(GOLD))
    parser.add_argument("--model-url", default="")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--no-thinking", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    complete = None
    if args.model_url:
        from thread_eval import openai_complete

        complete = openai_complete(args.model_url, args.model, args.no_thinking)
    result, details = evaluate(complete, Path(args.gold))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if args.verbose:
        for row in details:
            if row["expected"] != row["predicted"]:
                print(f"  {row['id']}: expected {row['expected']}, got {row['predicted']} — {row['why']}")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
