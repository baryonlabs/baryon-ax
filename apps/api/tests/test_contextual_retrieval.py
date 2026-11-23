import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="baryon-ax-ctxq-test-"))
os.environ.setdefault("DOCUMENT_STORE", str(TEST_ROOT / "documents"))
os.environ.setdefault("AUDIT_LOG", str(TEST_ROOT / "audit" / "events.jsonl"))
os.environ.setdefault("FASTEMBED_CACHE_PATH", str(TEST_ROOT / "fastembed"))
os.environ.setdefault("MEM0_HISTORY_DB", str(TEST_ROOT / "mem0" / "history.db"))
os.environ.setdefault("SYSTEM_METRICS_ENABLED", "false")

from fastapi.testclient import TestClient

from baryon.main import Message, app, retrieval_queries

client = TestClient(app)
ORG = "ctxq-org"
HEADERS = {"X-User-Id": "ctxq-user", "X-Organization-Id": ORG, "X-Roles": "admin"}

DOCS = {
    "rfp.md": "REQ-007 분석 에이전트 (Text-to-SQL 엔진). 자연어 질의를 SQL로 변환해 정량 데이터를 추출하고 시각화한다. "
              "허용된 테이블 외 접근 금지, 조인 깊이 제한.",
    "weekly.md": "주간업무보고서. 다시 확인할 일정과 회의 안내. 다음 주 보고 계획.",
    "display.md": "시각화 표시 규칙. 차트와 카드, 지도 타일 실패 시 안내 문구.",
}


def setup_module() -> None:
    for name, text in DOCS.items():
        response = client.post("/v1/documents", files={"file": (name, text.encode())},
                               data={"allowed_roles": "member,admin"}, headers=HEADERS)
        assert response.status_code == 200, response.text


def titles(messages: list[dict]) -> list[str]:
    response = client.post("/v1/chat/completions", json={"messages": messages, "stream": False}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return [item["title"] for item in response.json()["citations"]]


def test_queries_join_earlier_user_turns() -> None:
    messages = [Message(role="user", content="복합 text2sql 요구사항 점검"), Message(role="assistant", content="답"),
                Message(role="user", content="다시 해봐")]
    assert retrieval_queries(messages) == ["다시 해봐", "복합 text2sql 요구사항 점검 다시 해봐"]
    assert retrieval_queries(messages[-1:]) == ["다시 해봐"]
    # The opening turn names the topic and is kept even when later turns do not mention it.
    long = messages[:2] + [Message(role="user", content=f"잡담 {n}") for n in range(4)] + [Message(role="user", content="어디에 있어?")]
    assert retrieval_queries(long)[1] == "복합 text2sql 요구사항 점검 잡담 2 잡담 3 어디에 있어?"
    os.environ["RETRIEVAL_HISTORY_TURNS"] = "0"
    try:
        assert retrieval_queries(messages) == ["다시 해봐"]
    finally:
        del os.environ["RETRIEVAL_HISTORY_TURNS"]


def test_follow_up_without_topic_words_finds_the_topic() -> None:
    history = [{"role": "user", "content": "Text-to-SQL 엔진 요구사항이 뭐야?"},
               {"role": "assistant", "content": "찾지 못했습니다."}]
    # "다시 확인해봐" alone matches only the weekly report; with the earlier turn the RFP reaches the top two.
    assert "rfp.md" not in titles([{"role": "user", "content": "다시 확인해봐"}])
    assert "rfp.md" in titles(history + [{"role": "user", "content": "다시 확인해봐"}])[:2]


def test_search_while_documents_are_added() -> None:
    """Uploads and searches run on different threads; iterating the live dict raised RuntimeError."""
    import threading

    from baryon.main import Principal, documents

    principal = Principal(user_id="ctxq-user", organization_id=ORG, roles={"admin"})
    template = next(iter(documents.documents.values()))
    errors: list[BaseException] = []
    stop = threading.Event()

    def add() -> None:
        for n in range(400):
            clone = template.__class__(**{**template.__dict__, "id": f"race-{n}"})
            documents.documents[clone.id] = clone
        stop.set()

    def search() -> None:
        try:
            while not stop.is_set():
                documents._lexical_search(principal, "Text-to-SQL")
                documents.list_for(principal)
        except BaseException as error:  # noqa: BLE001 - the regression is any exception here
            errors.append(error)

    threads = [threading.Thread(target=search), threading.Thread(target=add)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for n in range(400):
        documents.documents.pop(f"race-{n}", None)
    assert errors == []


def test_new_topic_in_the_last_turn_still_wins() -> None:
    history = [{"role": "user", "content": "Text-to-SQL 엔진 요구사항이 뭐야?"},
               {"role": "assistant", "content": "REQ-007입니다."}]
    found = titles(history + [{"role": "user", "content": "지도 타일 실패하면 차트 표시 규칙은?"}])
    assert found[0] == "display.md"
