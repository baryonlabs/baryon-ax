"""Baryon AX voice sidecar: speech-to-text and text-to-speech (sherpa-onnx, fully offline).

Endpoints (OpenAI-compatible where it matters):
    GET  /health
    GET  /v1/models                       catalog with installed / downloading state
    POST /v1/models/{id}/download         fetch and unpack a model in the background
    POST /v1/audio/transcriptions         multipart: file (16 kHz+ mono WAV), model, language
    POST /v1/audio/speech                 JSON: input, model, voice, language, speed -> audio/wav

The Baryon AX API is the only intended client: it authenticates users, audits the
request and never stores audio. Set STT_API_KEY so only the API can call this
service.

Environment:
    STT_MODEL_DIR      where models are unpacked (default /models)
    STT_DEFAULT_MODEL  model used when a request names none (default sensevoice-ko)
    TTS_DEFAULT_MODEL  speech model used when a request names none (default supertonic-3)
    STT_API_KEY        bearer key required on every endpoint except /health
    STT_THREADS        decoding threads per request (default 2)
    STT_MAX_SECONDS    longest accepted audio (default 120)
"""

from __future__ import annotations

import hmac
import io
import logging
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from catalog import CATALOG, DEFAULT_MODEL, DEFAULT_TTS_MODEL, ModelSpec

logger = logging.getLogger("baryon-stt")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

MODEL_DIR = Path(os.getenv("STT_MODEL_DIR", "/models"))
API_KEY = os.getenv("STT_API_KEY", "")
THREADS = int(os.getenv("STT_THREADS", "2"))
MAX_SECONDS = float(os.getenv("STT_MAX_SECONDS", "120"))
MAX_BYTES = int(MAX_SECONDS * 16000 * 2 * 2) + 1024  # generous for 32 kHz input

app = FastAPI(title="Baryon AX STT", docs_url=None, redoc_url=None, openapi_url=None)

_recognizers: dict[str, Any] = {}
_load_lock = threading.Lock()
_downloads: dict[str, dict[str, Any]] = {}
_synthesizers: dict[str, Any] = {}
_tts_locks: dict[str, threading.Lock] = {}
MAX_SPEECH_CHARS = 600


def require_key(authorization: str | None) -> None:
    if not API_KEY:
        return  # local development; the compose profile always sets a key
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token.encode(), API_KEY.encode()):
        raise HTTPException(401, "invalid STT key")


def model_path(spec: ModelSpec) -> Path:
    return MODEL_DIR / spec.directory


def installed(spec: ModelSpec) -> bool:
    return all((model_path(spec) / name).is_file() for name in spec.files.values())


# --- model download ---------------------------------------------------------------

def _download(spec: ModelSpec) -> None:
    state = _downloads[spec.id]
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=MODEL_DIR) as tmp:
            archive = Path(tmp) / spec.archive
            with urllib.request.urlopen(spec.url, timeout=60) as response, open(archive, "wb") as out:  # noqa: S310 (fixed https URL)
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                while chunk := response.read(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    state["progress"] = round(done / total, 3) if total else None
            with tarfile.open(archive, "r:bz2") as tar:
                for member in tar.getmembers():  # refuse absolute paths and ../ (tar slip)
                    target = (Path(tmp) / member.name).resolve()
                    if not str(target).startswith(str(Path(tmp).resolve())) or member.issym() or member.islnk():
                        raise ValueError(f"unsafe archive member {member.name}")
                tar.extractall(tmp)  # noqa: S202 (members checked above)
            extracted = Path(tmp) / spec.directory
            missing = [name for name in spec.files.values() if not (extracted / name).is_file()]
            if missing:
                raise ValueError(f"archive is missing {missing}")
            destination = model_path(spec)
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(extracted), destination)
        state.update(status="installed", progress=1.0, error="")
        logger.info("installed %s", spec.id)
    except Exception as error:  # report, never crash the server
        state.update(status="failed", error=str(error)[:300])
        logger.exception("download of %s failed", spec.id)


@app.post("/v1/models/{model_id}/download")
async def download(model_id: str, authorization: str | None = Header(None)) -> dict:
    require_key(authorization)
    spec = CATALOG.get(model_id)
    if not spec:
        raise HTTPException(404, "unknown model")
    if installed(spec):
        return {"id": model_id, "status": "installed"}
    current = _downloads.get(model_id)
    if current and current.get("status") == "downloading":
        return {"id": model_id, **current}
    _downloads[model_id] = {"status": "downloading", "progress": 0.0, "error": "", "started_at": time.time()}
    threading.Thread(target=_download, args=(spec,), daemon=True).start()
    return {"id": model_id, "status": "downloading", "progress": 0.0}


def describe(spec: ModelSpec) -> dict:
    state = _downloads.get(spec.id, {})
    status = "installed" if installed(spec) else state.get("status", "available")
    return {
        "id": spec.id, "name": spec.name, "task": spec.task, "size_mb": spec.size_mb, "languages": list(spec.languages),
        "badges": list(spec.badges), "license": spec.license, "notes": spec.notes,
        "status": status, "progress": state.get("progress"), "error": state.get("error") or None,
    }


@app.get("/v1/models")
async def models(authorization: str | None = Header(None)) -> dict:
    require_key(authorization)
    default = os.getenv("STT_DEFAULT_MODEL", DEFAULT_MODEL)
    return {"object": "list", "default": default, "default_tts": os.getenv("TTS_DEFAULT_MODEL", DEFAULT_TTS_MODEL),
            "data": [describe(spec) for spec in CATALOG.values()]}


# --- recognition -----------------------------------------------------------------------

SENSEVOICE_LANGUAGES = {"zh", "en", "ja", "ko", "yue"}


def recognizer_key(spec: ModelSpec, language: str = "") -> str:
    """SenseVoice fixes the language when the recognizer is created (a per-stream option
    is ignored, and auto-detect reads short Korean commands as Japanese), so it gets one
    recognizer per language. Other models take the language per stream."""
    if spec.kind != "sense_voice":
        return spec.id
    return f"{spec.id}:{language if language in SENSEVOICE_LANGUAGES else 'auto'}"


def load(spec: ModelSpec, language: str = ""):
    """Create (once) the sherpa-onnx recognizer for a model (and language, for SenseVoice)."""
    key = recognizer_key(spec, language)
    with _load_lock:
        if key in _recognizers:
            return _recognizers[key]
        import sherpa_onnx  # imported lazily so tests can run without the native wheel

        base = model_path(spec)
        f = {role: str(base / name) for role, name in spec.files.items()}
        if spec.kind == "sense_voice":
            recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=f["model"], tokens=f["tokens"], num_threads=THREADS, use_itn=False, language=key.rsplit(":", 1)[1])
        elif spec.kind == "whisper":
            recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                encoder=f["encoder"], decoder=f["decoder"], tokens=f["tokens"], num_threads=THREADS,
                language="", task="transcribe")
        elif spec.kind == "nemo_transducer":
            recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=f["encoder"], decoder=f["decoder"], joiner=f["joiner"], tokens=f["tokens"],
                num_threads=THREADS, model_type="nemo_transducer")
        else:
            raise RuntimeError(f"unsupported model kind {spec.kind}")
        _recognizers[key] = recognizer
        return recognizer


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decode a PCM WAV into mono float32 samples. The web client always sends 16 kHz mono WAV."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as error:
        raise HTTPException(415, "audio must be a PCM WAV file (the Baryon AX web client converts recordings)") from error
    if width != 2:
        raise HTTPException(415, "only 16-bit PCM WAV is supported")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate < 8000:
        raise HTTPException(415, "sample rate too low")
    if len(samples) / rate > MAX_SECONDS:
        raise HTTPException(413, f"audio longer than {int(MAX_SECONDS)} s")
    return samples, rate


_SENTENCE = re.compile(r"[^.?!。？！]+[.?!。？！]?")


def collapse_repetition(text: str) -> str:
    """Whisper repeats short utterances ("A? A? A? A"). Keep the first copy and drop
    consecutive duplicates and a trailing fragment that merely restarts the sentence."""
    parts = [p.strip() for p in _SENTENCE.findall(text) if p.strip()]
    if len(parts) < 2:
        return text.strip()
    kept: list[str] = []
    for part in parts:
        if kept and part == kept[-1]:
            continue
        kept.append(part)
    if len(kept) >= 2 and kept[0].startswith(kept[-1].rstrip(".?!")) and kept[-1] != kept[0]:
        kept.pop()  # truncated restart of the first sentence
    return " ".join(kept)


SENSEVOICE_TAG = re.compile(r"<\|[^|]*\|>")


def transcribe(spec: ModelSpec, samples: np.ndarray, rate: int, language: str) -> dict:
    recognizer = load(spec, language)
    stream = recognizer.create_stream()
    for name, value in spec.options.items():
        stream.set_option(name, value)
    if spec.kind == "whisper":
        if language:
            stream.set_option("language", language)
        # Whisper hallucinates on very short clips without trailing silence.
        samples = np.concatenate([samples, np.zeros(int(rate * 0.5), dtype=np.float32)])
    stream.accept_waveform(rate, samples)
    recognizer.decode_stream(stream)
    result = stream.result
    text = SENSEVOICE_TAG.sub("", result.text or "").strip()
    if spec.kind == "whisper":
        text = collapse_repetition(text)
    detected = (getattr(result, "lang", "") or "").strip("<|>") or language or None
    return {"text": text, "language": detected}


def installed_spec(model_id: str, task: str) -> ModelSpec:
    spec = CATALOG.get(model_id)
    if not spec or spec.task != task:
        raise HTTPException(404, f"unknown {task} model {model_id}")
    if not installed(spec):
        raise HTTPException(409, f"model {model_id} is not installed")
    return spec


# --- text-to-speech -------------------------------------------------------------------

def load_tts(spec: ModelSpec):
    with _load_lock:
        if spec.id in _synthesizers:
            return _synthesizers[spec.id]
        import sherpa_onnx

        base = model_path(spec)
        f = {role: str(base / name) for role, name in spec.files.items()}
        if spec.kind != "supertonic":
            raise RuntimeError(f"unsupported speech model kind {spec.kind}")
        config = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
            supertonic=sherpa_onnx.OfflineTtsSupertonicModelConfig(**f), num_threads=THREADS, provider="cpu"))
        _synthesizers[spec.id] = sherpa_onnx.OfflineTts(config)
        _tts_locks[spec.id] = threading.Lock()
        return _synthesizers[spec.id]


def to_wav(samples, rate: int) -> bytes:
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buffer.getvalue()


def synthesize(spec: ModelSpec, text: str, voice: int, language: str, speed: float) -> tuple[bytes, float]:
    import sherpa_onnx

    tts = load_tts(spec)
    config = sherpa_onnx.GenerationConfig()
    config.sid = voice
    config.speed = speed
    config.num_steps = int(spec.options.get("num_steps", "8"))
    config.extra = {"lang": language}  # assign the dict: item assignment on .extra edits a copy and is lost
    with _tts_locks[spec.id]:  # one generation per model at a time; each takes well under a second
        audio = tts.generate(text, config)
    return to_wav(audio.samples, audio.sample_rate), len(audio.samples) / audio.sample_rate


class SpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=MAX_SPEECH_CHARS)
    model: str = ""
    voice: int = Field(default=0, ge=0, le=9)
    language: str = Field(default="ko", pattern=r"^[a-z]{2,3}$")
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


@app.post("/v1/audio/speech")
async def speech(request: SpeechRequest, authorization: str | None = Header(None)) -> Response:
    require_key(authorization)
    spec = installed_spec(request.model or os.getenv("TTS_DEFAULT_MODEL", DEFAULT_TTS_MODEL), "tts")
    text = request.input.strip()
    if not text:
        raise HTTPException(400, "input is empty")
    started = time.perf_counter()
    audio, seconds = await run_in_threadpool(synthesize, spec, text, request.voice, request.language, request.speed)
    return Response(audio, media_type="audio/wav", headers={
        "X-Model": spec.id, "X-Audio-Duration-Ms": str(int(seconds * 1000)),
        "X-Processing-Ms": str(int((time.perf_counter() - started) * 1000)),
    })


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form(""),
    language: str = Form(""),
    authorization: str | None = Header(None),
) -> dict:
    require_key(authorization)
    model_id = model or os.getenv("STT_DEFAULT_MODEL", DEFAULT_MODEL)
    spec = installed_spec(model_id, "stt")
    if language and not re.fullmatch(r"[a-z]{2,3}", language):
        raise HTTPException(400, "language must be an ISO code such as ko or en")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "audio too large")
    samples, rate = read_wav(data)
    started = time.perf_counter()
    result = await run_in_threadpool(transcribe, spec, samples, rate, language)  # keep the event loop free
    return {
        **result, "model": model_id,
        "duration_ms": int(len(samples) / rate * 1000),
        "processing_ms": int((time.perf_counter() - started) * 1000),
    }


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "installed": [spec.id for spec in CATALOG.values() if installed(spec)],
            "default": os.getenv("STT_DEFAULT_MODEL", DEFAULT_MODEL),
            "default_tts": os.getenv("TTS_DEFAULT_MODEL", DEFAULT_TTS_MODEL)}
