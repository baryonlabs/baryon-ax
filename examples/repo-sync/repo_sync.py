"""Keep a git repository's knowledge in baryon-ax: its docs, a code map and the merge history.

    python repo_sync.py --repo ../my-service --ref origin/main --name my-service \
        --api http://127.0.0.1:8000 --state .data/my-service.json

Questions like "what is already built?" or "where is the export API?" need the code's current state, not only
the design documents. Indexing every source file adds noise, so this indexes:

* **docs**: Markdown/text files matching ``--include`` (default: ``**/*.md``, ``**/*.txt``), as they are;
* **code map**: one document per module (top-level folder, or ``--module-depth``) listing its folders and files
  with the classes, functions and HTTP endpoints found in them (Java/Kotlin, TypeScript/JavaScript, Python);
* **history**: the most recent first-parent commits of ``--ref`` (merge requests and their titles).

Files are read from the git ref with ``git show`` (no checkout, so it can run beside your working tree). A state
file remembers what was uploaded: only changed files are uploaded again, and documents for deleted files are
removed. The uploads are owned by the sync's own identity (``--user``) and readable by ``--allowed-roles``.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import httpx

logger = logging.getLogger("repo_sync")

DEFAULT_INCLUDE = ["**/*.md", "**/*.txt"]
DEFAULT_EXCLUDE = ["**/node_modules/**", "**/.git/**", "**/dist/**", "**/build/**", "**/target/**", "**/vendor/**",
                   "**/CHANGELOG*.md", "**/LICENSE*", "**/.scratch/**", "**/third_party/**"]
CODE_EXT = {".java": "java", ".kt": "kotlin", ".ts": "ts", ".tsx": "ts", ".js": "js", ".jsx": "js", ".mjs": "js",
            ".py": "python"}
MAX_DOC_BYTES = 1_500_000
MAX_MAP_CHARS = 120_000   # one code-map document; larger modules are split by sub-folder

# --code: the source files themselves, one document each, cut into blocks headed by path and line range
DEFAULT_CODE_INCLUDE = ["**/*.java", "**/*.kt", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.mjs", "**/*.py",
                        "**/*.go", "**/*.rs", "**/*.cs", "**/*.sql", "**/*.sh"]
DEFAULT_CODE_EXCLUDE = ["**/test/**", "**/tests/**", "**/__tests__/**", "**/e2e/**", "**/*.test.*", "**/*.spec.*",
                        "**/*Test.java", "**/*Tests.java", "**/*IT.java", "**/test_*.py", "**/*_test.py", "**/*_test.go",
                        "**/generated/**", "**/*.min.js", "**/*.d.ts", "**/migrations/**"]
SKIP_DIRS = {"node_modules", "dist", "build", "target", "vendor", "__pycache__", "venv", "out", "coverage"}
CODE_BLOCK_LINES = 80
MAX_CODE_BYTES = 400_000  # a source file larger than this is usually generated
FENCE = {".java": "java", ".kt": "kotlin", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "jsx",
         ".mjs": "javascript", ".py": "python", ".go": "go", ".rs": "rust", ".cs": "csharp", ".sql": "sql", ".sh": "bash"}

SYMBOL_RES = {
    "java": [re.compile(r"^\s*(?:public|protected|private|abstract|final|static|sealed|\s)*\s*(class|interface|enum|record)\s+(\w+)", re.M)],
    "kotlin": [re.compile(r"^\s*(?:data |sealed |abstract |open |private |internal )*(class|interface|object)\s+(\w+)", re.M)],
    "ts": [re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?(function|class|interface|type|const|enum)\s+(\w+)", re.M)],
    "js": [re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?(function|class|const)\s+(\w+)", re.M)],
    "python": [re.compile(r"^(class|def|async def)\s+(\w+)", re.M)],
}
ENDPOINT_RES = [
    # Spring: @GetMapping("/x"), @RequestMapping(value = "/x", method = ...)
    re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?\s*\"([^\"]*)\""),
    # FastAPI / Flask: @app.get("/x"), @router.post("/x")
    re.compile(r"@\w+\.(get|post|put|patch|delete|route)\(\s*[\"']([^\"']+)"),
    # Express / Next-style: router.get('/x', ...)
    re.compile(r"\b(?:app|router)\.(get|post|put|patch|delete)\(\s*[\"'`]([^\"'`]+)"),
]
CLASS_MAPPING_RE = re.compile(r"@RequestMapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?\s*\"([^\"]*)\"[^)]*\)\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:public\s+)?(?:class|interface)", re.S)


# --------------------------------------------------------------------------- git


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def list_files(repo: Path, ref: str, *, regular_only: bool = False) -> dict[str, str]:
    """path → blob sha at ``ref``."""
    out = git(repo, "-c", "core.quotepath=false", "ls-tree", "-r", "-z", ref).decode("utf-8", "replace")
    files: dict[str, str] = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "blob" and (not regular_only or mode in {"100644", "100755"}):
            files[path] = sha
    return files


def read_blob(repo: Path, sha: str) -> bytes:
    if sha.startswith("file:"):  # --worktree: the "sha" is the file's path on disk
        return (repo / sha[5:]).read_bytes()
    return git(repo, "cat-file", "blob", sha)


def knowledge_items(
    repo: Path, ref: str, source_prefix: str, include: list[str], exclude: list[str],
) -> Iterator[dict[str, str]]:
    """Yield candidates from an explicitly selected Git tree without uploading."""
    url = urlsplit(source_prefix)
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.query or url.fragment or any(char.isspace() for char in source_prefix)):
        raise ValueError("A credential-free HTTPS commit URL prefix is required")
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode("ascii").strip()
    for path, sha in list_files(repo, commit, regular_only=True).items():
        if not matches(path, include) or matches(path, exclude):
            continue
        if int(git(repo, "cat-file", "-s", sha)) > MAX_CODE_BYTES:
            logger.warning("Skipping oversized file: %s", path)
            continue
        payload = read_blob(repo, sha)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping non-UTF-8 file: %s", path)
            continue
        if "\0" in text or not text.strip():
            logger.warning("Skipping binary or empty file: %s", path)
            continue
        yield {
            "kind": "repository_file",
            "filename": PurePosixPath(path).name,
            "repository_path": path,
            "source": f"{source_prefix.rstrip('/')}/{commit}/{quote(path)}",
            "content_base64": base64.b64encode(payload).decode("ascii"),
        }


def is_git_repo(repo: Path) -> bool:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-dir"], capture_output=True).returncode == 0


def list_worktree(repo: Path, exclude: list[str]) -> dict[str, str]:
    """path → "file:<path>" for files on disk (a folder that is not a git repo, or uncommitted work).

    In a git checkout, `git ls-files` (tracked + untracked, minus .gitignore) keeps build output out."""
    if is_git_repo(repo):
        out = git(repo, "-c", "core.quotepath=false", "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        paths = [p for p in out.decode("utf-8", "replace").split("\0") if p]
    else:
        paths = []
        for root, dirs, names in os.walk(repo):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
            rel = Path(root).relative_to(repo).as_posix()
            paths += [n if rel == "." else f"{rel}/{n}" for n in names]
    return {p: f"file:{p}" for p in paths if not matches(p, exclude) and (repo / p).is_file()}


SUMMARY_PROMPT = ("다음 소스 파일이 무엇을 하는지 한국어 두 문장 이내로 설명하세요. 화면·기능·업무 용어(예: 로그인, 회원가입, 검증, "
                  "권한, 통계 조회)를 넣고, 코드를 옮겨 적지 말고 설명만 씁니다.\n\n파일: {path}\n\n{text}")


class Summarizer:
    """One or two sentences per source file from an OpenAI-compatible chat endpoint.

    Code is English identifiers; questions are often in another language. A summary in the question's language
    at the top of every block lets a question like "로그인 폼 검증 규칙" find the code that does it."""

    def __init__(self, base_url: str, model: str, api_key: str = "", http: httpx.Client | None = None,
                 prompt: str = SUMMARY_PROMPT, max_chars: int = 6000) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = http or httpx.Client(timeout=120)
        self.prompt = prompt
        self.max_chars = max_chars

    def __call__(self, path: str, text: str) -> str:
        body = {"model": self.model, "temperature": 0.1, "max_tokens": 220,
                "messages": [{"role": "user", "content": self.prompt.format(path=path, text=text[: self.max_chars])}]}
        response = self.http.post(f"{self.base_url}/chat/completions", json=body, headers=self.headers)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"] or ""
        content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
        return " ".join(content.split())[:400]


def code_markdown(repo_name: str, ref: str, path: str, text: str, block_lines: int = CODE_BLOCK_LINES,
                  summary: str = "") -> str:
    """A source file as Markdown: a header (path, size, symbols, endpoints), then blocks of ``block_lines`` lines.

    Each block's heading repeats the path and line range, so a retrieved chunk says where the code is; with a
    ``summary`` every block also starts with it, so each chunk carries what the file does in plain words."""
    suffix = PurePosixPath(path).suffix.lower()
    language = CODE_EXT.get(suffix)
    lines = text.replace("\r\n", "\n").split("\n")
    found = symbols(language, text) if language else []
    routes = endpoints(text) if language else []
    head = [f"# {repo_name} · {path}", "",
            f"> 소스 코드 파일 `{path}` ({ref} 기준, {len(lines)}줄). 자동 생성."]
    if summary:
        head.append(f"> 요약: {summary}")
    if found:
        head.append(f"> 정의: {', '.join(found[:25])}")
    if routes:
        head.append(f"> 엔드포인트: {', '.join(f'`{r}`' for r in routes[:20])}")
    blocks = []
    for start in range(0, len(lines), block_lines):
        chunk = lines[start:start + block_lines]
        end = start + len(chunk)
        about = f"> {summary}\n\n" if summary else ""
        blocks.append(f"## {path} L{start + 1}-{end}\n\n{about}```{FENCE.get(suffix, '')}\n" + "\n".join(chunk).rstrip() + "\n```\n")
    return "\n".join(head) + "\n\n" + "\n".join(blocks)


def matches(path: str, patterns: list[str]) -> bool:
    candidates = (path, "/" + path)
    return any(fnmatch.fnmatch(c, p) or fnmatch.fnmatch(c, p.removeprefix("**/")) for p in patterns for c in candidates)


# --------------------------------------------------------------------------- code map


def symbols(language: str, text: str) -> list[str]:
    found: list[str] = []
    for pattern in SYMBOL_RES.get(language, []):
        for kind, name in pattern.findall(text):
            if not name.startswith("_"):
                found.append(name if kind in ("class", "interface", "enum", "record", "object", "type") else f"{name}()")
    return list(dict.fromkeys(found))[:40]


def endpoints(text: str) -> list[str]:
    prefix = ""
    class_mapping = CLASS_MAPPING_RE.search(text)
    if class_mapping:
        prefix = class_mapping.group(1).rstrip("/")
    found: list[str] = []
    for pattern in ENDPOINT_RES:
        for method, path in pattern.findall(text):
            verb = method.upper().replace("REQUEST", "ANY").replace("ROUTE", "ANY")
            full = path if path.startswith(prefix) else prefix + ("" if path.startswith("/") or not path else "/") + path
            if class_mapping and method == "Request" and path == class_mapping.group(1):
                continue  # the class-level mapping itself
            found.append(f"{verb} {full or prefix or '/'}")
    return list(dict.fromkeys(found))[:40]


def module_of(path: str, depth: int) -> str:
    parts = PurePosixPath(path).parts
    return "/".join(parts[:depth]) if len(parts) > depth else "(root)"


@dataclass
class ModuleMap:
    name: str
    files: dict[str, dict] = field(default_factory=dict)  # path → {"symbols": [...], "endpoints": [...], "lines": n}

    def render(self, repo_name: str, ref: str) -> list[tuple[str, str]]:
        """[(key, markdown)]; split by sub-folder when the module is large."""
        by_dir: dict[str, list[str]] = defaultdict(list)
        for path in sorted(self.files):
            by_dir[str(PurePosixPath(path).parent)].append(path)
        endpoint_lines = [f"- `{e}` — {PurePosixPath(p).name}" for p, info in sorted(self.files.items()) for e in info["endpoints"]]
        header = [f"# 코드 지도: {repo_name} / {self.name}", "",
                  f"> 저장소 `{repo_name}` 의 `{ref}` 기준 자동 생성. 파일 {len(self.files)}개, 폴더 {len(by_dir)}개.", ""]
        if endpoint_lines:
            header += ["## HTTP 엔드포인트", "", *endpoint_lines[:300], ""]
        sections: list[str] = []
        for folder, paths in by_dir.items():
            lines = [f"## {folder}", ""]
            for path in paths:
                info = self.files[path]
                detail = ", ".join(info["symbols"][:15]) or "-"
                # Endpoints also sit next to their file: a retrieved chunk of this folder then answers "which API?"
                routes = f" — 엔드포인트: {', '.join(f'`{e}`' for e in info['endpoints'][:12])}" if info["endpoints"] else ""
                lines.append(f"- `{PurePosixPath(path).name}` ({info['lines']}줄): {detail}{routes}")
            sections.append("\n".join(lines) + "\n")
        docs: list[tuple[str, str]] = []
        current = "\n".join(header)
        part = 1
        for section in sections:
            if len(current) + len(section) > MAX_MAP_CHARS and len(current) > len("\n".join(header)):
                docs.append((f"{self.name}#{part}", current))
                part += 1
                current = "\n".join(header[:3]) + f"\n(계속 {part})\n\n"
            current += section
        docs.append((f"{self.name}#{part}" if part > 1 else self.name, current))
        return docs


def build_code_map(repo: Path, files: dict[str, str], depth: int, exclude: list[str],
                   reader=read_blob) -> dict[str, ModuleMap]:
    modules: dict[str, ModuleMap] = {}
    for path, sha in files.items():
        language = CODE_EXT.get(PurePosixPath(path).suffix.lower())
        if not language or matches(path, exclude):
            continue
        text = reader(repo, sha).decode("utf-8", "replace")
        module = modules.setdefault(module_of(path, depth), ModuleMap(module_of(path, depth)))
        module.files[path] = {"symbols": symbols(language, text), "endpoints": endpoints(text), "lines": text.count("\n") + 1}
    return modules


def build_history(repo: Path, ref: str, limit: int) -> str:
    """Recent first-parent history. GitLab merge commits carry the MR title in the body."""
    raw = git(repo, "log", ref, "--first-parent", f"-n{limit}", "--date=short", "--format=%h%x1f%ad%x1f%s%x1f%b%x1e")
    lines = []
    for record in raw.decode("utf-8", "replace").split("\x1e"):
        if not record.strip():
            continue
        short, date, subject, body = (record.strip("\n").split("\x1f") + ["", "", "", ""])[:4]
        title = subject
        mr = re.search(r"merge request \S*?!(\d+)", body)
        if subject.startswith("Merge branch"):
            body_lines = [line.strip() for line in body.splitlines() if line.strip() and not line.startswith("See merge request")]
            title = body_lines[0] if body_lines else subject
        lines.append(f"- {date} `{short}`{f' !{mr.group(1)}' if mr else ''} {title}")
    return "\n".join([f"# 최근 변경 이력 ({ref})", "", "> 첫 번째 부모 기준 최근 커밋·머지 리퀘스트. 자동 생성.", "", *lines, ""])


# --------------------------------------------------------------------------- baryon-ax


class Uploader:
    def __init__(self, api: str, organization: str, user: str, roles: str, allowed_roles: str, token: str = "",
                 category: str = "code", http: httpx.Client | None = None) -> None:
        self.http = http or httpx.Client(base_url=api.rstrip("/"), timeout=600)
        self.headers = {"Authorization": f"Bearer {token}"} if token else {
            "X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}
        self.allowed_roles = allowed_roles
        self.category = category

    def upload(self, title: str, payload: bytes, tags: list[str]) -> str:
        response = self.http.post("/v1/documents", headers=self.headers, files={"file": (title, payload)},
                                  data={"allowed_roles": self.allowed_roles, "category": self.category, "tags": ",".join(tags)})
        if response.status_code >= 400:
            raise RuntimeError(f"{response.status_code} {response.text[:200]}")
        return response.json()["id"]

    def delete(self, document_id: str) -> None:
        response = self.http.delete(f"/v1/documents/{document_id}", headers=self.headers)
        if response.status_code not in (200, 204, 404):
            raise RuntimeError(f"delete {document_id}: {response.status_code} {response.text[:200]}")


def title_for(repo_name: str, path: str, suffix: str = "") -> str:
    """Upload filename (= document title). Slashes would be read as folders, so they become " › "."""
    name = f"{repo_name} · {path.replace('/', ' › ')}"
    return name if not suffix or name.endswith(suffix) else name + suffix


def sync(args: argparse.Namespace, uploader: Uploader | None = None, summarizer=None) -> dict:
    repo = Path(args.repo).resolve()
    name = args.name or repo.name
    worktree = args.worktree or not is_git_repo(repo)
    if args.fetch and not worktree:
        subprocess.run(["git", "-C", str(repo), "fetch", "--quiet"], check=False)
    files = list_worktree(repo, args.exclude) if worktree else list_files(repo, args.ref)
    ref_label = "작업 폴더" if worktree else args.ref
    if worktree:
        args.history = 0 if not is_git_repo(repo) else args.history
    state_path = Path(args.state)
    state: dict[str, dict] = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    uploader = uploader or Uploader(args.api, args.organization, args.user, args.roles, args.allowed_roles,
                                    os.getenv("BARYON_API_TOKEN", ""), args.category)
    wanted: dict[str, tuple[str, bytes | None, str, str]] = {}  # key → (version, payload | None, title, kind)

    if not args.no_docs:
        for path, sha in files.items():
            if matches(path, args.include) and not matches(path, args.exclude):
                version = sha if not worktree else hashlib.sha256(read_blob(repo, sha)).hexdigest()
                wanted[f"doc:{path}"] = (version, None, title_for(name, path), "doc")
    if args.summarize and summarizer is None:
        summarizer = Summarizer(args.summary_url, args.summary_model, os.getenv("SUMMARY_API_KEY", ""))
    # A summarized document's version ends in "+s": turning summaries on re-uploads once, and a file whose
    # summary failed (version without "+s") is tried again next run.
    want_summary = "+s" if summarizer else ""
    if args.code:
        for path, sha in files.items():
            if not matches(path, args.code_include) or matches(path, args.exclude) or matches(path, args.code_exclude):
                continue
            version = sha if not worktree else hashlib.sha256(read_blob(repo, sha)).hexdigest()
            wanted[f"code:{path}"] = (version + want_summary, None, title_for(name, path, ".md"), "code")
    if not args.no_code_map:
        for module in build_code_map(repo, files, args.module_depth, args.exclude).values():
            for key, markdown in module.render(name, ref_label):
                body = markdown.encode("utf-8")
                wanted[f"map:{key}"] = (hashlib.sha256(body).hexdigest(), body,
                                        title_for(name, f"코드지도 {key.replace('#', ' ')}", ".md"), "code-map")
    if args.history:
        body = build_history(repo, args.ref, args.history).encode("utf-8")
        wanted["history"] = (hashlib.sha256(body).hexdigest(), body, title_for(name, "최근 변경 이력", ".md"), "history")

    report = {"uploaded": 0, "unchanged": 0, "deleted": 0, "failed": [], "summarized": 0}
    todo = sorted((k, v) for k, v in wanted.items() if not (state.get(k) and state[k].get("version") == v[0]))
    report["unchanged"] = len(wanted) - len(todo)

    def source_of(key: str) -> tuple[str, bytes]:
        path = key.split(":", 1)[1]
        return path, read_blob(repo, files[path])

    def summarize(key: str) -> str:
        path, raw = source_of(key)
        if len(raw) > MAX_CODE_BYTES or b"\0" in raw[:4096]:
            return ""
        base = wanted[key][0].removesuffix("+s")
        cached = state.get(key, {})
        if cached.get("summary") and cached.get("source") == base:  # same file, summary already written
            return cached["summary"]
        try:
            return summarizer(path, raw.decode("utf-8", "replace"))
        except Exception as error:
            logger.warning("summary failed %s: %s", key, error)
            return ""

    pool = ThreadPoolExecutor(max_workers=max(1, args.summary_workers)) if summarizer else None
    summaries = {k: pool.submit(summarize, k) for k, (_, _, _, kind) in todo if kind == "code"} if pool else {}
    try:
        for key, (version, payload, title, kind) in todo:
            known = state.get(key)
            summary = ""
            if payload is None:
                path, payload = source_of(key)
                if kind == "code":
                    if len(payload) > MAX_CODE_BYTES or b"\0" in payload[:4096]:
                        continue
                    summary = summaries[key].result() if key in summaries else ""
                    if want_summary and not summary:
                        version = version.removesuffix("+s")  # retried next run
                    report["summarized"] += bool(summary)
                    payload = code_markdown(name, ref_label, path, payload.decode("utf-8", "replace"),
                                            summary=summary).encode("utf-8")
            uploaded = _upload_one(uploader, state, report, key, version, payload, title, kind, name, ref_label, known)
            if uploaded and kind == "code":
                state[key].update({"source": version.removesuffix("+s"), "summary": summary})
            if uploaded:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")  # resumable
    finally:
        if pool:
            pool.shutdown(wait=False, cancel_futures=True)

    for key in [k for k in state if k not in wanted]:
        uploader.delete(state.pop(key)["id"])
        report["deleted"] += 1
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def _upload_one(uploader, state, report, key, version, payload, title, kind, name, ref_label, known) -> bool:
    """Upload one document and replace the old one. On failure the old document stays (retried next run)."""
    if len(payload) > MAX_DOC_BYTES or not payload.strip():
        return False
    try:
        document_id = uploader.upload(title, payload, [f"repo:{name}", f"kind:{kind}", f"ref:{ref_label}"])
    except Exception as error:  # parse failure, API down
        report["failed"].append(f"{key}: {error}")
        logger.warning("upload failed %s: %s", key, error)
        return False
    if known:
        uploader.delete(known["id"])
    state[key] = {"version": version, "id": document_id, "title": title}
    report["uploaded"] += 1
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True, help="path to a git checkout (any branch; files come from --ref)")
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--knowledge-manifest-prefix", help="emit knowledge items as JSON lines using this HTTPS URL prefix")
    parser.add_argument("--knowledge-include", nargs="+", help="approved path patterns for a knowledge manifest")
    parser.add_argument("--name", default="", help="name shown in titles and tags (default: folder name)")
    parser.add_argument("--fetch", action="store_true", help="git fetch before reading --ref")
    parser.add_argument("--include", nargs="*", default=DEFAULT_INCLUDE)
    parser.add_argument("--exclude", nargs="*", default=DEFAULT_EXCLUDE)
    parser.add_argument("--module-depth", type=int, default=1, help="folder depth that makes one code-map module")
    parser.add_argument("--history", type=int, default=300, help="commits in the history document (0 = none)")
    parser.add_argument("--no-docs", action="store_true")
    parser.add_argument("--no-code-map", action="store_true")
    parser.add_argument("--code", action="store_true",
                        help="also index the source files themselves (one document each, blocks headed by path and lines)")
    parser.add_argument("--code-include", nargs="*", default=DEFAULT_CODE_INCLUDE)
    parser.add_argument("--code-exclude", nargs="*", default=DEFAULT_CODE_EXCLUDE, help="tests and generated code by default")
    parser.add_argument("--summarize", action="store_true",
                        help="with --code: a one-or-two sentence summary per file at the top of every block (LLM)")
    parser.add_argument("--summary-url", default=os.getenv("SUMMARY_BASE_URL", os.getenv("MODEL_BASE_URL", "")),
                        help="OpenAI-compatible base URL (…/v1); key from SUMMARY_API_KEY")
    parser.add_argument("--summary-model", default=os.getenv("SUMMARY_MODEL", os.getenv("MODEL_NAME", "")))
    parser.add_argument("--summary-workers", type=int, default=4)
    parser.add_argument("--worktree", action="store_true",
                        help="read files on disk instead of --ref (automatic for a folder that is not a git repo)")
    parser.add_argument("--api", default=os.getenv("BARYON_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--organization", default=os.getenv("BARYON_ORGANIZATION_ID", "local-org"))
    parser.add_argument("--user", default="repo-sync", help="owner of the uploaded documents (dev auth)")
    parser.add_argument("--roles", default="admin", help="roles of the sync identity (dev auth)")
    parser.add_argument("--allowed-roles", default="member,admin", help="who may read the documents")
    parser.add_argument("--category", default="code")
    parser.add_argument("--state", default=".data/repo-sync.json")
    args = parser.parse_args(argv)
    if args.knowledge_manifest_prefix and (not args.knowledge_include or args.worktree or args.fetch):
        parser.error("a knowledge manifest requires --knowledge-include and reads a local commit")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    if args.knowledge_manifest_prefix:
        for item in knowledge_items(Path(args.repo), args.ref, args.knowledge_manifest_prefix,
                                    args.knowledge_include, args.exclude + args.code_exclude):
            print(json.dumps(item))
        return 0
    report = sync(args)
    print(json.dumps({**report, "failed": report["failed"][:20], "failed_count": len(report["failed"])}, ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
