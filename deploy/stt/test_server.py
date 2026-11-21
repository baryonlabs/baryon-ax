"""STT sidecar tests with a fake recognizer (no model download, no native decoding)."""

from __future__ import annotations

import io
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

import server
from catalog import CATALOG

KEY = "stt-test-key"
AUTH = {"Authorization": f"Bearer {KEY}"}


def wav(seconds: float = 1.0, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * channels * width)
    return buffer.getvalue()


class FakeResult:
    def __init__(self, text: str, lang: str = "<|ko|>") -> None:
        self.text, self.lang = text, lang


class FakeStream:
    def __init__(self, text: str) -> None:
        self.options: dict[str, str] = {}
        self.samples = 0
        self.result = FakeResult(text)

    def set_option(self, key: str, value: str) -> None:
        self.options[key] = value

    def accept_waveform(self, rate: int, samples) -> None:
        self.samples = len(samples)


class FakeRecognizer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.streams: list[FakeStream] = []

    def create_stream(self) -> FakeStream:
        stream = FakeStream(self.text)
        self.streams.append(stream)
        return stream

    def decode_stream(self, stream: FakeStream) -> None:
        pass


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "API_KEY", KEY)
    monkeypatch.setattr(server, "MODEL_DIR", tmp_path)
    server._recognizers.clear()
    server._downloads.clear()
    return TestClient(server.app)


def install(tmp_path, model_id: str, text: str) -> FakeRecognizer:
    spec = CATALOG[model_id]
    folder = tmp_path / spec.directory
    folder.mkdir(parents=True)
    for name in spec.files.values():
        (folder / name).write_bytes(b"x")
    fake = FakeRecognizer(text)
    for language in ("", "ko"):
        server._recognizers[server.recognizer_key(spec, language)] = fake
    return fake


def test_key_required_except_health(api) -> None:
    assert api.get("/health").status_code == 200
    assert api.get("/v1/models").status_code == 401
    assert api.post("/v1/audio/transcriptions", files={"file": ("a.wav", wav())}).status_code == 401


def test_catalog_reports_install_state(api, tmp_path) -> None:
    install(tmp_path, "sensevoice-ko", "안녕")
    data = api.get("/v1/models", headers=AUTH).json()
    states = {m["id"]: m["status"] for m in data["data"]}
    assert states["sensevoice-ko"] == "installed" and states["whisper-turbo"] == "available"
    turbo = next(m for m in data["data"] if m["id"] == "whisper-turbo")
    assert "recommended" in turbo["badges"] and "ko" in turbo["languages"]
    parakeet = next(m for m in data["data"] if m["id"] == "parakeet-tdt-v3")
    assert "ko" not in parakeet["languages"]  # no Korean: never recommend it for Korean users


def test_sensevoice_transcription_strips_tags(api, tmp_path) -> None:
    install(tmp_path, "sensevoice-ko", "<|ko|><|NEUTRAL|>지식 화면 열어 줘")
    response = api.post("/v1/audio/transcriptions", headers=AUTH,
                        files={"file": ("a.wav", wav(1.3))}, data={"model": "sensevoice-ko", "language": "ko"})
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "지식 화면 열어 줘" and body["language"] == "ko"
    assert body["duration_ms"] == 1300 and body["model"] == "sensevoice-ko"


def test_whisper_pads_sets_language_and_collapses_repetition(api, tmp_path) -> None:
    fake = install(tmp_path, "whisper-turbo", "연차 2월 기준이 뭐예요? 연차 2월 기준이 뭐예요? 연차 2월 기")
    body = api.post("/v1/audio/transcriptions", headers=AUTH,
                    files={"file": ("a.wav", wav(1.0))}, data={"model": "whisper-turbo", "language": "ko"}).json()
    assert body["text"] == "연차 2월 기준이 뭐예요?"
    stream = fake.streams[-1]
    assert stream.options == {"max_tokens_per_second": "10", "language": "ko"}
    assert stream.samples == 16000 + 8000  # 0.5 s of trailing silence


def test_stereo_and_other_rates_are_accepted(api, tmp_path) -> None:
    fake = install(tmp_path, "sensevoice-ko", "ok")
    response = api.post("/v1/audio/transcriptions", headers=AUTH, files={"file": ("a.wav", wav(1.0, rate=48000, channels=2))})
    assert response.status_code == 200 and fake.streams[-1].samples == 48000


@pytest.mark.parametrize("payload,status", [
    (b"not audio", 415),
    (None, 415),  # 8-bit PCM
])
def test_rejects_unsupported_audio(api, tmp_path, payload, status) -> None:
    install(tmp_path, "sensevoice-ko", "x")
    data = payload if payload is not None else wav(width=1)
    assert api.post("/v1/audio/transcriptions", headers=AUTH, files={"file": ("a.wav", data)}).status_code == status


def test_rejects_long_audio_unknown_or_missing_model_and_bad_language(api, tmp_path, monkeypatch) -> None:
    install(tmp_path, "sensevoice-ko", "x")
    monkeypatch.setattr(server, "MAX_SECONDS", 2.0)
    assert api.post("/v1/audio/transcriptions", headers=AUTH, files={"file": ("a.wav", wav(3.0))}).status_code == 413
    assert api.post("/v1/audio/transcriptions", headers=AUTH, files={"file": ("a.wav", wav())},
                    data={"model": "nope"}).status_code == 404
    assert api.post("/v1/audio/transcriptions", headers=AUTH, files={"file": ("a.wav", wav())},
                    data={"model": "whisper-tiny"}).status_code == 409
    assert api.post("/v1/audio/transcriptions", headers=AUTH, files={"file": ("a.wav", wav())},
                    data={"language": "ko;rm -rf"}).status_code == 400


def test_download_unknown_and_already_installed(api, tmp_path) -> None:
    install(tmp_path, "sensevoice-ko", "x")
    assert api.post("/v1/models/nope/download", headers=AUTH).status_code == 404
    assert api.post("/v1/models/sensevoice-ko/download", headers=AUTH).json()["status"] == "installed"


def test_download_rejects_tar_slip(api, tmp_path, monkeypatch) -> None:
    import tarfile

    evil = tmp_path / "evil.tar.bz2"
    with tarfile.open(evil, "w:bz2") as tar:
        info = tarfile.TarInfo("../escape.txt")
        payload = b"pwned"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    class Response(io.BytesIO):
        headers = {"Content-Length": str(evil.stat().st_size)}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *a, **k: Response(evil.read_bytes()))
    spec = CATALOG["whisper-tiny"]
    server._downloads[spec.id] = {"status": "downloading"}
    server._download(spec)
    assert server._downloads[spec.id]["status"] == "failed"
    assert "unsafe" in server._downloads[spec.id]["error"]
    assert not (tmp_path.parent / "escape.txt").exists()


@pytest.mark.parametrize("raw,expected", [
    ("지식 화면 열어줘. 지식 화면 열어줘.", "지식 화면 열어줘."),
    ("연차 2월 기준이 뭐예요? 연차 2월 기준이 뭐예요? 연차 2월 기", "연차 2월 기준이 뭐예요?"),
    ("첫 문장입니다. 두 번째 문장입니다.", "첫 문장입니다. 두 번째 문장입니다."),
    ("반복 없음", "반복 없음"),
])
def test_collapse_repetition(raw: str, expected: str) -> None:
    assert server.collapse_repetition(raw) == expected


def test_sensevoice_gets_one_recognizer_per_language() -> None:
    spec = CATALOG["sensevoice-ko"]
    assert server.recognizer_key(spec, "ko") == "sensevoice-ko:ko"
    assert server.recognizer_key(spec, "") == server.recognizer_key(spec, "de") == "sensevoice-ko:auto"
    assert server.recognizer_key(CATALOG["whisper-turbo"], "ko") == "whisper-turbo"
