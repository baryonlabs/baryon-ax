#!/usr/bin/env bash
# Runs on the demo VM at every boot (GCE startup script) and on `demo.sh update`.
# Settings come from instance metadata, never from the repository:
#   git-ref, demo-host, demo-user, demo-password-hash, model-url, model-name, model-key (optional)
set -euo pipefail
exec > >(tee -a /var/log/baryon-demo.log) 2>&1
echo "== $(date -Is) baryon-ax demo start"

meta() { curl -fs -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" || true; }
DIR=/opt/baryon-ax
REF=$(meta git-ref); REF=${REF:-main}

if ! command -v docker >/dev/null; then
  apt-get update -y && apt-get install -y ca-certificates curl git
  curl -fsSL https://get.docker.com | sh
fi
if [ ! -d "$DIR/.git" ]; then
  git clone https://github.com/baryonlabs/baryon-ax.git "$DIR"
fi
git -C "$DIR" fetch --tags origin
git -C "$DIR" checkout -q "$REF"
git -C "$DIR" pull -q --ff-only origin "$REF" 2>/dev/null || true   # a tag or SHA stays as is
echo "code: $(git -C "$DIR" log --oneline -1)"

cd "$DIR"
[ -f .env ] || cp .env.example .env
setenv() { # key value: replace or append, value kept out of the log
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}
setenv BIND_HOST 127.0.0.1
setenv QDRANT_BIND_HOST 127.0.0.1
setenv DEMO_HOST "$(meta demo-host)"
setenv DEMO_USER "$(meta demo-user || echo demo)"
setenv DEMO_PASSWORD_HASH "'$(meta demo-password-hash)'"
setenv DEMO_MODEL_URL "$(meta model-url)"
setenv DEMO_MODEL_NAME "$(meta model-name)"
setenv DEMO_MODEL_KEY "$(meta model-key)"
chmod 600 .env

COMPOSE="docker compose -f docker-compose.yml -f deploy/gcp/docker-compose.demo.yml"
$COMPOSE up -d --build --remove-orphans
for _ in $(seq 60); do curl -fs localhost:8000/health >/dev/null && break; sleep 5; done

# Sample documents of a fictional company, once.
if [ ! -f /var/lib/baryon-demo-seeded ]; then
  H=(-H "X-User-Id: demo-admin" -H "X-Organization-Id: local-org" -H "X-Roles: admin")
  for file in deploy/gcp/demo-docs/*.md; do
    curl -fs "${H[@]}" -F "file=@$file" -F allowed_roles=member,admin -F category=demo -F tags=demo localhost:8000/v1/documents >/dev/null && echo "seeded $file"
  done
  cp docs/getting-started-ko.md "/tmp/Baryon AX 사용 안내.md"
  curl -fs "${H[@]}" -F "file=@/tmp/Baryon AX 사용 안내.md" -F allowed_roles=member,admin -F category=guide \
       -F "tags=guide,기능,사용법,MCP" localhost:8000/v1/documents >/dev/null && echo "seeded guide"
  touch /var/lib/baryon-demo-seeded
fi
echo "== $(date -Is) ready: https://$(meta demo-host)/"
