"""Speech model catalog (speech-to-text and text-to-speech) for the Baryon AX voice sidecar.

Every model runs locally with sherpa-onnx (Apache-2.0): audio never leaves the
server. Models are downloaded on demand from the sherpa-onnx model release and
unpacked under STT_MODEL_DIR.

Choices were measured on Korean speech (macOS ko_KR voices plus the SenseVoice
Korean sample, Apple M-series CPU, int8):

  sensevoice-ko  ~0.03 s per utterance, good Korean, inserts spaces inside words
  whisper-turbo  ~0.8 s per utterance, best Korean spacing and English terms
                 (HWP, 문서), needs a repetition guard on very short clips
  whisper-tiny   small multilingual fallback

The 2025-09-09 SenseVoice export decoded Korean as Cantonese with sherpa-onnx
2.0.2, so the catalog pins the 2024-07-17 export.

Text-to-speech (live voice conversation):

  supertonic-3   Supertone Supertonic 3, 31 languages incl. Korean, 10 voices,
                 ~0.4-0.8 s per sentence on CPU (8 steps). Whisper turbo read
                 its Korean output back verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    kind: str                       # sense_voice | whisper | nemo_transducer | supertonic
    archive: str                    # file name in the sherpa-onnx model release
    directory: str                  # directory inside the archive
    files: dict[str, str]           # role -> file name inside the directory
    size_mb: int                    # download size
    languages: tuple[str, ...]
    license: str
    badges: tuple[str, ...] = ()    # "recommended", "fast", "english"
    notes: str = ""
    options: dict[str, str] = field(default_factory=dict)
    task: str = "stt"               # stt | tts
    release: str = "asr-models"     # sherpa-onnx release tag holding the archive

    @property
    def url(self) -> str:
        return f"{RELEASES}/{self.release}/{self.archive}"


CATALOG: dict[str, ModelSpec] = {spec.id: spec for spec in [
    ModelSpec(
        id="whisper-turbo",
        name="Whisper Large v3 Turbo",
        kind="whisper",
        archive="sherpa-onnx-whisper-turbo.tar.bz2",
        directory="sherpa-onnx-whisper-turbo",
        files={"encoder": "turbo-encoder.int8.onnx", "decoder": "turbo-decoder.int8.onnx", "tokens": "turbo-tokens.txt"},
        size_mb=537,
        languages=("ko", "en", "ja", "zh", "multi"),
        license="MIT (OpenAI Whisper)",
        badges=("recommended",),
        notes="Most accurate Korean (spacing, English terms). About 1 s per short utterance on CPU.",
        options={"max_tokens_per_second": "10"},
    ),
    ModelSpec(
        id="sensevoice-ko",
        name="SenseVoice Small (ko·en·ja·zh·yue)",
        kind="sense_voice",
        archive="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        directory="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
        files={"model": "model.int8.onnx", "tokens": "tokens.txt"},
        size_mb=155,
        languages=("ko", "en", "ja", "zh", "yue"),
        license="FunASR Model License (check terms before commercial redistribution)",
        badges=("fast",),
        notes="Tens of milliseconds per utterance on CPU. May insert spaces inside Korean words.",
    ),
    ModelSpec(
        id="whisper-tiny",
        name="Whisper Tiny",
        kind="whisper",
        archive="sherpa-onnx-whisper-tiny.tar.bz2",
        directory="sherpa-onnx-whisper-tiny",
        files={"encoder": "tiny-encoder.int8.onnx", "decoder": "tiny-decoder.int8.onnx", "tokens": "tiny-tokens.txt"},
        size_mb=110,
        languages=("ko", "en", "ja", "zh", "multi"),
        license="MIT (OpenAI Whisper)",
        notes="Smallest multilingual model; lower Korean accuracy.",
        options={"max_tokens_per_second": "10"},
    ),
    ModelSpec(
        id="parakeet-tdt-v3",
        name="Parakeet TDT 0.6B v3",
        kind="nemo_transducer",
        archive="sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
        directory="sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        files={"encoder": "encoder.int8.onnx", "decoder": "decoder.int8.onnx", "joiner": "joiner.int8.onnx", "tokens": "tokens.txt"},
        size_mb=464,
        languages=("en", "de", "fr", "es", "it", "pt", "nl", "pl", "multi-eu"),
        license="CC-BY-4.0 (NVIDIA)",
        badges=("english",),
        notes="25 European languages, no Korean. Use for English/European teams.",
    ),
    ModelSpec(
        id="supertonic-3",
        name="Supertonic 3 (음성 합성)",
        kind="supertonic",
        task="tts",
        release="tts-models",
        archive="sherpa-onnx-supertonic-3-tts-int8-2026-05-11.tar.bz2",
        directory="sherpa-onnx-supertonic-3-tts-int8-2026-05-11",
        files={"duration_predictor": "duration_predictor.int8.onnx", "text_encoder": "text_encoder.int8.onnx",
               "vector_estimator": "vector_estimator.int8.onnx", "vocoder": "vocoder.int8.onnx",
               "tts_json": "tts.json", "unicode_indexer": "unicode_indexer.bin", "voice_style": "voice.bin"},
        size_mb=145,
        languages=("ko", "en", "ja", "de", "es", "fr", "multi"),
        license="OpenRAIL-M (Supertone; use restrictions apply)",
        badges=("recommended",),
        notes="Speaks the answers in live conversation. 10 voices, about 0.5 s per sentence on CPU.",
        options={"num_steps": "8"},
    ),
]}

DEFAULT_MODEL = "sensevoice-ko"
DEFAULT_TTS_MODEL = "supertonic-3"
