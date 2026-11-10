#!/usr/bin/env python3
"""Measure sustained throughput and derive a self-hosted GPU cost model.

    # model server (vLLM/Ollama OpenAI-compatible), default
    python3 scripts/measure_throughput.py

    # Baryon AX gateway, end-to-end latency (TTFT + total)
    python3 scripts/measure_throughput.py --target api

    # save JSON evidence for the clawfit registry entry
    python3 scripts/measure_throughput.py --json /tmp/throughput.json

Why this exists
---------------
clawfit's registry requires a deterministic cost point. Self-hosted platforms
have no per-call price, so docs/research/clawfit/registry-entry-proposal.md
proposes a GPU-hour formula:

    cost_per_1k_tokens($) = amortized_cost_per_gpu_hour / (3.6 * tokens_per_second)

`tokens_per_second` must be a *sustained* measurement, not the short-response
latency recorded in docs/b300-vllm.md. This script produces that number.

Notes
-----
* The model is asked for a fixed, long completion so decode throughput dominates.
* The Baryon AX gateway streams content deltas and does not forward `usage`, so
  the API target reports latency only. Use the model target for token counts.
* Results are median of `--runs` runs; cache effects are visible in the spread.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

DEFAULT_MODEL_BASE = os.getenv("MODEL_BASE_URL", "http://127.0.0.1:8020/v1").rstrip("/")
DEFAULT_MODEL_NAME = os.getenv("MODEL_NAME", "baryon-ax-qwen")
DEFAULT_API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000").rstrip("/")

PROMPT = (
    "다음 주제로 400단어 이상의 한국어 설명문을 작성하세요. "
    "문단을 나누고, 각 문단은 완결된 문장으로 작성하세요. 주제: 온프레미스 AI 플랫폼의 감사 로그 설계"
)

# Illustrative amortized rates ($/GPU-hour). Replace with the organization's
# capex/power/cooling figure before publishing a registry entry.
RATE_STEPS = (1.0, 3.0, 6.0)


def _client(timeout: float):
    try:
        import httpx
    except ModuleNotFoundError as error:  # pragma: no cover - environment guard
        raise SystemExit(
            "httpx is required: pip install 'httpx>=0.27' "
            "or run this script inside the API virtualenv."
        ) from error
    return httpx.Client(timeout=timeout)


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = os.getenv("MODEL_API_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def measure_model(base: str, model: str, runs: int, max_tokens: int, timeout: float) -> dict:
    url = f"{base}/chat/completions"
    rows = []
    with _client(timeout) as client:
        for index in range(runs):
            body = {
                "model": model,
                "messages": [{"role": "user", "content": PROMPT}],
                "max_tokens": max_tokens,
                "temperature": 0.0,
                "stream": False,
            }
            started = time.perf_counter()
            response = client.post(url, json=body, headers=_headers())
            elapsed = time.perf_counter() - started
            response.raise_for_status()
            payload = response.json()
            usage = payload.get("usage") or {}
            completion_tokens = usage.get("completion_tokens")
            if not completion_tokens:
                raise SystemExit(
                    "The backend did not return usage.completion_tokens; "
                    "cannot compute sustained throughput."
                )
            rows.append(
                {
                    "run": index + 1,
                    "completion_tokens": completion_tokens,
                    "elapsed_s": round(elapsed, 4),
                    "tokens_per_second": round(completion_tokens / elapsed, 3),
                }
            )
            print(f"  run {index + 1}: {completion_tokens} tokens in {elapsed:.2f}s "
                  f"({completion_tokens / elapsed:.1f} tok/s)")

    throughputs = [row["tokens_per_second"] for row in rows]
    return {
        "target": "model",
        "endpoint": url,
        "model": model,
        "max_tokens": max_tokens,
        "runs": rows,
        "tokens_per_second_median": round(statistics.median(throughputs), 3),
        "tokens_per_second_min": round(min(throughputs), 3),
        "tokens_per_second_max": round(max(throughputs), 3),
    }


def measure_api(base: str, runs: int, timeout: float) -> dict:
    url = f"{base}/v1/chat/completions"
    rows = []
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": os.getenv("MEASURE_USER", "throughput-probe"),
        "X-Organization-Id": os.getenv("MEASURE_ORG", "throughput"),
        "X-Roles": "member",
    }
    payload = {
        "model": os.getenv("MEASURE_MODEL", "demo-local"),
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "use_retrieval": False,
    }
    with _client(timeout) as client:
        for index in range(runs):
            started = time.perf_counter()
            first_token = None
            deltas = 0
            with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = (event.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if delta:
                        deltas += 1
                        if first_token is None:
                            first_token = time.perf_counter() - started
            total = time.perf_counter() - started
            rows.append(
                {
                    "run": index + 1,
                    "ttft_s": round(first_token or 0.0, 4),
                    "total_s": round(total, 4),
                    "content_events": deltas,
                }
            )
            print(f"  run {index + 1}: ttft {rows[-1]['ttft_s']:.2f}s, "
                  f"total {total:.2f}s, {deltas} content events")

    return {
        "target": "api",
        "endpoint": url,
        "runs": rows,
        "ttft_s_median": round(statistics.median([r["ttft_s"] for r in rows]), 4),
        "total_s_median": round(statistics.median([r["total_s"] for r in rows]), 4),
        "note": "gateway streams deltas and does not forward usage; token counts unavailable",
    }


def cost_table(throughput: float | None) -> list[dict]:
    if not throughput:
        return []
    table = []
    for rate in RATE_STEPS:
        table.append(
            {
                "amortized_cost_per_gpu_hour": rate,
                "cost_per_1k_tokens": round(rate / (3.6 * throughput), 6),
            }
        )
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=("model", "api"), default="model")
    parser.add_argument("--base", default=None, help="model base (default MODEL_BASE_URL) or API base (default API_BASE)")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--json", default=None, help="write the result as JSON")
    args = parser.parse_args()

    print(f"target={args.target} runs={args.runs}")
    if args.target == "api":
        result = measure_api(args.base or DEFAULT_API_BASE, args.runs, args.timeout)
    else:
        result = measure_model(args.base or DEFAULT_MODEL_BASE, args.model, args.runs, args.max_tokens, args.timeout)

    throughput = result.get("tokens_per_second_median")
    result["cost_model"] = {
        "formula": "amortized_cost_per_gpu_hour / (3.6 * tokens_per_second_sustained)",
        "tokens_per_second_sustained": throughput,
        "table": cost_table(throughput),
        "rates_are_illustrative": True,
    }

    print("\nsummary")
    for key, value in result.items():
        if key not in {"runs", "cost_model"}:
            print(f"  {key}: {value}")
    if throughput:
        print(f"  sustained: {throughput} tok/s")
        for row in result["cost_model"]["table"]:
            print(f"  ${row['amortized_cost_per_gpu_hour']:.2f}/GPU-h -> "
                  f"${row['cost_per_1k_tokens']:.4f} per 1k tokens")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
