"""Recordings from the phone (a meeting, a lecture, a voice memo) turned into text for the secretary.

The speech model hears at most about 30 seconds at a time, so a long recording is cut into pieces of
15 to 25 seconds, each cut placed at the quietest moment in that range so words are not split. Silent
pieces are skipped, and the sign-offs speech models invent from silence ("시청해 주셔서 감사합니다")
are dropped. The result keeps a time stamp per piece: "[03:12] …".

Upload protocol (WebSocket /v1/voice/upload, the same device token as calls):

    app -> {"type": "recording", "rate": 16000}     then PCM16 mono chunks (binary), then {"type": "end"}
    gateway -> {"type": "received", "seconds"}        all audio is in
               {"type": "progress", "done", "total"}   while transcribing
               {"type": "transcript", "text", "seconds", "pieces"}
               {"type": "error", "message"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable

import numpy as np

from audio import resample

logger = logging.getLogger("gateway")

RATE = 16000
MAX_SECONDS = 40 * 60
MIN_PIECE, MAX_PIECE = 15.0, 25.0
FRAME = 0.03
SILENT_RMS = 0.003
SILENT_PEAK = 0.05
MAX_PARALLEL = 2

Transcribe = Callable[[np.ndarray, int], Awaitable[str]]


def pieces(samples: np.ndarray, rate: int = RATE) -> list[tuple[int, int]]:
    """(start, end) sample ranges of 15-25 s, each ending at the quietest 30 ms in its window."""
    total, frame = len(samples), int(rate * FRAME)
    out, start = [], 0
    while total - start > int(rate * MAX_PIECE):
        low, high = start + int(rate * MIN_PIECE), start + int(rate * MAX_PIECE)
        window = samples[low:high]
        frames = len(window) // frame
        energy = np.sqrt(np.mean(window[:frames * frame].reshape(frames, frame) ** 2, axis=1))
        cut = low + int(np.argmin(energy)) * frame + frame // 2
        out.append((start, cut))
        start = cut
    if total - start > int(rate * 0.5):
        out.append((start, total))
    return out


def stamp(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60:02d}:{seconds % 60:02d}"


async def transcribe_recording(samples: np.ndarray, transcribe: Transcribe, phantom: re.Pattern,
                               progress: Callable[[int, int], Awaitable[None]] | None = None,
                               rate: int = RATE) -> tuple[str, int]:
    """The recording as time-stamped lines, and how many pieces had speech."""
    ranges = pieces(samples, rate)
    results: list[str] = [""] * len(ranges)
    gate = asyncio.Semaphore(MAX_PARALLEL)
    done = 0

    async def one(index: int, start: int, end: int) -> None:
        nonlocal done
        piece = samples[start:end]
        if float(np.sqrt(np.mean(piece ** 2))) >= SILENT_RMS and float(np.max(np.abs(piece))) >= SILENT_PEAK:
            async with gate:
                try:
                    text = " ".join((await transcribe(piece, rate)).split())
                except Exception:  # noqa: BLE001 - one bad piece should not lose the whole recording
                    logger.exception("recording piece %d failed", index)
                    text = ""
            if text and not phantom.match(text):
                results[index] = f"[{stamp(start / rate)}] {text}"
        done += 1
        if progress:
            await progress(done, len(ranges))

    await asyncio.gather(*(one(index, start, end) for index, (start, end) in enumerate(ranges)))
    lines = [line for line in results if line]
    return "\n".join(lines), len(lines)


async def receive_upload(websocket: Any, transcribe: Transcribe, phantom: re.Pattern) -> None:
    """One recording over /v1/voice/upload: take the audio, answer with its text."""
    async def send(event: dict) -> None:
        await websocket.send(json.dumps(event, ensure_ascii=False))

    rate, chunks, size, started = RATE, [], 0, False
    async for message in websocket:
        if isinstance(message, bytes):
            if not started:
                continue
            size += len(message)
            if size > MAX_SECONDS * rate * 2:
                await send({"type": "error", "message": f"녹음은 {MAX_SECONDS // 60}분까지 보낼 수 있어요."})
                return
            chunks.append(message)
            continue
        try:
            command = json.loads(message)
        except ValueError:
            continue
        if command.get("type") == "recording":
            rate = int(command.get("rate") or RATE)
            if rate not in (8000, 16000, 22050, 24000, 32000, 44100, 48000):
                await send({"type": "error", "message": "지원하지 않는 샘플레이트예요."})
                return
            started = True
        elif command.get("type") == "end" and started:
            break
    else:
        return  # the app went away before the end
    samples = np.frombuffer(b"".join(chunks)[: size - size % 2], dtype="<i2").astype(np.float32) / 32768.0
    samples = resample(samples, rate, RATE)
    seconds = len(samples) / RATE
    await send({"type": "received", "seconds": round(seconds, 1)})
    if seconds < 1:
        await send({"type": "error", "message": "녹음이 너무 짧아요."})
        return

    async def progress(done: int, total: int) -> None:
        await send({"type": "progress", "done": done, "total": total})

    text, count = await transcribe_recording(samples, transcribe, phantom, progress)
    logger.info("recording of %.0fs transcribed: %d pieces with speech, %d chars", seconds, count, len(text))
    await send({"type": "transcript", "text": text, "seconds": round(seconds, 1), "pieces": count})
