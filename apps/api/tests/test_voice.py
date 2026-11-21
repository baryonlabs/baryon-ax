"""Voice dictation API: STT proxy, command parsing, audit hygiene, operator-only model installs."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import baryon.main as main
from baryon import voice
from baryon.main import app

client = TestClient(app)
MEMBER = {"X-User-Id": "voice-user", "X-Organization-Id": "voice-org", "X-Roles": "member"}
ADMIN = {**MEMBER, "X-User-Id": "voice-admin", "X-Roles": "admin"}
WAV = b"RIFF....WAVEfmt fake-audio-bytes"


@pytest.fixture()
def stt(monkeypatch):
    """Fake STT sidecar; records requests."""
    seen: list[httpx.Request] = []
    state = {"text": "지식 화면 열어 줘", "status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"default": "sensevoice-ko", "data": [
                {"id": "sensevoice-ko", "name": "SenseVoice", "size_mb": 155, "languages": ["ko"], "badges": ["fast"],
                 "status": "installed", "progress": None, "error": None, "secret_path": "/models/x"},
                {"id": "whisper-turbo", "name": "Whisper", "size_mb": 537, "languages": ["ko"], "badges": ["recommended"],
                 "status": "available", "progress": None, "error": None},
            ]})
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(200, content=b"RIFFfake-wav", headers={"content-type": "audio/wav", "x-audio-duration-ms": "900"})
        if request.url.path.endswith("/download"):
            return httpx.Response(200, json={"id": "whisper-turbo", "status": "downloading", "progress": 0.0})
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"detail": "model whisper-turbo is not installed"})
        return httpx.Response(200, json={"text": state["text"], "language": "ko", "model": "sensevoice-ko",
                                         "duration_ms": 1300, "processing_ms": 30})

    real = httpx.AsyncClient
    monkeypatch.setattr(voice.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(main, "stt_config", voice.SttConfig(base_url="http://stt.test", api_key="stt-key",
                                                            default_model="sensevoice-ko", max_bytes=1024))
    return seen, state


def audit_events(headers: dict, kind: str) -> list[dict]:
    events = client.get("/v1/audit-events", headers={**headers, "X-Roles": "admin"}).json()["data"]
    return [event for event in events if event.get("event_type", event.get("type")) == kind]


def test_transcription_returns_command_and_audits_without_text(stt) -> None:
    seen, _ = stt
    response = client.post("/v1/audio/transcriptions", headers=MEMBER,
                           files={"file": ("rec.wav", WAV, "audio/wav")}, data={"locale": "ko"})
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "지식 화면 열어 줘"
    assert body["command"] == {"action": "open_view", "view": "knowledge", "phrase": "지식 화면 열어 줘"}
    forwarded = seen[-1]
    assert forwarded.headers["authorization"] == "Bearer stt-key"
    assert b'name="language"\r\n\r\nko' in forwarded.content and b"sensevoice-ko" in forwarded.content

    events = audit_events(MEMBER, "audio.transcribed")
    assert events, "transcription must be audited"
    dumped = json.dumps(events[-1], ensure_ascii=False)
    assert "지식 화면" not in dumped and "fake-audio" not in dumped  # neither transcript nor audio in the log
    assert '"characters": 10' in dumped and "open_view" in dumped


def test_dictation_is_not_a_command(stt) -> None:
    _, state = stt
    state["text"] = "새 대화에서 연차 이월 기준 알려줘"
    body = client.post("/v1/audio/transcriptions", headers=MEMBER, files={"file": ("rec.wav", WAV)}).json()
    assert body["command"] is None and body["text"].startswith("새 대화에서")


def test_commands_can_be_disabled(stt) -> None:
    body = client.post("/v1/audio/transcriptions", headers=MEMBER, files={"file": ("rec.wav", WAV)},
                       data={"commands": "false"}).json()
    assert body["command"] is None


def test_upload_limits_and_validation(stt) -> None:
    assert client.post("/v1/audio/transcriptions", headers=MEMBER,
                       files={"file": ("rec.wav", b"x" * 2048)}).status_code == 413
    assert client.post("/v1/audio/transcriptions", headers=MEMBER, files={"file": ("rec.wav", b"")}).status_code == 400
    assert client.post("/v1/audio/transcriptions", headers=MEMBER, files={"file": ("rec.wav", WAV)},
                       data={"model": "../../etc"}).status_code == 400


def test_sidecar_errors_map_to_client_errors(stt) -> None:
    _, state = stt
    state["status"] = 409
    response = client.post("/v1/audio/transcriptions", headers=MEMBER, files={"file": ("rec.wav", WAV)},
                           data={"model": "whisper-turbo"})
    assert response.status_code == 409 and "not installed" in response.json()["detail"]


def test_not_configured_is_503_and_models_report_disabled(monkeypatch) -> None:
    monkeypatch.setattr(main, "stt_config", voice.SttConfig(base_url="", api_key="", default_model="", max_bytes=1024))
    assert client.post("/v1/audio/transcriptions", headers=MEMBER, files={"file": ("rec.wav", WAV)}).status_code == 503
    models = client.get("/v1/audio/models", headers=MEMBER).json()
    assert models["enabled"] is False and models["models"] == []


def test_models_are_whitelisted_and_install_is_operator_only(stt) -> None:
    models = client.get("/v1/audio/models", headers=MEMBER).json()
    assert models["reachable"] is True and models["default"] == "sensevoice-ko" and models["can_manage"] is False
    assert all("secret_path" not in m for m in models["models"])
    assert client.post("/v1/admin/audio/models/whisper-turbo/download", headers=MEMBER).status_code == 403
    assert client.get("/v1/audio/models", headers=ADMIN).json()["can_manage"] is True
    started = client.post("/v1/admin/audio/models/whisper-turbo/download", headers=ADMIN)
    assert started.status_code == 200 and started.json()["status"] == "downloading"
    assert client.post("/v1/admin/audio/models/BAD!/download", headers=ADMIN).status_code == 400


@pytest.mark.parametrize("utterance,expected", [
    ("새 대화 시작해 줘", ("new_chat", None)),
    ("세 대화 시작 해줘", ("new_chat", None)),
    ("새로운 채팅 열어 주세요", ("new_chat", None)),
    ("보내줘", ("send", None)),
    ("다 지워 줘", ("clear", None)),
    ("멈춰", ("stop", None)),
    ("지식 공백 화면 열어", ("open_view", "gaps")),
    ("설정으로 가", ("open_view", "settings")),
    ("open knowledge", ("open_view", "knowledge")),
    ("new chat", ("new_chat", None)),
    ("멈춰어", ("stop", None)),
    ("설정 화면 으로 가 죠", ("open_view", "settings")),
    ("보 내줘", ("send", None)),
])
def test_command_phrases(utterance: str, expected: tuple[str, str | None]) -> None:
    command = voice.parse_command(utterance)
    assert command is not None and (command.action, command.view) == expected


@pytest.mark.parametrize("utterance", [
    "새 대화에서 연차 알려줘",
    "보내는 방법 알려줘",
    "연차 2월 기준이 뭐예요?",
    "HWP 문서를 올리면 출처와 함께 답해줍니다.",
    "",
])
def test_dictation_phrases_are_not_commands(utterance: str) -> None:
    assert voice.parse_command(utterance) is None


def test_speech_cleans_markdown_and_proxies_wav(stt) -> None:
    seen, _ = stt
    response = client.post("/v1/audio/speech", headers=MEMBER,
                           json={"input": "## 연차\n- **3월 말**까지 이월됩니다 [1].", "voice": 2, "locale": "ko"})
    assert response.status_code == 200 and response.content == b"RIFFfake-wav"
    assert response.headers["content-type"] == "audio/wav" and response.headers["x-audio-duration-ms"] == "900"
    sent = json.loads(seen[-1].content)
    assert sent == {"input": "연차. 3월 말까지 이월됩니다.", "voice": 2, "speed": 1.0, "language": "ko", "model": ""}
    assert seen[-1].headers["authorization"] == "Bearer stt-key"


def test_speech_validation(stt, monkeypatch) -> None:
    assert client.post("/v1/audio/speech", headers=MEMBER, json={"input": "```x```"}).status_code == 400
    assert client.post("/v1/audio/speech", headers=MEMBER, json={"input": "hi", "voice": 12}).status_code == 422
    assert client.post("/v1/audio/speech", headers=MEMBER, json={"input": "hi", "model": "../x"}).status_code == 400
    monkeypatch.setattr(main, "stt_config", voice.SttConfig(base_url="", api_key="", default_model="", max_bytes=1024))
    assert client.post("/v1/audio/speech", headers=MEMBER, json={"input": "hi"}).status_code == 503


@pytest.mark.parametrize("raw,spoken", [
    ("네, 맞아요!", "네, 맞아요!"),
    ("자세한 건 [인사규정](https://intra/a) 참고 [2, 3]", "자세한 건 인사규정 참고"),
    ("코드:\n```py\nx = 1\n```\n끝", "코드: 끝"),
    ("*강조*와 `snake_case`", "강조와 snake_case"),
])
def test_speakable(raw: str, spoken: str) -> None:
    assert voice.speakable(raw) == spoken


def test_voice_style_adds_spoken_answer_hint() -> None:
    text_request = main.ChatRequest(messages=[{"role": "user", "content": "연차?"}])
    voice_request = main.ChatRequest(messages=[{"role": "user", "content": "연차?"}], response_style="voice")
    adapter = main.model_adapter
    assert "[음성 대화]" not in adapter.system_prompt(text_request, None, [])
    assert "[음성 대화]" in adapter.system_prompt(voice_request, None, [])
