"""Questions about the product itself ("설명서 알려줘", "사용법 자세히", "슬랙에서 파일은 어떻게 올려?").

Ordinary questions are answered from the few chunks search returns, which made "설명서 알려줘" a five-line
summary ending in "더 알고 싶은 부분이 있으신가요?". For these questions the bot reads the whole usage guide
(docs/getting-started-ko.md, registered as a document tagged ``guide``) and explains from all of it: every
section for "설명서 알려줘", the matching part in depth for "슬랙에서 파일은 어떻게 올려?".

Environment: SLACK_GUIDE_DOCUMENT_ID pins the document; otherwise the newest document tagged SLACK_GUIDE_TAG
(default ``guide``) that the asking person can read. SLACK_GUIDE=false turns this off.
"""

from __future__ import annotations

import logging
import os
import re
import time

import httpx

logger = logging.getLogger("baryon.slack.guide")

# "도움말", "뭘 할 수 있어?", "무슨 기능이 있어?" (about the product unless something else is named).
PRODUCT_QUESTION = re.compile(
    r"도움말|(?:뭘|뭐를|무엇을|뭐)\s*할\s*수\s*있|(?:무슨|어떤)\s*기능|기능\s*(?:목록|소개|설명|전부|모두|다)|기능\S*\s*(?:을|를)\s*(?:전부|모두|다)"
    r"|\b(?:help|what\s+can\s+you\s+do)\b", re.IGNORECASE)
# About the product only when nothing else is named: "설명서 알려줘", "사용법 자세히" — but not "법인카드 사용 방법".
HOW_TO = re.compile(
    r"설명서|사용\s*설명|사용법|사용\s*방법|쓰는\s*법|쓰는\s*방법|이용\s*방법|매뉴얼|메뉴얼|안내서|사용\s*안내|가이드"
    r"|어떻게\s*(?:써|쓰|사용|이용|올려|올리|해|하|연결|등록|맡기|물어)|\b(?:manual|user\s*guide|how\s+(?:do\s+i|to)\s+use)\b",
    re.IGNORECASE)
PRODUCT = re.compile(r"바리온|baryon|\bax\b|봇|bot|비서|슬랙|slack|mcp|크롤링|문서로\s*추가|지식\s*공백|온톨로지|웹\s*채팅|너는|너를|당신",
                     re.IGNORECASE)
FILLER = re.compile(r"자세히|상세히|상세하게|자세하게|친절하게|쉽게|전체|전부|모두|다|기본|좀|한번|한\s*번|설명|해\s*줘|해줘|알려\s*줘|알려줘|"
                    r"보여\s*줘|주세요|줄래|해\s*봐|해봐|해|줘|요|은|는|이|가|을|를|의|좀|뭐야|뭐|있어|있나|있니|봐|\?|!|\.|,", re.IGNORECASE)
GUIDE_CHARS = 40_000  # the whole guide fits; the model window is 32k tokens
CACHE_SECONDS = 600

PROMPT = """너는 Baryon AX의 사용 안내 담당이다. 아래 [사용 안내서]만 근거로, 사용자의 질문에 자세하고 친절하게 답하라.

답하는 방법:
- 질문이 설명서 전체("설명서 알려줘", "사용법", "뭘 할 수 있어?")이면 안내서의 모든 장을 빠짐없이, 장마다 소제목(**굵게**)을 달고
  무엇을 하는지, 어디서 어떻게 쓰는지, 실제로 입력할 예시 문장을 들어 설명하라. 요약 몇 줄로 끝내지 마라.
- 질문이 특정 기능(예: 슬랙에서 파일 올리기, 메일 초안, 크롤링, MCP 연결)이면 그 부분을 단계별(1, 2, 3 …)로 자세히 설명하고,
  필요한 권한·설정, 자주 생기는 문제와 해결도 함께 적어라.
- 슬랙에서 물었으니 슬랙에서 쓰는 방법(멘션, 스레드, DM, 첨부, 버튼)을 먼저 보여 주고, 다른 곳(웹·음성 비서·MCP)은 그다음에 적어라.
- 안내서에 없는 기능이나 설정은 지어내지 말고 "안내서에 없다"고 말하라.
- 마지막에 "더 궁금한 점이 있으신가요?" 같은 되묻는 말은 쓰지 말고, 바로 해 볼 수 있는 예시 질문 2~3개로 끝내라.

[사용 안내서: {title}]
{guide}
[안내서 끝]

사용자 질문: {question}"""


def wants_guide(text: str) -> bool:
    """Asking how to use Baryon AX itself: guide words, and either the product is named or nothing else is."""
    text = text or ""
    if not (PRODUCT_QUESTION.search(text) or HOW_TO.search(text)):
        return False
    if PRODUCT.search(text):
        return True
    rest = FILLER.sub(" ", HOW_TO.sub(" ", PRODUCT_QUESTION.sub(" ", text)))
    return len(re.sub(r"[^가-힣A-Za-z0-9]", "", rest)) <= 2  # "법인카드 사용 방법" names something else


class Guide:
    def __init__(self, baryon, document_id: str = "", tag: str = "guide", enabled: bool = True) -> None:
        self.baryon = baryon
        self.document_id = document_id
        self.tag = tag
        self.enabled = enabled
        self._cache: dict[str, tuple[float, dict | None]] = {}

    @classmethod
    def from_env(cls, baryon) -> "Guide":
        return cls(baryon, document_id=os.getenv("SLACK_GUIDE_DOCUMENT_ID", "").strip(),
                   tag=os.getenv("SLACK_GUIDE_TAG", "guide").strip() or "guide",
                   enabled=os.getenv("SLACK_GUIDE", "true").strip().lower() in {"1", "true", "yes", "on"})

    def find(self, slack_user: str) -> dict | None:
        """The guide document ({id, title, content}) this person can read, or None."""
        cached = self._cache.get(slack_user)
        if cached and time.time() - cached[0] < CACHE_SECONDS:
            return cached[1]
        found = None
        try:
            document_id = self.document_id
            if not document_id:
                response = self.baryon.as_user("GET", "/v1/documents", slack_user, params={"tag": self.tag})
                response.raise_for_status()
                listed = response.json().get("data") or []
                listed.sort(key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""), reverse=True)
                document_id = listed[0]["id"] if listed else ""
            if document_id:
                response = self.baryon.as_user("GET", f"/v1/documents/{document_id}", slack_user)
                response.raise_for_status()
                data = response.json()
                content = str(data.get("content") or "")
                if content.strip():
                    found = {"id": document_id, "title": str(data.get("title") or "사용 안내"), "content": content}
        except (httpx.HTTPError, ValueError, KeyError):
            logger.warning("could not read the usage guide; answering from search", exc_info=True)
        self._cache[slack_user] = (time.time(), found)
        return found

    def prompt(self, question: str, document: dict) -> str:
        return PROMPT.format(title=document["title"], guide=document["content"][:GUIDE_CHARS], question=question)
