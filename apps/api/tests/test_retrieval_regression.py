"""Vocabulary-mismatch regression: hybrid must find more evidence than lexical on paraphrased questions.

Replays vectors recorded from the production default embedder
(sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) through the real retrieval path
(DocumentService.search + in-process Qdrant), so CI measures the same numbers as
`uv run python eval/retrieval_eval.py` without downloading the model. After editing
eval/retrieval_paraphrase_ko.json, re-record with `--record-fixture`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "eval" / "retrieval_eval.py"


@pytest.fixture(scope="module")
def report():
    spec = importlib.util.spec_from_file_location("retrieval_eval", EVAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dataset = module.load_dataset()
    embed = module.FixtureEmbedder()
    store, principal = module.build_store(dataset, embed, embed.dimension)
    result = module.evaluate(store, principal, dataset, k=4)
    print("\n" + module.markdown(result, embed.model))
    return {mode: data["summary"] for mode, data in result["modes"].items()}


def test_dataset_is_mostly_vocabulary_mismatch(report) -> None:
    assert report["hybrid"]["paraphrase"]["n"] >= 30
    assert report["hybrid"]["control"]["n"] >= 8


def test_hybrid_finds_more_evidence_than_lexical_on_paraphrases(report) -> None:
    lexical, hybrid = report["lexical"]["paraphrase"], report["hybrid"]["paraphrase"]
    # Recorded: lexical 13/33 (0.394), hybrid 30/33 (0.909).
    assert hybrid["doc@k"] - lexical["doc@k"] >= 0.3
    assert hybrid["doc@k"] >= 0.85
    assert hybrid["mrr"] > lexical["mrr"]


def test_hybrid_keeps_lexical_wins(report) -> None:
    # Questions that reuse the source's own words: lexical finds 10/10; hybrid must not lose them.
    assert report["lexical"]["control"]["doc@k"] == 1.0
    assert report["hybrid"]["control"]["doc@k"] >= report["lexical"]["control"]["doc@k"]


def test_hybrid_beats_each_retriever_alone_overall(report) -> None:
    assert report["hybrid"]["all"]["doc@k"] > report["vector"]["all"]["doc@k"]
    assert report["hybrid"]["all"]["doc@k"] > report["lexical"]["all"]["doc@k"]
