#!/usr/bin/env bash
# Forward the B300 loopback vLLM port to the Mac so Docker Desktop can reach it.
set -euo pipefail
HOST=${GPU_HOST:-${B300_HOST:-gpu-host}}
PORT=${GPU_PORT:-${B300_PORT:-8020}}
exec ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L "0.0.0.0:${PORT}:127.0.0.1:${PORT}" "$HOST"
