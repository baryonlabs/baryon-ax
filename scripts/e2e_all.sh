#!/usr/bin/env bash
# Everything, end to end, on one machine: every test suite, then live checks against a running stack.
#
#   scripts/e2e_all.sh                 # unit suites + live checks (skips live parts that are not running)
#   E2E_LIVE=0 scripts/e2e_all.sh      # unit suites only
#   E2E_REQUIRE_LIVE=1 ...             # fail instead of skip when the API or web app is not running
#
# Live checks expect the Compose stack (API on :8000, web on :3001) and, if present, the voice secretary
# (:18603) and the phone gateway (:9093). Network checks (reading a public web page) are skipped with
# E2E_NETWORK=0. Used locally and by the self-hosted runner workflow (.github/workflows/e2e-selfhosted.yml).
#
# Writes a summary table to stdout and, when GITHUB_STEP_SUMMARY is set, to the job summary.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${E2E_API:-http://127.0.0.1:8000}"
WEB="${E2E_BASE_URL:-http://127.0.0.1:3001}"
SECRETARY="${E2E_SECRETARY:-http://127.0.0.1:18603}"
GATEWAY="${E2E_GATEWAY:-127.0.0.1:9093}"
LIVE="${E2E_LIVE:-1}"
REQUIRE_LIVE="${E2E_REQUIRE_LIVE:-0}"
NETWORK="${E2E_NETWORK:-1}"
LOGS="${E2E_LOGS:-$(mktemp -d -t baryon-e2e)}"
mkdir -p "$LOGS"
RESULTS=()
FAILED=0

step() {  # step <name> <command…>: run, time, record pass/fail, keep the log
  local name="$1"; shift
  local log="$LOGS/$(echo "$name" | tr -cs 'A-Za-z0-9' '-' | sed 's/-$//').log" started=$SECONDS
  printf '▶ %s … ' "$name"
  if (cd "$ROOT" && "$@") >"$log" 2>&1; then
    RESULTS+=("| ✅ | $name | $((SECONDS - started))s | $(tail -n 1 "$log" | tr -d '|' | cut -c1-80) |")
    echo "ok ($((SECONDS - started))s)"
  else
    RESULTS+=("| ❌ | $name | $((SECONDS - started))s | see $log |")
    FAILED=$((FAILED + 1))
    echo "FAILED ($((SECONDS - started))s) — $log"
    tail -n 20 "$log" | sed 's/^/    /'
  fi
}

skip() { RESULTS+=("| ⏭ | $1 | – | $2 |"); echo "⏭ $1 — $2"; }

have() { command -v "$1" >/dev/null 2>&1; }

pytest_in() {  # pytest_in <dir> <uv args…> -- <pytest args…>
  local dir="$1"; shift
  local uv_args=() pytest_args=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do uv_args+=("$1"); shift; done
  [ "${1:-}" = "--" ] && shift
  pytest_args=("$@")
  (cd "$ROOT/$dir" && uv run -q "${uv_args[@]}" python -m pytest -q "${pytest_args[@]}")
}

# --------------------------------------------------------------------------------------------- unit suites

step "api (pytest)"            pytest_in apps/api --extra dev --
step "voice secretary"         pytest_in apps/secretary --no-project --with-requirements requirements.txt --with pytest --with httpx --
step "phone gateway"           pytest_in deploy/phone/gateway --no-project --with-requirements requirements.txt --with pytest -- test_gateway.py
step "stt sidecar"             pytest_in deploy/stt --no-project --with-requirements requirements.txt --with pytest --with httpx -- test_server.py
step "slack bot"               pytest_in examples/slack-bot --no-project --with-requirements requirements.txt --with pytest -- tests
step "repo-sync"               pytest_in examples/repo-sync --no-project --with httpx --with pytest -- tests
if have npm; then
  step "web (unit + build)"    bash -c "cd apps/web && npm ci --silent && npm test --silent && npm run build --silent"
else
  skip "web (unit + build)" "npm not installed"
fi
if have flutter; then
  step "voice app (flutter)"   bash -c "cd apps/voice_app && flutter pub get >/dev/null && flutter analyze --no-fatal-infos && flutter test"
else
  skip "voice app (flutter)" "flutter not installed"
fi
rm -f "$ROOT/apps/secretary/uv.lock"  # uv run creates one; the secretary installs from requirements.txt

# --------------------------------------------------------------------------------------------- live checks

live_ok() { curl -fsS -m 5 "$API/health" >/dev/null 2>&1; }

if [ "$LIVE" = "1" ] && live_ok; then
  step "live: api smoke"       python3 "$ROOT/scripts/e2e_live.py" --api "$API" --secretary "$SECRETARY" --gateway "$GATEWAY" \
                                 $([ "$NETWORK" = "1" ] || echo --no-network)
  if curl -fsS -m 5 "$WEB/" >/dev/null 2>&1 && [ -x "$ROOT/apps/web/node_modules/.bin/playwright" ]; then
    (cd "$ROOT/apps/web" && npx playwright install chromium >/dev/null 2>&1 || true)
    step "live: web smoke"     bash -c "cd apps/web && E2E_BASE_URL=$WEB npm run -s test:e2e"
    step "live: web buttons"   bash -c "cd apps/web && E2E_BASE_URL=$WEB npm run -s test:buttons"
    step "live: web connectors" bash -c "cd apps/web && E2E_BASE_URL=$WEB npm run -s test:connectors"
  else
    skip "live: web (playwright)" "web app not on $WEB or playwright not installed"
  fi
elif [ "$LIVE" = "1" ]; then
  if [ "$REQUIRE_LIVE" = "1" ]; then
    RESULTS+=("| ❌ | live checks | – | API not running on $API |"); FAILED=$((FAILED + 1))
  else
    skip "live checks" "API not running on $API (docker compose up -d)"
  fi
fi

# --------------------------------------------------------------------------------------------- summary

SUMMARY=$(printf '%s\n' "| | step | time | result |" "|---|---|---|---|" "${RESULTS[@]}")
echo
echo "$SUMMARY"
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && { echo "## End-to-end ($(hostname -s))"; echo; echo "$SUMMARY"; } >> "$GITHUB_STEP_SUMMARY"
echo
if [ "$FAILED" -gt 0 ]; then
  echo "❌ $FAILED step(s) failed. Logs: $LOGS"
  exit 1
fi
echo "✅ all steps passed. Logs: $LOGS"
