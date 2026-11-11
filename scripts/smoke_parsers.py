#!/usr/bin/env python3
"""Smoke check for parser sidecars (kordoc / docling / Tika).

    python3 scripts/smoke_parsers.py \
        --kordoc http://127.0.0.1:18080 --docling http://127.0.0.1:18000

    # also parse a real file
    python3 scripts/smoke_parsers.py --kordoc http://127.0.0.1:18080 --file sample.hwpx

Exit code is non-zero when a configured sidecar is unhealthy or a parse fails.
Override URLs with KORDOC_URL / DOCLING_URL / TIKA_URL.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def check_health(httpx, name: str, base: str, timeout: float) -> bool:
    try:
        response = httpx.get(base.rstrip("/") + "/health", timeout=timeout)
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"FAIL {name}: {type(error).__name__}: {error}")
        return False
    if response.status_code >= 400:
        print(f"FAIL {name}: HTTP {response.status_code}")
        return False
    print(f"ok   {name}: {response.text.strip()[:160]}")
    return True


def parse(httpx, name: str, base: str, path: Path, timeout: float) -> bool:
    payload = path.read_bytes()
    try:
        if name == "tika":
            response = httpx.put(
                base.rstrip("/") + "/tika",
                content=payload,
                headers={"Accept": "text/plain", "Content-Type": "application/octet-stream"},
                timeout=timeout,
            )
            response.raise_for_status()
            print(f"ok   {name} parse: {len(response.text)} chars")
            return True
        response = httpx.post(
            base.rstrip("/") + "/parse",
            files={"file": (path.name, payload, "application/octet-stream")},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as error:  # noqa: BLE001
        print(f"FAIL {name} parse: {type(error).__name__}: {error}")
        return False
    blocks = data.get("blocks") or []
    markdown = data.get("markdown") or ""
    if not blocks and not markdown:
        print(f"FAIL {name} parse: no blocks or markdown")
        return False
    print(f"ok   {name} parse: blocks={len(blocks)} markdown={len(markdown)} chars metadata={data.get('metadata')}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kordoc", default=os.getenv("KORDOC_URL", ""))
    parser.add_argument("--docling", default=os.getenv("DOCLING_URL", ""))
    parser.add_argument("--tika", default=os.getenv("TIKA_URL", ""))
    parser.add_argument("--file", default=None, help="optional document to parse (sent to the first healthy sidecar)")
    parser.add_argument("--target", choices=("kordoc", "docling", "tika"), default=None)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    try:
        import httpx
    except ModuleNotFoundError:
        print("httpx is required: pip install httpx", file=sys.stderr)
        return 2

    endpoints = {name: url for name, url in (("kordoc", args.kordoc), ("docling", args.docling), ("tika", args.tika)) if url}
    if not endpoints:
        print("no sidecar URL configured (KORDOC_URL / DOCLING_URL / TIKA_URL)", file=sys.stderr)
        return 2

    healthy = {name: check_health(httpx, name, url, args.timeout) for name, url in endpoints.items()}
    ok = all(healthy.values())
    if args.file and ok:
        path = Path(args.file)
        target = args.target or next(name for name, alive in healthy.items() if alive)
        if target not in endpoints:
            print(f"target {target} is not configured", file=sys.stderr)
            return 2
        ok = parse(httpx, target, endpoints[target], path, args.timeout)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
