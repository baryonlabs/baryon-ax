#!/usr/bin/env python3
"""Fail a pull request when security-sweep reports a blocking finding.

Usage:
    python3 .github/scripts/security_gate.py path/to/findings.json

Environment:
    SECURITY_FAIL_ON   comma-separated priorities that fail the job (default P0,P1)
    SECURITY_ALLOW_MISSING=1  do not fail when findings.json is absent

Findings are produced by baryonlabs/security-sweep (osv-scanner + trivy +
OSV verification). P0 = KEV or critical/high with a call trace; P1 = other
critical/high, EPSS >= 0.1, or a detected secret.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FAIL_ON = {item.strip().upper() for item in os.getenv("SECURITY_FAIL_ON", "P0,P1").split(",") if item.strip()}
ALLOW_MISSING = os.getenv("SECURITY_ALLOW_MISSING", "0") == "1"


def emit(line: str = "") -> None:
    print(line)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def main() -> int:
    if len(sys.argv) < 2:
        emit("security gate: findings.json path is required")
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        if ALLOW_MISSING:
            emit(f"security gate: {path} missing (allowed)")
            return 0
        emit(f"security gate: **{path} not found** — the sweep did not produce results")
        return 2

    try:
        findings = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        emit(f"security gate: invalid findings JSON: {error}")
        return 2
    if not isinstance(findings, list):
        emit("security gate: findings.json must be a list")
        return 2

    blocking = [finding for finding in findings if str(finding.get("priority") or "").upper() in FAIL_ON]
    counts: dict[str, int] = {}
    for finding in findings:
        priority = str(finding.get("priority") or "unrated")
        counts[priority] = counts.get(priority, 0) + 1

    emit("## security-sweep gate")
    emit("")
    emit(f"threshold: **{', '.join(sorted(FAIL_ON))}** · findings: {len(findings)}")
    emit("")
    emit("| priority | count |")
    emit("| --- | ---: |")
    for priority in ("P0", "P1", "P2", "P3", "unrated"):
        if priority in counts:
            emit(f"| {priority} | {counts[priority]} |")

    if blocking:
        emit("")
        emit(f"### ❌ {len(blocking)} blocking finding(s)")
        emit("")
        emit("| priority | kind | target | id | package | severity | location |")
        emit("| --- | --- | --- | --- | --- | --- | --- |")
        for finding in blocking[:50]:
            values = [
                finding.get("priority", ""),
                finding.get("kind", ""),
                finding.get("name", ""),
                finding.get("id", ""),
                finding.get("package", "") or "",
                finding.get("severity", ""),
                finding.get("location", "") or "",
            ]
            emit("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
        emit("")
        emit("Fix the findings or raise `SECURITY_FAIL_ON` after an explicit risk decision.")
        return 1

    emit("")
    emit("### ✅ no blocking findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
