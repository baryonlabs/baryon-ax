#!/usr/bin/env python3
"""Live end-to-end checks against a running Baryon AX stack (standard library only).

    python3 scripts/e2e_live.py --api http://127.0.0.1:8000 [--secretary URL] [--gateway host:port] [--no-network]

Everything runs in a throwaway organization (e2e-<time>) and its documents are deleted at the end, so the
checks can run against a stack people use. Exit code 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
import urllib.error
import urllib.request
import uuid

failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def call(method: str, url: str, headers: dict | None = None, body: object = None, *, form: bytes | None = None,
         content_type: str = "application/json", timeout: float = 180) -> tuple[int, object]:
    data = form if form is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(url, data=data, method=method, headers={**(headers or {}),
                                     **({"Content-Type": content_type} if data is not None else {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local stack
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        return status, raw.decode("utf-8", "replace")


def multipart(filename: str, content: bytes, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
             for key, value in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f"Content-Type: text/markdown\r\n\r\n".encode() + content + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def chat(api: str, headers: dict, text: str) -> dict:
    status, body = call("POST", f"{api}/v1/chat/completions", headers,
                        {"messages": [{"role": "user", "content": text}], "stream": False})
    return body if status == 200 and isinstance(body, dict) else {"error": status, "body": body}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--secretary", default="http://127.0.0.1:18603")
    parser.add_argument("--gateway", default="127.0.0.1:9093")
    parser.add_argument("--no-network", action="store_true", help="skip checks that read public web pages")
    args = parser.parse_args()
    api = args.api.rstrip("/")
    org = f"e2e-{int(time.time())}"
    member = {"X-User-Id": "e2e-member", "X-Organization-Id": org, "X-Roles": "member"}
    admin = {**member, "X-User-Id": "e2e-admin", "X-Roles": "admin"}
    created: list[str] = []

    status, health = call("GET", f"{api}/health")
    check("api health", status == 200 and isinstance(health, dict) and health.get("status") == "ok", health)

    # Documents: upload, answer with a source, small talk without one, an unknown question becomes a gap.
    form, kind = multipart("e2e-출장규정.md", "# 출장 규정\n\n해외 출장 숙박비 상한은 1박 25만원입니다.".encode(),
                           {"allowed_roles": "member,admin", "category": "e2e", "tags": "e2e"})
    status, uploaded = call("POST", f"{api}/v1/documents", admin, form=form, content_type=kind)
    check("upload a document", status == 200 and isinstance(uploaded, dict) and uploaded.get("chunks", 0) >= 1, uploaded)
    if isinstance(uploaded, dict) and uploaded.get("id"):
        created.append(uploaded["id"])
    answer = chat(api, member, "해외 출장 숙박비 상한은 얼마야?")
    titles = [item.get("title") for item in answer.get("citations", [])]
    check("grounded answer cites the document", "e2e-출장규정.md" in titles and answer.get("grounding", {}).get("answered"), answer)
    greeting = chat(api, member, "안녕하세요!")
    check("a greeting has no sources", greeting.get("citations") == [] and greeting.get("knowledge_gap") is None, greeting)
    unknown = chat(api, member, "2031년 달 출장 수당은 얼마야?")
    check("an unknown question is recorded as a gap", unknown.get("knowledge_gap") is not None
          or not unknown.get("grounding", {}).get("answered", True), unknown)

    # MCP server.
    rpc = lambda method, params=None: call("POST", f"{api}/mcp", {**member, "Accept": "application/json, text/event-stream"},
                                           {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    status, init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "1"}})
    check("mcp initialize", status == 200 and isinstance(init, dict) and "result" in init, init)
    status, tools = rpc("tools/list")
    names = {tool["name"] for tool in (tools.get("result", {}).get("tools", []) if isinstance(tools, dict) else [])}
    check("mcp lists its tools", {"search_documents", "ask", "crawl_site"} <= names, names)
    status, found = rpc("tools/call", {"name": "search_documents", "arguments": {"query": "숙박비 상한"}})
    check("mcp search finds the document", status == 200 and "e2e-출장규정" in json.dumps(found, ensure_ascii=False), found)

    # Web pages: internal addresses refused; a public page read, cited and crawled.
    status, refused = call("POST", f"{api}/v1/documents/url", member, {"url": "http://10.0.0.1/"})
    check("internal address refused", status == 422, (status, refused))
    if not args.no_network:
        page = chat(api, member, "https://example.com/ 이 페이지는 무엇을 위한 거야?")
        steps = {step.get("step"): step.get("status") for step in page.get("trace", {}).get("steps", [])}
        check("a linked page is read and cited", steps.get("web_fetch") == "ok"
              and any(str(item.get("title", "")).startswith("웹 · ") for item in page.get("citations", [])), page)
        status, crawl = call("POST", f"{api}/v1/crawls", member, {"url": "https://example.com/", "max_pages": 2})
        state = crawl.get("state") if isinstance(crawl, dict) else None
        for _ in range(60):
            if state in {"done", "failed", "cancelled"}:
                break
            time.sleep(1)
            _, crawl = call("GET", f"{api}/v1/crawls/{crawl['id']}", member)
            state = crawl.get("state")
        check("a crawl finishes", state == "done" and crawl.get("pages_done", 0) >= 1, crawl)
    status, listed = call("GET", f"{api}/v1/documents", admin)
    for document in listed.get("data", []) if isinstance(listed, dict) else []:
        if document["id"] not in created:
            created.append(document["id"])

    # Voice secretary and phone gateway, when they run on this machine.
    try:
        status, secretary = call("GET", f"{args.secretary.rstrip('/')}/health", timeout=5)
        check("voice secretary health", status == 200 and secretary.get("ok") is True, secretary)
        status, _ = call("GET", f"{args.secretary.rstrip('/')}/v1/board", timeout=5)
        check("voice secretary requires its key", status == 401, status)
    except (urllib.error.URLError, OSError):
        print("⏭ voice secretary not running")
    host, _, port = args.gateway.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=3).close()
        check("phone gateway listening", True)
    except OSError:
        print("⏭ phone gateway not running")

    for document_id in created:
        call("DELETE", f"{api}/v1/documents/{document_id}", admin)
    print(f"\n{len(failures)} failed" if failures else "\nall live checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
