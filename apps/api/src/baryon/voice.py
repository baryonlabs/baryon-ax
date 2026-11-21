"""Voice dictation and voice commands.

The API forwards recorded audio to the on-prem STT sidecar (deploy/stt, sherpa-onnx)
and never stores it. It then decides whether the transcript is a **command** ("새
대화", "보내", "지식 화면 열어") or **dictation** to put into the composer.

A transcript counts as a command only when the whole utterance is a command phrase
(with optional politeness endings such as 줘/주세요). Sentences that merely contain
a command word, such as "새 대화에서 연차 알려줘", stay dictation. Common STT
mishearings of short Korean commands are accepted (e.g. 새/세 대화).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

VIEWS = {
    "chat": ("채팅", "대화", "chat", "conversation"),
    "knowledge": ("지식", "문서", "knowledge", "documents"),
    "tacit": ("암묵지", "tacit"),
    "memory": ("메모리", "기억", "memory"),
    "governance": ("거버넌스", "감사", "governance", "audit"),
    "connectors": ("커넥터", "연결", "connectors"),
    "gaps": ("지식공백", "공백", "gaps"),
    "settings": ("설정", "settings"),
}

# STT often stretches or blurs short endings: 멈춰 → 멈춰어, 가 줘 → 가 죠.
POLITE = r"(?:해|하|해서)?(?:줘|줘요|주세요|주시겠어요|주라|라|요|자|기|죠|조|져)?(?:어|요)?"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("new_chat", re.compile(rf"(?:(?:새|세|새로운|신규)(?:대화|채팅)(?:창)?(?:시작|열어|열기|만들어)?{POLITE}|(?:대화|채팅)새로{POLITE}|newchat|newconversation|startover)")),
    ("send", re.compile(rf"(?:보내|보내기|전송|제출|질문해|send|submit){POLITE}")),
    ("clear", re.compile(rf"(?:(?:입력|내용|다)?(?:지워|지우기|지워버려|삭제)|clear|cleartext){POLITE}")),
    ("stop", re.compile(rf"(?:멈춰|중지|정지|그만|취소|stop|cancel){POLITE}")),
]
_VIEW_VERB = r"(?:화면|탭|페이지|메뉴)?(?:으로|로)?(?:열어|열기|가|가기|이동|보여|띄워|켜)" + POLITE
_OPEN_EN = re.compile(r"(?:open|goto|show)(?P<view>\w+)")


@dataclass(frozen=True)
class Command:
    action: str           # new_chat | send | clear | stop | open_view
    view: str | None = None

    def as_dict(self, phrase: str) -> dict:
        return {"action": self.action, "view": self.view, "phrase": phrase}


def normalize(text: str) -> str:
    return re.sub(r"[\s.,!?·~'\"。？！]+", "", text).lower()


def parse_command(text: str) -> Command | None:
    compact = normalize(text)
    if not compact or len(compact) > 24:  # long utterances are dictation
        return None
    for action, pattern in _PATTERNS:
        if pattern.fullmatch(compact):
            return Command(action)
    for view, words in VIEWS.items():
        for word in sorted(words, key=len, reverse=True):
            if re.fullmatch(re.escape(word.replace(" ", "")) + _VIEW_VERB, compact):
                return Command("open_view", view)
    match = _OPEN_EN.fullmatch(compact)
    if match:
        target = match.group("view")
        for view, words in VIEWS.items():
            if target in words:
                return Command("open_view", view)
    return None


# --- STT sidecar client -------------------------------------------------------------------

class SttUnavailable(Exception):
    """The STT sidecar is not configured or did not answer."""


@dataclass
class SttConfig:
    base_url: str
    api_key: str
    default_model: str
    max_bytes: int

    @classmethod
    def from_env(cls) -> "SttConfig":
        return cls(
            base_url=os.getenv("STT_BASE_URL", "").strip().rstrip("/"),
            api_key=os.getenv("STT_API_KEY", "").strip(),
            default_model=os.getenv("STT_DEFAULT_MODEL", "").strip(),
            max_bytes=int(os.getenv("STT_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024))),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


async def stt_send(config: SttConfig, method: str, path: str, *, timeout: float = 10.0, **kwargs) -> httpx.Response:
    if not config.enabled:
        raise SttUnavailable("speech-to-text is not configured (STT_BASE_URL)")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, f"{config.base_url}{path}", headers=config.headers(), **kwargs)
    except httpx.HTTPError as error:
        raise SttUnavailable(f"speech-to-text service unreachable: {type(error).__name__}") from error
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise SttRejected(response.status_code, str(detail)[:300])
    return response


async def stt_request(config: SttConfig, method: str, path: str, *, timeout: float = 10.0, **kwargs) -> dict:
    return (await stt_send(config, method, path, timeout=timeout, **kwargs)).json()


# --- text to speech ------------------------------------------------------------------------

_CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.S)
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_CITATION = re.compile(r"\[(?:\d+(?:\s*[,-]\s*\d+)*)\]")
_URL = re.compile(r"https?://\S+")
_MD_BLOCK = re.compile(r"^\s*(?:#{1,6}|[-+*>]|\|)\s*", re.M)
_MD_INLINE = re.compile(r"[*`~|]+|(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])")  # keeps snake_case


def speakable(text: str) -> str:
    """Turn a chat answer (Markdown with citations) into text a speech model can read aloud."""
    text = _CODE_BLOCK.sub(" ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _CITATION.sub("", text)
    text = _URL.sub("", text)
    text = _MD_BLOCK.sub("", text)
    text = _MD_INLINE.sub("", text)
    # A line break ends a spoken phrase (headings, list items, table rows).
    text = re.sub(r"[ \t]+\n", "\n", text.strip())
    text = re.sub(r"(?<![.!?。:,\n])\n+\s*", ". ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"([.!?])\.+", r"\1", text)
    return text.strip()


class SttRejected(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail
