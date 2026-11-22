"""Small audio helpers for the phone gateway: AudioSocket framing, resampling, WAV, chunking."""

from __future__ import annotations

import asyncio
import io
import re
import struct
import wave

import numpy as np

# Asterisk AudioSocket: 1 byte kind, 2 bytes big-endian length, payload.
KIND_HANGUP = 0x00
KIND_UUID = 0x01
KIND_DTMF = 0x03
KIND_AUDIO = 0x10
KIND_ERROR = 0xFF

PHONE_RATE = 8000          # AudioSocket carries signed linear 16-bit, 8 kHz, mono
FRAME_MS = 20
FRAME_SAMPLES = PHONE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    header = await reader.readexactly(3)
    kind, length = header[0], struct.unpack(">H", header[1:])[0]
    payload = await reader.readexactly(length) if length else b""
    return kind, payload


def frame(kind: int, payload: bytes = b"") -> bytes:
    return bytes([kind]) + struct.pack(">H", len(payload)) + payload


def pcm_to_float(payload: bytes) -> np.ndarray:
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def resample(samples: np.ndarray, source: int, target: int) -> np.ndarray:
    """Linear resampling. Going down, a short moving average first keeps aliasing out of the phone band."""
    if source == target or not len(samples):
        return samples.astype(np.float32)
    if source > target:
        width = max(1, round(source / target))
        if width > 1:
            samples = np.convolve(samples, np.ones(width, dtype=np.float32) / width, mode="same")
    count = max(1, int(round(len(samples) * target / source)))
    positions = np.linspace(0, len(samples) - 1, count)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def to_wav(samples: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(float_to_pcm(samples))
    return buffer.getvalue()


def from_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        channels, rate = w.getnchannels(), w.getframerate()
        samples = pcm_to_float(w.readframes(w.getnframes()))
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


_URL = re.compile(r"https?://(?:www\.)?([^/\s]+)\S*")
_MARKS = re.compile(r"[*`#>|~_]+|\[\d+(?:,\s*\d+)*\]")


def speakable(text: str) -> str:
    """Keep only what reads well aloud: no Markdown marks, citation numbers or full URLs."""
    text = _URL.sub(r"\1", text)
    text = re.sub(r"\s+", " ", _MARKS.sub("", text))
    return re.sub(r"\s+([.,!?])", r"\1", text).strip()


class SentenceChunker:
    """Splits a streamed answer into speakable chunks; the first one is flushed early at a comma.
    Same rules as apps/web/src/live.ts so phone and browser sound alike."""

    _SENTENCE = re.compile(r"[.!?。！？](?=\s|$)|\n+")
    _COMMA = re.compile(r"[,，]\s")

    def __init__(self) -> None:
        self.buffer = ""
        self.first = True

    def push(self, token: str) -> list[str]:
        self.buffer += token
        chunks: list[str] = []
        while True:
            end = self._boundary()
            if end < 0:
                return chunks
            chunk, self.buffer = self.buffer[:end].strip(), self.buffer[end:]
            if chunk:
                chunks.append(chunk)
                self.first = False

    def flush(self) -> str:
        rest, self.buffer, self.first = self.buffer.strip(), "", True
        return rest

    def _boundary(self) -> int:
        for match in self._SENTENCE.finditer(self.buffer):
            end = match.end()
            if end >= len(self.buffer) and "\n" not in match.group():
                return -1  # "3." may continue as "3.5"
            if len(self.buffer[:end].strip()) >= 6:
                return end
        if self.first:
            for match in self._COMMA.finditer(self.buffer):
                if match.start() >= 18:
                    return match.start() + 1
        if len(self.buffer) > 160:
            space = self.buffer.rfind(" ", 0, 140)
            return space if space > 40 else 140
        return -1
