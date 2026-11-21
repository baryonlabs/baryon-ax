#!/usr/bin/env bash
# Re-resolve the docling parser pins to the newest versions allowed by requirements.in.
# Run this when the weekly "dependency pins" issue reports drift, then open a PR.
set -euo pipefail
cd "$(dirname "$0")/../deploy/parsers/docling"
tmp="$(mktemp)"
uv pip compile requirements.in \
  --python-version 3.12 --python-platform x86_64-manylinux_2_28 --no-header -o "$tmp"
mv "$tmp" requirements.txt
git --no-pager diff --stat -- requirements.txt
