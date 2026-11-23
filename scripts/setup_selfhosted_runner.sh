#!/usr/bin/env bash
# Install a GitHub Actions self-hosted runner for the e2e workflow (.github/workflows/e2e-selfhosted.yml)
# on a Mac (Apple silicon). Run it once on the Mac mini, as the user the runner should run as:
#
#   scripts/setup_selfhosted_runner.sh                 # baryonlabs/baryon-ax, runner name = this host
#   RUNNER_REPO=owner/repo RUNNER_NAME=mini scripts/setup_selfhosted_runner.sh
#   scripts/setup_selfhosted_runner.sh --remove        # unregister and stop the service
#
# Needs: gh (logged in as a repository admin), Docker Desktop running, uv, Node.js; Flutter is optional
# (the voice-app step is skipped without it). The runner registers with the label baryon-e2e and runs as a
# launchd service, so it survives logout and reboot.
#
# Safety (the repository is public): the e2e workflow has no pull_request trigger, so code from forks never
# runs here. Keep it that way — never add pull_request / pull_request_target to a workflow that uses
# runs-on: self-hosted. Use a separate macOS user without your personal keys if the machine is shared.
set -euo pipefail

REPO="${RUNNER_REPO:-baryonlabs/baryon-ax}"
NAME="${RUNNER_NAME:-$(hostname -s)}"
DIR="${RUNNER_DIR:-$HOME/actions-runner-baryon}"
LABELS="baryon-e2e"

need() { command -v "$1" >/dev/null 2>&1 || { echo "❌ $1 is required: $2"; exit 1; }; }

if [ "${1:-}" = "--remove" ]; then
  cd "$DIR"
  ./svc.sh stop || true
  ./svc.sh uninstall || true
  token=$(gh api -X POST "repos/$REPO/actions/runners/remove-token" -q .token)
  ./config.sh remove --token "$token"
  echo "✅ runner removed from $REPO"
  exit 0
fi

[ "$(uname -s)-$(uname -m)" = "Darwin-arm64" ] || { echo "❌ this script is for Apple silicon Macs"; exit 1; }
need gh "brew install gh && gh auth login"
need docker "install Docker Desktop and start it"
need uv "brew install uv"
need node "brew install node"
need curl "should come with macOS"
docker info >/dev/null 2>&1 || { echo "❌ Docker is not running"; exit 1; }
gh api "repos/$REPO" -q .permissions.admin | grep -q true || { echo "❌ gh is not logged in as an admin of $REPO"; exit 1; }
command -v flutter >/dev/null 2>&1 || echo "ℹ️  flutter not found: the voice-app tests will be skipped"

if gh api "repos/$REPO/actions/runners" -q ".runners[].name" | grep -qx "$NAME"; then
  echo "ℹ️  a runner named $NAME is already registered with $REPO; remove it first (--remove) to reinstall"
  exit 0
fi

# The official runner release for macOS arm64, checked against the SHA-256 published in its release notes.
release=$(gh api repos/actions/runner/releases/latest)
version=$(echo "$release" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"].lstrip("v"))')
asset="actions-runner-osx-arm64-$version.tar.gz"
expected=$(echo "$release" | python3 -c 'import json,re,sys; body=json.load(sys.stdin)["body"]; m=re.search(r"BEGIN SHA osx-arm64 -->\s*([0-9a-f]{64})", body); print(m.group(1) if m else "")')
[ -n "$expected" ] || { echo "❌ could not find the published checksum for $asset"; exit 1; }
mkdir -p "$DIR" && cd "$DIR"
curl -fsSL -o "$asset" "https://github.com/actions/runner/releases/download/v$version/$asset"
actual=$(shasum -a 256 "$asset" | cut -d' ' -f1)
[ "$actual" = "$expected" ] || { echo "❌ checksum mismatch for $asset"; rm -f "$asset"; exit 1; }
tar xzf "$asset" && rm -f "$asset"

token=$(gh api -X POST "repos/$REPO/actions/runners/registration-token" -q .token)
./config.sh --unattended --url "https://github.com/$REPO" --token "$token" --name "$NAME" \
  --labels "$LABELS" --work _work --replace
./svc.sh install
./svc.sh start
echo "✅ runner $NAME ($LABELS) registered with $REPO and running as a launchd service in $DIR"
echo "   Try it: gh workflow run e2e-selfhosted.yml -R $REPO"
