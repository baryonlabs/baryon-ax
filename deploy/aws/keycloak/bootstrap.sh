#!/usr/bin/env bash
# First-boot configuration for Keycloak on AWS. Runs as a non-essential sidecar in
# the Keycloak task after Keycloak is healthy, and is safe to run on every start:
# it only creates what is missing and re-applies per-environment settings.
#
# Environment (set by Terraform):
#   KC_INTERNAL_URL       e.g. http://127.0.0.1:8080/auth
#   KEYCLOAK_ADMIN_PASSWORD (secret)   bootstrap admin password
#   PUBLIC_URL            e.g. https://demo.baryon-ax.baryon.ai
#   BOT_CLIENT_SECRET     (secret)     client secret for baryon-ax-bot
#   BOT_ORGANIZATION      organization of the bot service account
#   USERS                 "username|organization|group1,group2;..."
#   USER_PASSWORD_<n>     (secret)     initial password of the n-th entry in USERS
set -euo pipefail

REALM=baryon-ax
KCADM=/opt/keycloak/bin/kcadm.sh
TEMPLATE=/opt/keycloak/baryon/realm.json

log() { echo "[bootstrap] $*"; }

for attempt in $(seq 1 60); do
  if "$KCADM" config credentials --server "$KC_INTERNAL_URL" --realm master \
      --user admin --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null 2>&1; then
    break
  fi
  [ "$attempt" = 60 ] && { log "Keycloak did not accept admin credentials"; exit 1; }
  sleep 5
done

if ! "$KCADM" get "realms/$REALM" >/dev/null 2>&1; then
  log "creating realm $REALM"
  "$KCADM" create realms -f "$TEMPLATE"
fi

id_of() { # id_of <path> <query...>
  local path=$1; shift
  "$KCADM" get "$path" -r "$REALM" "$@" --fields id --format csv --noquotes | head -n1
}

# Per-environment client settings (re-applied every start).
web_id=$(id_of clients -q clientId=baryon-ax-web)
"$KCADM" update "clients/$web_id" -r "$REALM" \
  -s "redirectUris=[\"$PUBLIC_URL/*\"]" -s "webOrigins=[\"$PUBLIC_URL\"]" \
  -s "attributes.\"post.logout.redirect.uris\"=$PUBLIC_URL/*"

bot_id=$(id_of clients -q clientId=baryon-ax-bot)
"$KCADM" update "clients/$bot_id" -r "$REALM" -s "secret=$BOT_CLIENT_SECRET"
bot_user=$("$KCADM" get "clients/$bot_id/service-account-user" -r "$REALM" --fields id --format csv --noquotes)
"$KCADM" update "users/$bot_user" -r "$REALM" -s "attributes.organization=[\"$BOT_ORGANIZATION\"]"

join_group() { # join_group <user id> <group name>
  local gid
  gid=$("$KCADM" get groups -r "$REALM" -q "search=$2" -q exact=true --fields id --format csv --noquotes | head -n1)
  [ -n "$gid" ] || { log "group $2 not found"; return 1; }
  "$KCADM" update "users/$1/groups/$gid" -r "$REALM" -s realm="$REALM" -s userId="$1" -s groupId="$gid" -n
}

for group in baryon-service baryon-member; do join_group "$bot_user" "$group"; done

# Users: created once with a temporary password; never overwritten afterwards.
index=0
IFS=';' read -r -a entries <<< "${USERS:-}"
for entry in "${entries[@]}"; do
  [ -n "$entry" ] || continue
  IFS='|' read -r username organization groups <<< "$entry"
  password_var="USER_PASSWORD_$index"
  index=$((index + 1))
  existing=$(id_of users -q "username=$username" -q exact=true)
  if [ -n "$existing" ]; then
    log "user $username exists"
    continue
  fi
  log "creating user $username ($organization)"
  user_id=$("$KCADM" create users -r "$REALM" -s "username=$username" -s enabled=true \
    -s "attributes.organization=[\"$organization\"]" -i)
  "$KCADM" set-password -r "$REALM" --userid "$user_id" --new-password "${!password_var}" --temporary
  IFS=',' read -r -a user_groups <<< "$groups"
  for group in "${user_groups[@]}"; do join_group "$user_id" "$group"; done
done

log "done"
