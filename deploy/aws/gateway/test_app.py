"""Tests for the Bedrock gateway with a fake Bedrock client (no AWS calls)."""

from __future__ import annotations

import io
import json

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

import app as gateway

KEY = "test-key"
AUTH = {"Authorization": f"Bearer {KEY}"}


class FakeBedrock:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail: ClientError | None = None

    def converse(self, **kwargs):
        self.calls.append(("converse", kwargs))
        if self.fail:
            raise self.fail
        return {"output": {"message": {"content": [{"text": "연차는 "}, {"text": "15일입니다."}]}},
                "stopReason": "end_turn", "usage": {"inputTokens": 12, "outputTokens": 5}}

    def converse_stream(self, **kwargs):
        self.calls.append(("converse_stream", kwargs))
        return {"stream": iter([
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"delta": {"text": "안녕"}}},
            {"contentBlockDelta": {"delta": {"text": "하세요"}}},
            {"messageStop": {"stopReason": "max_tokens"}},
            {"metadata": {"usage": {"inputTokens": 3, "outputTokens": 2}}},
        ])}

    def invoke_model(self, **kwargs):
        self.calls.append(("invoke_model", kwargs))
        body = json.loads(kwargs["body"])
        if "texts" in body:
            return {"body": io.BytesIO(json.dumps({"embeddings": [[0.1] * 4 for _ in body["texts"]]}).encode())}
        return {"body": io.BytesIO(json.dumps({"embedding": [float(len(body["inputText"]))] * 4}).encode())}


@pytest.fixture()
def fake(monkeypatch):
    client = FakeBedrock()
    monkeypatch.setattr(gateway, "_client", client)
    monkeypatch.setattr(gateway, "API_KEY", KEY)
    return client


def test_rejects_missing_or_wrong_key(fake) -> None:
    api = TestClient(gateway.app)
    assert api.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401
    assert api.post("/v1/embeddings", json={"input": "x"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert api.get("/health").status_code == 200  # health stays public for the load balancer


def test_chat_maps_system_and_merges_consecutive_roles(fake) -> None:
    api = TestClient(gateway.app)
    body = {"model": "baryon-chat", "max_tokens": 100, "temperature": 0.2, "messages": [
        {"role": "system", "content": "근거로만 답하라"},
        {"role": "assistant", "content": "무엇을 도와드릴까요?"},
        {"role": "user", "content": "연차?"},
        {"role": "user", "content": [{"type": "text", "text": "규정 알려줘"}]},
    ]}
    response = api.post("/v1/chat/completions", json=body, headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "연차는 15일입니다."
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"] == {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}
    _, kwargs = fake.calls[-1]
    assert kwargs["system"] == [{"text": "근거로만 답하라"}]
    assert [t["role"] for t in kwargs["messages"]] == ["user", "assistant", "user"]  # leading user added, users merged
    assert kwargs["messages"][-1]["content"] == [{"text": "연차?"}, {"text": "규정 알려줘"}]
    assert kwargs["inferenceConfig"] == {"maxTokens": 100, "temperature": 0.2}


def test_chat_stream_emits_openai_chunks_and_usage(fake) -> None:
    api = TestClient(gateway.app)
    body = {"stream": True, "stream_options": {"include_usage": True}, "messages": [{"role": "user", "content": "hi"}]}
    with api.stream("POST", "/v1/chat/completions", json=body, headers=AUTH) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in response.iter_lines() if line.startswith("data:")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line[5:]) for line in lines[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert text == "안녕하세요"
    assert [c["choices"][0]["finish_reason"] for c in chunks if c["choices"]][-1] == "length"
    assert chunks[-1]["usage"]["total_tokens"] == 5


def test_bedrock_errors_map_to_http_status(fake) -> None:
    fake.fail = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")
    response = TestClient(gateway.app).post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]}, headers=AUTH)
    assert response.status_code == 429
    assert "ThrottlingException" in response.json()["detail"]


def test_titan_embeddings_one_call_per_text(fake, monkeypatch) -> None:
    monkeypatch.setattr(gateway, "EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
    response = TestClient(gateway.app).post("/v1/embeddings", json={"input": ["ab", "abcd"]}, headers=AUTH)
    assert response.status_code == 200
    data = response.json()["data"]
    assert [d["index"] for d in data] == [0, 1] and data[1]["embedding"] == [4.0] * 4
    bodies = [json.loads(kw["body"]) for name, kw in fake.calls if name == "invoke_model"]
    assert all(b["dimensions"] == gateway.EMBEDDING_DIMENSION and b["normalize"] for b in bodies)


def test_cohere_embeddings_batch_and_input_type(fake, monkeypatch) -> None:
    monkeypatch.setattr(gateway, "EMBEDDING_MODEL_ID", "cohere.embed-multilingual-v3")
    api = TestClient(gateway.app)
    api.post("/v1/embeddings", json={"model": "baryon-embed-query", "input": "질문"}, headers=AUTH)
    api.post("/v1/embeddings", json={"model": "baryon-embed", "input": ["a"] * 100}, headers=AUTH)
    bodies = [json.loads(kw["body"]) for name, kw in fake.calls if name == "invoke_model"]
    assert bodies[0]["input_type"] == "search_query"
    assert [len(b["texts"]) for b in bodies[1:]] == [96, 4]
    assert all(b["input_type"] == "search_document" for b in bodies[1:])


def test_rejects_bad_input(fake) -> None:
    api = TestClient(gateway.app)
    assert api.post("/v1/chat/completions", json={"messages": []}, headers=AUTH).status_code == 400
    assert api.post("/v1/embeddings", json={"input": [1, 2]}, headers=AUTH).status_code == 400
