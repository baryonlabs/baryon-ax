#!/usr/bin/env bash
# Run this script on the B300 server (or copy it there and run it in tmux).
# Serves Qwen3.5-27B with vLLM on one free GPU, loopback-only.
set -euo pipefail
ROOT=/data/baryon-ax
GPU=${B300_GPU:-7}
PORT=${B300_PORT:-8020}
MODEL=${B300_MODEL:-$ROOT/models/base/Qwen3.5-27B}
NAME=${B300_SERVED_NAME:-baryon-ax-qwen}
MAX_LEN=${B300_MAX_LEN:-32768}
GMU=${B300_GMU:-0.90}

if [ "$(ss -ltnH "sport = :$PORT" | wc -l)" != "0" ]; then
  echo "port $PORT is already in use" >&2
  exit 2
fi
if [ "$(nvidia-smi -i "$GPU" --query-gpu=memory.used --format=csv,noheader,nounits | tr -d ' ')" != "0" ]; then
  echo "GPU $GPU is already in use" >&2
  exit 3
fi

export HF_HOME=$ROOT/cache/hf
export PATH=$ROOT/.venv/bin:/usr/local/cuda/bin:$PATH
export CUDA_HOME=/usr/local/cuda
export TMPDIR=$ROOT/tmp
export CUDA_VISIBLE_DEVICES="$GPU"

echo "serving $MODEL as $NAME on GPU $GPU, port $PORT"
exec vllm serve "$MODEL" \
  --served-model-name "$NAME" \
  --host 127.0.0.1 --port "$PORT" \
  --tensor-parallel-size 1 \
  --max-model-len "$MAX_LEN" \
  --gpu-memory-utilization "$GMU" \
  --max-num-seqs 32 \
  --enable-prefix-caching
