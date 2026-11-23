"""Vocabulary-mismatch retrieval benchmark: lexical vs vector vs hybrid on the same corpus.

The corpus and questions live in eval/retrieval_paraphrase_ko.json. Most questions ask for a fact
in words the source does not use ("쉬는 날" for "연차휴가"); `control` questions share the source's
vocabulary so a hybrid regression on lexical wins would show up.

Every arm runs through the production path: DocumentService.search with a real VectorIndex
(Qdrant in-process, ``QDRANT_URL=:memory:``), the same permission filter and the same fusion.

Run from apps/api:
    uv run python eval/retrieval_eval.py                       # real embedder (fastembed, downloads once)
    uv run python eval/retrieval_eval.py --embedder fixture    # recorded vectors, no download (CI)
    uv run python eval/retrieval_eval.py --record-fixture      # re-record tests/fixtures after changing the set

Scoring: a question "finds evidence" when its source document is among the top-k hits (k=4, the
chat default). chunk@k requires the exact source chunk; MRR uses the first hit of the source document.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DATASET = ROOT / "eval" / "retrieval_paraphrase_ko.json"
FIXTURE = ROOT / "tests" / "fixtures" / "retrieval_paraphrase_embeddings.json"
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODES = ("lexical", "vector", "hybrid")


def load_dataset(path: Path = DATASET) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _key(text: str, query: bool) -> str:
    return hashlib.sha1((("q:" if query else "p:") + text).encode("utf-8")).hexdigest()


class FastEmbedder:
    """The production default embedder (fastembed), with the same query/passage prefixes."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        from baryon.main import VectorIndex
        self.model = model
        self.index = VectorIndex(url="")
        self.index.model_name = model
        self.index.provider = "fastembed"

    def __call__(self, texts: list[str], query: bool) -> list[list[float]]:
        return self.index._embed(texts, query)


class RecordingEmbedder:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.vectors: dict[str, list[float]] = {}

    def __call__(self, texts: list[str], query: bool) -> list[list[float]]:
        vectors = self.inner(texts, query)
        for text, vector in zip(texts, vectors):
            self.vectors[_key(text, query)] = vector
        return vectors

    def dump(self, path: Path, model: str) -> None:
        import numpy as np
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = {key: base64.b64encode(np.asarray(vector, dtype=np.float16).tobytes()).decode("ascii")
                   for key, vector in sorted(self.vectors.items())}
        dimension = len(next(iter(self.vectors.values())))
        path.write_text(json.dumps({"model": model, "dimension": dimension, "dtype": "float16", "vectors": encoded},
                                   indent=0) + "\n", encoding="utf-8")


class FixtureEmbedder:
    """Replays vectors recorded from the real model, so CI measures the same numbers offline."""

    def __init__(self, path: Path = FIXTURE) -> None:
        import numpy as np
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.model = payload["model"]
        self.dimension = payload["dimension"]
        self.vectors = {key: np.frombuffer(base64.b64decode(value), dtype=np.float16).astype("float32").tolist()
                        for key, value in payload["vectors"].items()}

    def __call__(self, texts: list[str], query: bool) -> list[list[float]]:
        missing = [text for text in texts if _key(text, query) not in self.vectors]
        if missing:
            raise KeyError(f"No recorded vector for {missing[0]!r}; run eval/retrieval_eval.py --record-fixture")
        return [self.vectors[_key(text, query)] for text in texts]


def build_store(dataset: dict, embed, dimension: int = 384):
    """A DocumentService over the benchmark corpus with an in-process Qdrant index."""
    from baryon.main import DocumentService, Principal, VectorIndex

    previous = os.environ.get("EMBEDDING_DIMENSION")
    os.environ["EMBEDDING_DIMENSION"] = str(dimension)
    try:
        vector = VectorIndex(url=":memory:", collection="retrieval_bench", embed=embed)
    finally:
        if previous is None:
            os.environ.pop("EMBEDDING_DIMENSION", None)
        else:
            os.environ["EMBEDDING_DIMENSION"] = previous
    if not vector.enabled:
        raise RuntimeError(f"in-process Qdrant failed: {vector.last_error}")
    store = DocumentService(Path(tempfile.mkdtemp(prefix="baryon-retrieval-bench-")), vector=vector)
    principal = Principal("bench-user", "bench-org", {"member"})
    for item in dataset["documents"]:
        store.create(principal, item["title"], "\n\n".join(item["chunks"]), {"member"},
                     chunks=[{"text": text} for text in item["chunks"]])
    return store, principal


def evaluate(store, principal, dataset: dict, k: int = 4, modes=MODES) -> dict:
    """Per mode and question kind: doc@1, doc@k, chunk@k, MRR@k, plus per-question outcomes."""
    report: dict = {"k": k, "modes": {}}
    for mode in modes:
        rows = []
        for question in dataset["questions"]:
            hits = store.search(principal, question["q"], limit=k, mode=mode)
            titles = [hit["title"] for hit in hits]
            rank = titles.index(question["doc"]) + 1 if question["doc"] in titles else 0
            chunk_hit = any(hit["title"] == question["doc"] and hit["chunk"] == question["chunk"] for hit in hits)
            rows.append({"q": question["q"], "kind": question["kind"], "rank": rank, "chunk_hit": chunk_hit,
                         "returned": len(hits)})
        report["modes"][mode] = {"summary": _summarize(rows), "questions": rows}
    return report


def _summarize(rows: list[dict]) -> dict:
    summary = {}
    for kind in ("all", "paraphrase", "control"):
        selected = [row for row in rows if kind == "all" or row["kind"] == kind]
        if not selected:
            continue
        n = len(selected)
        summary[kind] = {
            "n": n,
            "doc@1": round(sum(row["rank"] == 1 for row in selected) / n, 3),
            "doc@k": round(sum(row["rank"] > 0 for row in selected) / n, 3),
            "chunk@k": round(sum(row["chunk_hit"] for row in selected) / n, 3),
            "mrr": round(sum(1 / row["rank"] for row in selected if row["rank"]) / n, 3),
            "found": sum(row["rank"] > 0 for row in selected),
        }
    return summary


def markdown(report: dict, model: str) -> str:
    lines = [f"Embedding model: `{model}` · top-k = {report['k']}", "",
             "| mode | set | n | doc@1 | doc@k | chunk@k | MRR |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for mode, data in report["modes"].items():
        for kind, row in data["summary"].items():
            lines.append(f"| {mode} | {kind} | {row['n']} | {row['doc@1']:.3f} | {row['doc@k']:.3f} "
                         f"({row['found']}/{row['n']}) | {row['chunk@k']:.3f} | {row['mrr']:.3f} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--embedder", choices=("fastembed", "fixture"), default="fastembed")
    parser.add_argument("--model", default=os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL))
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--record-fixture", action="store_true", help=f"write recorded vectors to {FIXTURE.relative_to(ROOT)}")
    parser.add_argument("--json", type=Path, help="write the full report (per-question ranks) here")
    args = parser.parse_args()

    dataset = load_dataset()
    if args.embedder == "fixture":
        embed = FixtureEmbedder()
        model, dimension = embed.model, embed.dimension
    else:
        embed = RecordingEmbedder(FastEmbedder(args.model))
        model, dimension = args.model, len(embed(["warmup"], False)[0])
    store, principal = build_store(dataset, embed, dimension)
    report = evaluate(store, principal, dataset, k=args.k)
    print(markdown(report, model))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.record_fixture:
        if args.embedder != "fastembed":
            parser.error("--record-fixture needs --embedder fastembed")
        embed.vectors.pop(_key("warmup", False), None)
        embed.dump(FIXTURE, model)
        print(f"\nrecorded {len(embed.vectors)} vectors to {FIXTURE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
