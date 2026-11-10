#!/usr/bin/env python3
"""End-to-end smoke check for the full Baryon AX stack.

    python3 scripts/smoke_full_stack.py
    API_BASE=http://127.0.0.1:8000 REQUIRE_FULL=1 python3 scripts/smoke_full_stack.py

The script assumes the API is running and, for REQUIRE_FULL=1, that Qdrant,
PostgreSQL, Redis, Langfuse, and Mem0 are enabled.
"""

from __future__ import annotations

import os
import sys
import time

import httpx

BASE = os.getenv("API_BASE", "http://127.0.0.1:8000").rstrip("/")
REQUIRE_FULL = os.getenv("REQUIRE_FULL", "0") == "1"
ORG = os.getenv("SMOKE_ORG", "full-stack-smoke")
USER = os.getenv("SMOKE_USER", f"full-stack-smoke-{int(time.time())}")
HEADERS = {"X-User-Id": USER, "X-Organization-Id": ORG, "X-Roles": "member"}
ADMIN_HEADERS = {**HEADERS, "X-Roles": "admin"}

client = httpx.Client(timeout=180)
failures: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    print(("PASS " if condition else "FAIL ") + name + (f" :: {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def main() -> int:
    health = client.get(f"{BASE}/health").json()
    check("health", health.get("status") == "ok", health)
    if REQUIRE_FULL:
        check("qdrant backend", health.get("vector_backend") == "qdrant", health.get("vector_backend"))
        check("postgresql audit", health.get("audit_backend") == "postgresql", health.get("audit_backend"))
        check("redis cache", health.get("cache_backend") == "redis", health.get("cache_backend"))
        check("langfuse tracing", health.get("tracing_backend") == "langfuse", health.get("tracing_backend"))
        check("mem0 memory", health.get("memory_backend") == "mem0", health.get("memory_backend"))

    token = f"stack-{int(time.time())}"
    upload = client.post(
        f"{BASE}/v1/documents",
        headers=HEADERS,
        files={"file": (f"{token}.md", f"Baryon AX full stack uses Qdrant for vector search and PostgreSQL for audit events. Token {token}.".encode())},
    )
    check("document upload", upload.status_code == 200, upload.status_code)
    document_id = upload.json().get("id", "") if upload.status_code == 200 else ""

    listed = client.get(f"{BASE}/v1/documents", headers=HEADERS)
    check("document list", listed.status_code == 200 and any(item["id"] == document_id for item in listed.json().get("data", [])))

    search = client.get(f"{BASE}/v1/documents/search", params={"q": "Qdrant vector search"}, headers=HEADERS)
    check("retrieval", search.status_code == 200 and any(item["document_id"] == document_id for item in search.json().get("data", [])))

    chat = client.post(
        f"{BASE}/v1/chat/completions",
        headers=HEADERS,
        json={"messages": [{"role": "user", "content": "Which vector database does the full stack use? One sentence."}], "stream": False, "locale": "en"},
    )
    check("chat completion", chat.status_code == 200, chat.status_code)
    if chat.status_code == 200:
        answer = chat.json()["choices"][0]["message"]["content"]
        check("chat answer", bool(answer.strip()), answer[:120])
        check("chat citations", any(item["document_id"] == document_id for item in chat.json().get("citations", [])))

    audit = client.get(f"{BASE}/v1/audit-events?limit=20", headers=ADMIN_HEADERS)
    check("admin audit", audit.status_code == 200 and audit.json().get("data"), audit.status_code)

    if health.get("memory_backend") == "mem0":
        memory_results: list[dict] = []
        for _ in range(2):
            memory = client.post(f"{BASE}/v1/memories", headers=HEADERS,
                                 json={"text": "The user prefers Qdrant for vector search and uses Ollama with qwen2.5:7b."})
            if memory.status_code != 200:
                check("memory add", False, memory.status_code)
                break
            memory_results = memory.json().get("results", [])
            if memory_results:
                break
            time.sleep(1)
        check("memory add", bool(memory_results), memory_results)
        memories = client.get(f"{BASE}/v1/memories/search", params={"q": "preferred vector database"}, headers=HEADERS)
        check("memory search", memories.status_code == 200 and bool(memories.json().get("data")), memories.status_code)
        for item in memory_results:
            client.delete(f"{BASE}/v1/memories/{item['id']}", headers=HEADERS)
    else:
        print("SKIP memory (Mem0 disabled)")

    if document_id:
        deleted = client.delete(f"{BASE}/v1/documents/{document_id}", headers=ADMIN_HEADERS)
        check("document cleanup", deleted.status_code == 200, deleted.status_code)

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
