"""Re-index stored documents into Qdrant (migration from lexical-only, or an embedding model change).

Run inside the API container, so it reads the same DOCUMENT_STORE, QDRANT_URL and EMBEDDING_* settings:

    docker compose exec api python -m baryon.reindex --dry-run
    docker compose exec api python -m baryon.reindex              # upsert every document
    docker compose exec api python -m baryon.reindex --prune      # also drop vectors of deleted documents
    docker compose exec api python -m baryon.reindex --recreate   # drop the collection first (model change)
    docker compose exec api python -m baryon.reindex --organization acme

Upserts are idempotent (point ids derive from document id and chunk number), so it is safe to run
while the API serves traffic and safe to re-run after an interruption. Exit codes: 0 ok,
1 some documents failed, 2 Qdrant is not enabled.
"""

from __future__ import annotations

import argparse
import sys
import time


def run(argv: list[str] | None = None, *, documents=None, vector=None, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog="python -m baryon.reindex", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--organization", help="only this organization's documents")
    parser.add_argument("--recreate", action="store_true", help="drop and recreate the collection before indexing")
    parser.add_argument("--prune", action="store_true", help="delete vectors whose document no longer exists")
    parser.add_argument("--dry-run", action="store_true", help="report what would be indexed and exit")
    args = parser.parse_args(argv)

    if documents is None or vector is None:
        from . import main as app
        documents, vector = app.documents, app.vector
    if not vector or not vector.enabled:
        reason = getattr(vector, "last_error", "") or "QDRANT_URL is not set"
        print(f"Qdrant vector backend is not enabled: {reason}", file=out)
        return 2
    if args.recreate and args.organization:
        parser.error("--recreate drops every organization's vectors; run it without --organization")

    selected = [document for document in list(documents.documents.values())
                if not args.organization or document.organization_id == args.organization]
    chunks = sum(len(document.chunks) or 1 for document in selected)
    print(f"collection={vector.collection} model={vector.model_name} points_before={vector.count()}", file=out)
    print(f"documents={len(selected)} (approx. chunks={chunks})", file=out)
    if args.dry_run:
        return 0

    if args.recreate:
        vector.recreate()
        print("collection recreated", file=out)

    started = time.perf_counter()
    failed: list[str] = []
    for number, document in enumerate(selected, start=1):
        if vector.index_document(document) is False:
            failed.append(document.id)
        if number % 50 == 0 or number == len(selected):
            print(f"  {number}/{len(selected)} indexed", file=out)

    pruned = 0
    if args.prune and not args.recreate:
        known = set(documents.documents)
        for document_id in sorted(vector.indexed_document_ids() - known):
            vector.delete_document(document_id)
            pruned += 1

    elapsed = time.perf_counter() - started
    print(f"done in {elapsed:.1f}s: indexed={len(selected) - len(failed)} failed={len(failed)} pruned={pruned} "
          f"points_after={vector.count()}", file=out)
    for document_id in failed:
        print(f"  failed: {document_id}", file=out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
