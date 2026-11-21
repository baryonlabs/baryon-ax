#!/usr/bin/env python3
"""Fail a pull request when security-sweep reports a blocking finding.

Usage:
    python3 .github/scripts/security_gate.py path/to/findings.json

Environment:
    SECURITY_FAIL_ON   comma-separated priorities that fail the job (default P0,P1)
    SECURITY_ALLOW_MISSING=1  do not fail when findings.json is absent
    SECURITY_CHECK_WITHDRAWN=0  skip the OSV lookup for withdrawn advisories

Advisories that their publisher has withdrawn (OSV ``withdrawn`` field) are listed
but never block: the report itself was retracted, so there is nothing to fix. If
OSV cannot be reached the finding keeps blocking (fail closed).

Findings are produced by baryonlabs/security-sweep (osv-scanner + trivy +
OSV verification). P0 = KEV or critical/high with a call trace; P1 = other
critical/high, EPSS >= 0.1, or a detected secret.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

FAIL_ON = {item.strip().upper() for item in os.getenv("SECURITY_FAIL_ON", "P0,P1").split(",") if item.strip()}
ALLOW_MISSING = os.getenv("SECURITY_ALLOW_MISSING", "0") == "1"
CHECK_WITHDRAWN = os.getenv("SECURITY_CHECK_WITHDRAWN", "1") != "0"
OSV_URL = "https://api.osv.dev/v1/vulns/"


def withdrawn_on(advisory_id: str) -> str | None:
    """Return the OSV withdrawal date for an advisory, or None (also on lookup failure)."""
    if not CHECK_WITHDRAWN or not advisory_id or not advisory_id.startswith(("GHSA-", "CVE-", "PYSEC-", "GO-", "RUSTSEC-")):
        return None
    try:
        with urllib.request.urlopen(OSV_URL + advisory_id, timeout=10) as response:
            record = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    withdrawn = record.get("withdrawn")
    return str(withdrawn) if withdrawn else None


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

    candidates = [finding for finding in findings if str(finding.get("priority") or "").upper() in FAIL_ON]
    blocking, withdrawn = [], []
    for finding in candidates:
        date = withdrawn_on(str(finding.get("id") or "")) if finding.get("kind") == "vuln" else None
        (withdrawn if date else blocking).append((finding, date))
    blocking = [finding for finding, _ in blocking]
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

    if withdrawn:
        emit("")
        emit(f"### ⚠️ {len(withdrawn)} finding(s) on withdrawn advisories (not blocking)")
        emit("")
        for finding, date in withdrawn:
            emit(f"- {finding.get('id', '')} · {finding.get('package', '')} · withdrawn {date[:10]}")

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
