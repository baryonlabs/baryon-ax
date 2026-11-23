"""Add what was shared in Slack to baryon-ax as documents ("문서로 추가").

``@bot 문서로 추가해줘`` (or "add this to the docs") in a thread registers:

* the files attached anywhere in the thread (or to the message itself), with
  ``.zip`` archives expanded into their members, or
* when nothing is attached, the thread's text above the request (or, for a
  top-level message, the message just above it) as one Markdown document.

Files are downloaded with the bot token (``files:read``) and uploaded to
``POST /v1/documents`` as the Slack user who asked, so the document owner and the
audit log are that person. When the request also asks for analysis ("분석",
"요약", "검토", "analyze", …) the bot answers from the text of the documents it
just added instead of from search results.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Callable

import httpx

logger = logging.getLogger("baryon.slack.intake")

INTAKE_RE = re.compile(
    r"(문서|자료|파일|지식)\s*(?:으로|로|에|를|을)?\s*(?:추가|등록|올리|올려|저장|넣)"
    r"|\b(?:add|save|ingest|index|upload)\b.{0,30}\b(?:docs?|documents?|knowledge(?:\s*base)?)\b",
    re.IGNORECASE,
)
ANALYZE_RE = re.compile(r"분석|요약|검토|정리|파악|analy[sz]|summar|review", re.IGNORECASE)
# Asking to read or look at what was shared. With files in the thread this reads them (registers, then answers).
READ_RE = re.compile(r"읽어|읽고|읽으|열어|살펴|봐라|봐줘|봐 줘|보아라|확인해|확인하라|이해해|분석|요약|검토|정리|파악"
                     r"|\b(?:read|open|look at|check|analy[sz]e|summari[sz]e|review)\b", re.IGNORECASE)
INTAKE_PENDING = ":inbox_tray: 문서로 등록하고 있습니다…"
ANALYSIS_PENDING = ":hourglass_flowing_sand: 등록한 문서를 읽고 분석하고 있습니다…"
SKIP_MEMBERS = ("__MACOSX/", ".DS_Store", "Thumbs.db", "desktop.ini")
ARCHIVES = (".zip",)
SECTION_LIMIT = 2900


class IntakeError(Exception):
    """A problem the person who asked should read (missing scope, nothing to add)."""


@dataclass
class IntakeConfig:
    enabled: bool = True
    max_file_bytes: int = 20_000_000   # per file, after unzipping; match the API's MAX_DOCUMENT_BYTES
    max_files: int = 30                # per request, archive members included
    allowed_roles: str = "member,admin"
    category: str = "slack"
    analysis_chars: int = 24_000       # document text sent to the model for "분석" requests

    @classmethod
    def from_env(cls) -> "IntakeConfig":
        return cls(
            enabled=os.getenv("SLACK_DOC_INTAKE", "true").strip().lower() in {"1", "true", "yes", "on"},
            max_file_bytes=int(os.getenv("SLACK_INTAKE_MAX_FILE_BYTES", cls.max_file_bytes)),
            max_files=int(os.getenv("SLACK_INTAKE_MAX_FILES", cls.max_files)),
            allowed_roles=os.getenv("SLACK_INTAKE_ALLOWED_ROLES", cls.allowed_roles),
            category=os.getenv("SLACK_INTAKE_CATEGORY", cls.category),
            analysis_chars=int(os.getenv("SLACK_INTAKE_ANALYSIS_CHARS", cls.analysis_chars)),
        )


@dataclass
class Added:
    name: str
    document_id: str
    title: str
    chunks: int = 0
    parser: str = ""
    reused: bool = False  # registered earlier from the same Slack file, not uploaded again


@dataclass
class Skipped:
    name: str
    reason: str


@dataclass
class IntakeResult:
    added: list[Added] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    source: str = "files"  # "files" or "text"
    reused: int = 0        # of `added`, documents registered earlier from the same Slack files


def wants_intake(text: str) -> bool:
    return bool(INTAKE_RE.search(text or ""))


def wants_analysis(text: str) -> bool:
    return bool(ANALYZE_RE.search(INTAKE_RE.sub(" ", text or "")))


# The question is about code or a folder/repository (indexed by repo-sync), not about what was attached here.
ELSEWHERE_RE = re.compile(r"코드|소스|폴더|디렉터리|디렉토리|저장소|레포|리포지|깃랩|깃허브|커밋|머지|브랜치|~/|[A-Za-z]:\\|/dev/"
                          r"|\b(?:code|source|repo|repository|codebase|folder|directory|commit|branch)\b", re.IGNORECASE)
ATTACHED_RE = re.compile(r"첨부|올린|올려준|공유한|공유해 준|위의|위에|이 파일|이파일|엑셀|pdf|docx|hwp|xlsx|zip|\b(?:attached|this file)\b",
                         re.IGNORECASE)


def wants_file_reading(text: str) -> bool:
    """Read the thread's files: asked to read/analyze, and not about code or a folder elsewhere
    ("~/dev/app 폴더의 코드를 보고 분석해" goes to search, which has the synced code)."""
    text = text or ""
    if not READ_RE.search(text):
        return False
    return not (ELSEWHERE_RE.search(text) and not ATTACHED_RE.search(text))


class IntakeLedger:
    """Slack file id → the document it became, so asking again about the same thread does not upload twice."""

    def __init__(self, path: str | None) -> None:
        self.path = path
        self.items: dict[str, dict] = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    self.items = json.load(handle)
            except (OSError, ValueError):
                logger.warning("could not read %s; starting empty", path)

    @classmethod
    def from_env(cls) -> "IntakeLedger":
        path = os.getenv("SLACK_INTAKE_STATE_PATH", "").strip()
        if not path:
            proactive = os.getenv("PROACTIVE_STATE_PATH", "").strip()
            path = os.path.join(os.path.dirname(proactive) if proactive else ".data", "intake_state.json")
        return cls(path)

    def get(self, file_id: str) -> list[Added]:
        return [Added(**{**item, "reused": True}) for item in self.items.get(file_id, [])]

    def put(self, file_id: str, added: list[Added]) -> None:
        if not file_id or not added:
            return
        self.items[file_id] = [{k: v for k, v in vars(item).items() if k != "reused"} for item in added]
        if self.path:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as handle:
                    json.dump(self.items, handle, ensure_ascii=False, indent=1)
            except OSError:
                logger.warning("could not write %s", self.path, exc_info=True)


# --------------------------------------------------------------------------- collecting


def _from_bot(message: dict, bot_user_id: str) -> bool:
    return message.get("user") == bot_user_id or bool(message.get("bot_id"))


def thread_files(messages: list[dict], bot_user_id: str) -> list[dict]:
    """Files people attached in these messages, oldest first, each once."""
    seen: set[str] = set()
    files: list[dict] = []
    for message in messages:
        if _from_bot(message, bot_user_id):
            continue
        for item in message.get("files") or []:
            key = item.get("id") or item.get("url_private") or item.get("name")
            if not key or key in seen or item.get("mode") in {"tombstone", "hidden_by_limit", "external"}:
                continue
            seen.add(key)
            files.append(item)
    return files


def thread_text(messages: list[dict], bot_user_id: str, before_ts: str, strip: Callable[[str], str]) -> str:
    """People's messages before the request, without other "문서로 추가" requests."""
    parts: list[str] = []
    for message in messages:
        if float(message.get("ts", 0)) >= float(before_ts) or _from_bot(message, bot_user_id):
            continue
        text = strip(message.get("text") or "")
        if text and not (wants_intake(text) and len(text) < 80):
            parts.append(text)
    return "\n\n".join(parts).strip()


def member_name(info: zipfile.ZipInfo) -> str:
    """Archives made on Korean Windows store names in CP949 without the UTF-8 flag."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def expand(name: str, payload: bytes, max_bytes: int) -> tuple[list[tuple[str, bytes]], list[Skipped]]:
    """A .zip becomes its members (folders kept in the name); anything else stays as is."""
    if not name.lower().endswith(ARCHIVES):
        return [(name, payload)], []
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        return [], [Skipped(name, "압축 파일을 열 수 없습니다")]
    members: list[tuple[str, bytes]] = []
    skipped: list[Skipped] = []
    with archive:
        for info in archive.infolist():
            inner = member_name(info)
            base = PurePosixPath(inner).name
            if info.is_dir() or not base or any(marker in inner for marker in SKIP_MEMBERS) or base.startswith("~$"):
                continue
            label = f"{name}/{inner}"
            if inner.lower().endswith(ARCHIVES):
                skipped.append(Skipped(label, "압축 안의 압축 파일은 건너뜁니다"))
            elif info.file_size > max_bytes:
                skipped.append(Skipped(label, f"{info.file_size // 1_000_000}MB로 크기 제한을 넘습니다"))
            else:
                members.append((inner, archive.read(info)))
    return members, skipped


# --------------------------------------------------------------------------- intake


class DocumentIntake:
    """Collects files or text from a Slack thread and uploads them to baryon-ax."""

    def __init__(self, config: IntakeConfig, baryon, slack_token: str, http: httpx.Client | None = None,
                 ledger: IntakeLedger | None = None) -> None:
        self.config = config
        self.baryon = baryon
        self.slack_token = slack_token
        self.http = http or httpx.Client(timeout=120, follow_redirects=True)
        self.ledger = ledger or IntakeLedger(None)

    def download(self, item: dict) -> bytes:
        url = item.get("url_private_download") or item.get("url_private")
        if not url:
            raise IntakeError("파일 주소가 없습니다")
        response = self.http.get(url, headers={"Authorization": f"Bearer {self.slack_token}"})
        response.raise_for_status()
        # Without files:read Slack answers with its HTML sign-in page instead of the file.
        if "text/html" in response.headers.get("content-type", "") and not str(item.get("name", "")).lower().endswith((".html", ".htm")):
            raise IntakeError("봇에 files:read 권한이 없어 파일을 받을 수 없습니다")
        return response.content

    def upload(self, name: str, payload: bytes, slack_user: str, tags: list[str]) -> Added | Skipped:
        response = self.baryon.as_user(
            "POST", "/v1/documents", slack_user,
            files={"file": (PurePosixPath(name).name, payload)},
            data={"allowed_roles": self.config.allowed_roles, "category": self.config.category, "tags": ",".join(tags)},
        )
        if response.status_code >= 400:
            try:
                detail = str(response.json().get("detail", ""))
            except ValueError:
                detail = response.text
            return Skipped(name, _reason(response.status_code, detail))
        data = response.json()
        return Added(name, data["id"], data.get("title") or name, int(data.get("chunks") or 0), data.get("parser", ""))

    def collect(self, messages: list[dict], request: dict, bot_user_id: str, strip: Callable[[str], str],
                slack_user: str) -> IntakeResult:
        tags = ["slack", f"slack-channel:{request.get('channel', '')}"]
        files = thread_files(messages + [request], bot_user_id)
        result = IntakeResult()
        if files:
            for item in files:
                name = item.get("name") or item.get("title") or item.get("id", "file")
                earlier = self.ledger.get(item.get("id", ""))
                if earlier:  # registered from this Slack file before: read that document again, do not upload twice
                    known = {added.document_id for added in result.added}
                    fresh = [added for added in earlier if added.document_id not in known]
                    result.added.extend(fresh)
                    result.reused += len(fresh)
                    continue
                if len(result.added) + len(result.skipped) >= self.config.max_files:
                    result.skipped.append(Skipped(name, f"한 번에 {self.config.max_files}개까지만 등록합니다"))
                    continue
                size = int(item.get("size") or 0)
                archive = name.lower().endswith(ARCHIVES)
                if size > self.config.max_file_bytes and not archive:
                    result.skipped.append(Skipped(name, f"{size // 1_000_000}MB로 크기 제한을 넘습니다"))
                    continue
                try:
                    payload = self.download(item)
                except httpx.HTTPError as error:
                    result.skipped.append(Skipped(name, f"내려받지 못했습니다 ({type(error).__name__})"))
                    continue
                members, skipped = expand(name, payload, self.config.max_file_bytes)
                result.skipped.extend(skipped)
                from_this_file: list[Added] = []
                for member, data in members:
                    label = member if member == name else f"{name}/{member}"
                    if len(result.added) + len(result.skipped) >= self.config.max_files:
                        result.skipped.append(Skipped(label, f"한 번에 {self.config.max_files}개까지만 등록합니다"))
                        continue
                    outcome = self.upload(member, data, slack_user, tags + [f"slack-file:{item.get('id', '')}"])
                    outcome.name = label
                    if isinstance(outcome, Added):
                        from_this_file.append(outcome)
                    (result.added if isinstance(outcome, Added) else result.skipped).append(outcome)
                self.ledger.put(item.get("id", ""), from_this_file)
            return result

        text = thread_text(messages, bot_user_id, request["ts"], strip)
        if not text:
            raise IntakeError("등록할 파일이나 글을 찾지 못했습니다. 파일을 첨부한 스레드에서 다시 요청하거나, 글을 함께 보내 주세요.")
        when = datetime.fromtimestamp(float(request["ts"]), tz=timezone.utc).strftime("%Y-%m-%d")
        first_line = text.splitlines()[0][:60].strip(" #*>")
        name = f"slack-{when}-{_slug(first_line) or request['ts']}.md"
        body = f"# {first_line or 'Slack 대화'}\n\n> 출처: Slack 대화 ({when})\n\n{text}\n"
        outcome = self.upload(name, body.encode("utf-8"), slack_user, tags)
        result.source = "text"
        (result.added if isinstance(outcome, Added) else result.skipped).append(outcome)
        return result

    def document_text(self, document_id: str, slack_user: str) -> str:
        response = self.baryon.as_user("GET", f"/v1/documents/{document_id}", slack_user)
        response.raise_for_status()
        return str(response.json().get("content") or "")

    def analysis_prompt(self, request: str, added: list[Added], slack_user: str) -> tuple[str, bool]:
        """The request plus the new documents' text, split evenly within the budget."""
        budget = max(2000, self.config.analysis_chars // max(1, len(added)))
        sections: list[str] = []
        truncated = False
        for item in added:
            try:
                text = self.document_text(item.document_id, slack_user)
            except httpx.HTTPError:
                logger.warning("could not read document %s", item.document_id, exc_info=True)
                continue
            if len(text) > budget:
                text, truncated = text[:budget] + "\n…(이하 생략)", True
            sections.append(f"### 문서: {item.title}\n{text}")
        prompt = (f"{request}\n\n아래는 방금 등록한 문서입니다. 이 문서들의 내용만 근거로 답하고, "
                  f"문서에 없는 내용은 없다고 말해 주세요. 어떤 문서에서 나온 내용인지 문서 이름을 밝혀 주세요.\n\n"
                  + "\n\n".join(sections))
        return prompt, truncated


def _slug(text: str) -> str:
    return re.sub(r"[^\w가-힣]+", "-", text).strip("-")[:40]


def _reason(status: int, detail: str) -> str:
    if "no parser" in detail:
        return "지원하지 않는 형식입니다"
    if status == 413:
        return "크기 제한을 넘습니다"
    if status == 403:
        return "문서를 등록할 권한이 없습니다"
    return f"등록하지 못했습니다 (HTTP {status}{': ' + detail[:80] if detail else ''})"


def analysis_blocks(answer_mrkdwn: str, added: list[Added], truncated: bool, trace_id: str) -> list[dict]:
    """The analysis answer, with the documents it was read from (not search results) as its basis."""
    def clip(text: str) -> str:
        return text if len(text) <= SECTION_LIMIT else text[: SECTION_LIMIT - 1] + "…"

    basis = "\n".join(f"{index}. {item.title}" for index, item in enumerate(added[:10], 1))
    if len(added) > 10:
        basis += f"\n… 외 {len(added) - 10}건"
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": clip(answer_mrkdwn)}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": clip("*:bookmark_tabs: 근거 (방금 등록한 문서 원문)*\n" + basis)}},
    ]
    notes = []
    if truncated:
        notes.append(":scissors: 문서가 길어 앞부분만 읽었습니다. 세부 내용은 질문으로 다시 물어봐 주세요.")
    if trace_id:
        notes.append(f"baryon-ax · trace `{trace_id}`")
    if notes:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(notes)}]})
    return blocks


def result_text(result: IntakeResult) -> str:
    """Slack mrkdwn summary of what was added and what was not."""
    lines: list[str] = []
    if result.added:
        what = "스레드 글" if result.source == "text" else "파일"
        new = len(result.added) - result.reused
        if new:
            again = f" (앞서 등록한 {result.reused}건은 다시 올리지 않음)" if result.reused else ""
            lines.append(f":white_check_mark: *{what}을 문서 {new}건으로 등록했습니다.*{again} 이제 질문하면 이 문서도 근거로 찾습니다.")
        else:
            lines.append(f":white_check_mark: *이 스레드의 파일은 이미 문서 {result.reused}건으로 등록되어 있어 그 내용을 읽었습니다.*")
        for item in result.added:
            detail = ", ".join(part for part in (f"{item.chunks}개 조각" if item.chunks else "", item.parser) if part)
            lines.append(f"• {item.title}" + (f" ({detail})" if detail else ""))
    else:
        lines.append(":warning: *등록한 문서가 없습니다.*")
    if result.skipped:
        lines.append(f"\n:no_entry_sign: 건너뛴 파일 {len(result.skipped)}건")
        for item in result.skipped[:15]:
            lines.append(f"• {item.name} — {item.reason}")
        if len(result.skipped) > 15:
            lines.append(f"• … 외 {len(result.skipped) - 15}건")
    text = "\n".join(lines)
    return text if len(text) <= SECTION_LIMIT else text[: SECTION_LIMIT - 1] + "…"
