#!/usr/bin/env bash
# Forward the B300 loopback vLLM port to the Mac so Docker Desktop can reach it.
set -euo pipefail
HOST=${B300_HOST:-b300}
PORT=${B300_PORT:-8020}
exec ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L "0.0.0.0:${PORT}:127.0.0.1:${PORT}" "$HOST"
