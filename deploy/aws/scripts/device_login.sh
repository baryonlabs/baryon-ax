#!/usr/bin/env bash
# Sign in as an operator with the OAuth device flow and print an access token.
#
#   export BARYON_TOKEN=$(deploy/aws/scripts/device_login.sh https://demo.baryon-ax.baryon.ai)
#   BARYON_API_URL=https://demo.baryon-ax.baryon.ai python3 scripts/tenant_admin.py list
#
# The password is entered in the browser on the Keycloak page, never in this
# terminal. Uses the public client "baryon-ax-cli" (device grant only).
set -euo pipefail

base=${1:?public URL, e.g. https://demo.baryon-ax.baryon.ai}
realm_url="${base%/}/auth/realms/baryon-ax/protocol/openid-connect"
json() { python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))"; }

start=$(curl -fsS -d client_id=baryon-ax-cli -d scope=openid "$realm_url/auth/device")
device_code=$(echo "$start" | json device_code)
interval=$(echo "$start" | json interval); interval=${interval:-5}
echo "Open $(echo "$start" | json verification_uri_complete) and sign in." >&2

while true; do
  sleep "$interval"
  response=$(curl -sS -d client_id=baryon-ax-cli -d grant_type=urn:ietf:params:oauth:grant-type:device_code \
    -d device_code="$device_code" "$realm_url/token")
  token=$(echo "$response" | json access_token)
  if [ -n "$token" ]; then
    echo "$token"
    exit 0
  fi
  error=$(echo "$response" | json error)
  case "$error" in
    authorization_pending) ;;
    slow_down) interval=$((interval + 5)) ;;
    *) echo "login failed: $error" >&2; exit 1 ;;
  esac
done
