"""Move an ontology store to schema 2 (S4: one ontology per organization). Run once at deploy time.

Before S4 an organization's knowledge was split over two places under ``$ONTOLOGY_STORE``:

    ont_<id>.json        one gap pack per file (gaps.PackStore), any organization
    loop/<org>.json      the loop's terms, relations, actions, questions (loop.LoopStore, schema 1)

After it there is one file per organization, ``loop/<org>.json`` (schema 2): the loop's data, every term typed and
with its definition as a claim, and the packs under ``packs`` with their entities merged into the same terms
(``ontology_merge.merge_pack``).

What a run does, in order:

1. Finds the work: pack files still in the root, loop files not on schema 2. Nothing to do → exits, writes nothing.
2. **Backup first**: ``$ONTOLOGY_STORE/backups/ontology-v1-<UTC time>.tar.gz`` with every file it will touch. No
   backup, no change.
3. Upgrades each loop file (``ontology_merge.upgrade``), merges each pack into its organization's file, then moves
   the pack file to ``$ONTOLOGY_STORE/migrated/``. Files are replaced atomically (write, then rename).

Idempotent: a second run finds nothing to do. A run stopped half way can be run again: a pack merged twice adds
nothing (sources are keyed by pack id), and its file is moved only after its organization's file is saved.

    uv run python -m baryon.ontology_migrate --store /data/ontology --dry-run
    uv run python -m baryon.ontology_migrate --store /data/ontology
    uv run python -m baryon.ontology_migrate --store /data/ontology --restore /data/ontology/backups/ontology-v1-….tar.gz
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from . import ontology_merge
from .loop import LoopStore


def plan(root: Path, loop_dir: Path) -> dict:
    packs = sorted(path for path in root.glob("ont_*.json") if path.is_file())
    stale = []
    for path in sorted(loop_dir.glob("*.json")) if loop_dir.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("schema_version") != ontology_merge.SCHEMA_VERSION:
            stale.append(path)
    return {"packs": packs, "loop_files": stale}


def _arcname(path: Path, loop_dir: Path) -> str:
    return f"loop/{path.name}" if path.parent == loop_dir else f"packs/{path.name}"


def backup(root: Path, loop_dir: Path, packs: list[Path], stamp: str) -> Path:
    """Every pack file and every organization file (a pack may be merged into any of them), checked after writing."""
    target_dir = root / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"ontology-v1-{stamp}.tar.gz"
    files = sorted(set(packs) | set(loop_dir.glob("*.json") if loop_dir.exists() else []))
    with tarfile.open(target, "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=_arcname(path, loop_dir))
    with tarfile.open(target, "r:gz") as archive:  # a backup that cannot be read is no backup
        names = set(archive.getnames())
    if any(_arcname(path, loop_dir) not in names for path in files):
        raise RuntimeError("backup is incomplete")
    return target


def migrate(root: str | Path, dry_run: bool = False, stamp: str | None = None, loop_dir: str | Path | None = None) -> dict:
    root = Path(root)
    loop_dir = Path(loop_dir) if loop_dir else root / "loop"
    work = plan(root, loop_dir)
    report = {"store": str(root), "packs": len(work["packs"]), "loop_files": len(work["loop_files"]), "dry_run": dry_run,
              "backup": "", "organizations": {}, "errors": []}
    if not work["packs"] and not work["loop_files"]:
        report["status"] = "nothing_to_do"
        return report
    if dry_run:
        report["status"] = "dry_run"
        return report
    stamp = stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report["backup"] = str(backup(root, loop_dir, work["packs"], stamp))
    store = LoopStore(loop_dir)
    for path in work["loop_files"]:  # load() upgrades; save writes schema 2
        organization = path.stem
        store.update(organization, lambda data: None)
        report["organizations"].setdefault(organization, {"upgraded": True, "packs": 0, "changes": 0})
    moved_dir = root / "migrated"
    for path in work["packs"]:
        try:
            pack = json.loads(path.read_text(encoding="utf-8"))
            organization = str(pack["organization_id"])
            pack_id = str(pack["id"])
        except (OSError, ValueError, KeyError) as error:
            report["errors"].append({"file": path.name, "error": type(error).__name__})
            continue
        if store._path(organization).stem != organization:  # an id the store would rename: refuse rather than guess
            report["errors"].append({"file": path.name, "error": "organization_id"})
            continue

        def put(data: dict, pack: dict = pack, pack_id: str = pack_id) -> int:
            if pack_id in data["packs"]:  # merged by an earlier, interrupted run: keep the newer copy
                return 0
            return ontology_merge.merge_pack(data, pack).count()

        changes = store.update(organization, put)
        row = report["organizations"].setdefault(organization, {"upgraded": False, "packs": 0, "changes": 0})
        row["packs"] += 1
        row["changes"] += changes
        moved_dir.mkdir(parents=True, exist_ok=True)
        os.replace(path, moved_dir / path.name)
    report["status"] = "migrated" if not report["errors"] else "migrated_with_errors"
    return report


def restore(root: str | Path, archive_path: str | Path, loop_dir: str | Path | None = None) -> int:
    """Put a backup back: the organization files as they were, the pack files back in the root."""
    root = Path(root)
    loop_dir = Path(loop_dir) if loop_dir else root / "loop"
    restored = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            folder, _, name = member.name.partition("/")
            if not member.isfile() or folder not in ("loop", "packs") or not name or "/" in name or name.startswith("."):
                continue  # only the two flat folders this module writes
            target = (loop_dir if folder == "loop" else root) / name
            source = archive.extractfile(member)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as out:
                shutil.copyfileobj(source, out)
            restored += 1
            moved = root / "migrated" / name
            if folder == "packs" and moved.exists():
                moved.unlink()
    return restored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", default=os.getenv("ONTOLOGY_STORE", "/tmp/baryon-ax-ontology"),
                        help="the ONTOLOGY_STORE directory (default: $ONTOLOGY_STORE)")
    parser.add_argument("--loop-store", default=os.getenv("LOOP_STORE", ""), help="the LOOP_STORE directory (default: <store>/loop)")
    parser.add_argument("--dry-run", action="store_true", help="say what would change; write nothing")
    parser.add_argument("--restore", default="", help="put this backup archive back instead of migrating")
    args = parser.parse_args(argv)
    if args.restore:
        count = restore(args.store, args.restore, args.loop_store or None)
        print(json.dumps({"status": "restored", "from": args.restore, "files": count}))
        return 0
    report = migrate(args.store, args.dry_run, loop_dir=args.loop_store or None)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
