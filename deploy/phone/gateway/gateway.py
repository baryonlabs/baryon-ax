"""Phone gateway: a phone call to the voice secretary.

Asterisk answers the SIP call (from an Android/iOS SIP app over the tailnet, which the
phone treats as a normal call, so the car's Bluetooth hands-free works) and streams
the audio here with AudioSocket. For each call this service

    listens   Silero VAD finds where each utterance starts and ends
    hears     the on-prem STT sidecar (deploy/stt) transcribes it
    asks      the voice secretary (apps/secretary) answers or acts, with spoken confirmations
    speaks    the answer is synthesized sentence by sentence (Supertonic) and streamed back
    yields    if you start talking while it speaks, playback stops and the agent is interrupted

Environment:
    GATEWAY_LISTEN            host:port for AudioSocket (default 0.0.0.0:9092)
    STT_URL, STT_API_KEY      the speech sidecar (deploy/stt)
    STT_MODEL                 dictation model for calls (default whisper-turbo: phone audio is 8 kHz,
                              and Whisper hears it far better than SenseVoice)
    SECRETARY_URL, SECRETARY_API_KEY, SECRETARY_OWNER
    VAD_MODEL                 silero_vad.onnx path (default /models/silero_vad.onnx)
    VOICE_LANGUAGE            ko (default) or en
    VOICE_ID, VOICE_SPEED     Supertonic voice 0-9 and speed (default 0, 1.05)
    GREETING                  first sentence spoken when the call connects
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import numpy as np

from audio import (FRAME_BYTES, FRAME_MS, KIND_AUDIO, KIND_ERROR, KIND_HANGUP, KIND_UUID, PHONE_RATE,
                   SentenceChunker, float_to_pcm, frame, from_wav, pcm_to_float, read_frame, resample, speakable, to_wav)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("phone-gateway")
logging.getLogger("httpx").setLevel(logging.WARNING)  # request lines would log session ids on every utterance

VAD_RATE = 16000
STOP_WORDS = re.compile(r"^(?:멈춰|그만|중지|스톱|stop)[.!?\s]*$", re.I)
BARGE_IN_FRAMES = 8         # 8 x 20 ms phone frames = 160 ms of speech while the secretary talks interrupts it
LEAD_SECONDS = 0.08         # how far ahead of real time audio is written to Asterisk


@dataclass
class Settings:
    stt_url: str = field(default_factory=lambda: os.getenv("STT_URL", "http://stt:8000").rstrip("/"))
    stt_key: str = field(default_factory=lambda: os.getenv("STT_API_KEY", ""))
    stt_model: str = field(default_factory=lambda: os.getenv("STT_MODEL", "whisper-turbo"))
    secretary_url: str = field(default_factory=lambda: os.getenv("SECRETARY_URL", "").rstrip("/"))
    secretary_key: str = field(default_factory=lambda: os.getenv("SECRETARY_API_KEY", ""))
    owner: str = field(default_factory=lambda: os.getenv("SECRETARY_OWNER", "phone-owner"))
    language: str = field(default_factory=lambda: os.getenv("VOICE_LANGUAGE", "ko"))
    voice: int = field(default_factory=lambda: int(os.getenv("VOICE_ID", "0")))
    speed: float = field(default_factory=lambda: float(os.getenv("VOICE_SPEED", "1.05")))
    greeting: str = field(default_factory=lambda: os.getenv("GREETING", "네, 비서입니다. 말씀하세요."))
    vad_model: str = field(default_factory=lambda: os.getenv("VAD_MODEL", "/models/silero_vad.onnx"))


# --- collaborators (real ones talk HTTP; tests pass fakes) -----------------------------------

class Speech(Protocol):
    async def transcribe(self, samples: np.ndarray, rate: int) -> str: ...
    async def synthesize(self, text: str) -> np.ndarray: ...  # float32 at PHONE_RATE


class Secretary(Protocol):
    async def start(self) -> None: ...
    async def turn(self, text: str) -> str: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...
    def events(self): ...  # async iterator of event dicts


class HttpSpeech:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=60.0, headers={"Authorization": f"Bearer {settings.stt_key}"})

    async def transcribe(self, samples: np.ndarray, rate: int) -> str:
        response = await self.client.post(f"{self.settings.stt_url}/v1/audio/transcriptions",
                                          files={"file": ("utterance.wav", to_wav(samples, rate), "audio/wav")},
                                          data={"language": self.settings.language, "model": self.settings.stt_model})
        response.raise_for_status()
        return str(response.json().get("text") or "").strip()

    async def synthesize(self, text: str) -> np.ndarray:
        response = await self.client.post(f"{self.settings.stt_url}/v1/audio/speech", json={
            "input": text, "voice": self.settings.voice, "speed": self.settings.speed, "language": self.settings.language})
        response.raise_for_status()
        samples, rate = from_wav(response.content)
        return resample(samples, rate, PHONE_RATE)


class HttpSecretary:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None),
                                        headers={"Authorization": f"Bearer {settings.secretary_key}"})
        self.id = ""

    async def start(self) -> None:
        response = await self.client.post(f"{self.settings.secretary_url}/v1/sessions", json={"owner": self.settings.owner})
        response.raise_for_status()
        self.id = response.json()["id"]

    async def turn(self, text: str) -> str:
        response = await self.client.post(f"{self.settings.secretary_url}/v1/sessions/{self.id}/turn", json={"text": text})
        response.raise_for_status()
        return response.json().get("used", "")

    async def interrupt(self) -> None:
        await self.client.post(f"{self.settings.secretary_url}/v1/sessions/{self.id}/interrupt")

    async def close(self) -> None:
        if self.id:
            try:
                await self.client.delete(f"{self.settings.secretary_url}/v1/sessions/{self.id}")
            except httpx.HTTPError:
                pass
        await self.client.aclose()

    async def events(self):
        async with self.client.stream("GET", f"{self.settings.secretary_url}/v1/sessions/{self.id}/events") as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])


class SileroVad:
    """sherpa-onnx Silero VAD at 16 kHz (phone audio is upsampled first)."""

    WINDOW = 512

    def __init__(self, model: str) -> None:
        import sherpa_onnx

        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = model
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = 0.6   # end of an utterance
        config.silero_vad.min_speech_duration = 0.1    # short commands such as "멈춰" still count
        config.sample_rate = VAD_RATE
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)
        self.pending = np.zeros(0, dtype=np.float32)
        self.speaking = False

    def push(self, samples: np.ndarray) -> tuple[bool, list[np.ndarray]]:
        """Returns (caller is speaking now, finished utterances).

        A 20 ms phone frame is shorter than one 32 ms VAD window, so many frames run no
        window at all. The last decision is kept for those frames; resetting it would make
        "speaking" flicker and barge-in would never trigger."""
        self.pending = np.concatenate([self.pending, samples])
        done: list[np.ndarray] = []
        while len(self.pending) >= self.WINDOW:
            self.vad.accept_waveform(self.pending[: self.WINDOW])
            self.pending = self.pending[self.WINDOW:]
            self.speaking = self.vad.is_speech_detected()
            while not self.vad.empty():
                done.append(np.array(self.vad.front.samples, dtype=np.float32))
                self.vad.pop()
        return self.speaking, done


# --- one call ----------------------------------------------------------------------------

class Call:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, speech: Speech,
                 secretary: Secretary, vad: Any, settings: Settings) -> None:
        self.reader, self.writer = reader, writer
        self.speech, self.secretary, self.vad, self.settings = speech, secretary, vad, settings
        self.id = ""
        self.generation = 0
        self.queue: asyncio.Queue[tuple[int, asyncio.Task]] = asyncio.Queue()
        self.playing = False
        self.agent_busy = False
        self.chunker = SentenceChunker()
        self.discard_until_done = False
        self.speech_windows = 0
        self.tasks: list[asyncio.Task] = []
        self.log: list[str] = []  # what was heard and said, for tests; never persisted

    async def run(self) -> None:
        try:
            kind, payload = await read_frame(self.reader)
            if kind != KIND_UUID:
                logger.warning("call did not start with a UUID frame (kind %s)", kind)
                return
            self.id = str(uuid.UUID(bytes=payload))
            logger.info("call %s connected", self.id)
            self.tasks.append(asyncio.create_task(self._player()))
            self.say(self.settings.greeting)
            await self.secretary.start()
            self.tasks.append(asyncio.create_task(self._listen_secretary()))
            while True:
                kind, payload = await read_frame(self.reader)
                if kind == KIND_AUDIO:
                    self._hear(payload)
                elif kind in (KIND_HANGUP, KIND_ERROR):
                    break
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception:
            logger.exception("call %s failed", self.id)
        finally:
            logger.info("call %s ended", self.id)
            for task in self.tasks:
                task.cancel()
            await self.secretary.close()
            self.writer.close()

    # --- hearing ---

    def _hear(self, payload: bytes) -> None:
        samples = resample(pcm_to_float(payload), PHONE_RATE, VAD_RATE)
        speaking, utterances = self.vad.push(samples)
        if speaking and (self.playing or self.agent_busy):
            self.speech_windows += 1
            if self.speech_windows == BARGE_IN_FRAMES:  # once per stretch of speech
                self.tasks.append(asyncio.create_task(self.interrupt()))
        elif not speaking:
            self.speech_windows = 0
        for utterance in utterances:
            self.tasks.append(asyncio.create_task(self._utterance(utterance)))

    async def _utterance(self, samples: np.ndarray) -> None:
        started = time.perf_counter()
        try:
            text = await self.speech.transcribe(samples, VAD_RATE)
        except Exception:
            logger.exception("transcription failed")
            self.say("잘 못 들었어요. 다시 말씀해 주세요.")
            return
        if len(re.sub(r"[\s.,!?]", "", text)) < 1:
            return
        self.log.append(f"heard: {text}")
        logger.info("call %s heard %d chars in %.2fs", self.id, len(text), time.perf_counter() - started)
        if STOP_WORDS.match(text):
            await self.interrupt()
            return
        # The newest thing the caller said wins: stop talking and drop the old answer first.
        if self.playing or self.agent_busy or not self.queue.empty():
            await self.interrupt()
        self.agent_busy = True
        self.chunker = SentenceChunker()
        try:
            await self.secretary.turn(text)
        except Exception:
            logger.exception("secretary turn failed")
            self.agent_busy = False
            self.say("비서에 연결할 수 없어요.")

    async def interrupt(self) -> None:
        """Barge-in or "멈춰": stop talking now and stop the agent's current step."""
        self.generation += 1
        self.chunker.flush()
        if self.agent_busy:
            self.discard_until_done = True
            try:
                await self.secretary.interrupt()
            except Exception:
                logger.debug("interrupt failed", exc_info=True)
        logger.info("call %s interrupted", self.id)

    # --- speaking ---

    def say(self, text: str) -> None:
        text = speakable(text)
        if not text:
            return
        self.log.append(f"said: {text}")
        self.queue.put_nowait((self.generation, asyncio.create_task(self.speech.synthesize(text))))

    async def _listen_secretary(self) -> None:
        async for event in self.secretary.events():
            kind = event.get("type")
            if self.discard_until_done:
                if kind == "done":
                    self.discard_until_done = False
                    self.agent_busy = False
                continue
            if kind == "text":
                for chunk in self.chunker.push(event.get("delta", "")):
                    self.say(chunk)
            elif kind == "status":
                self.say(event.get("text", ""))
            elif kind == "confirm":
                rest = self.chunker.flush()
                if rest:
                    self.say(rest)
                self.say(event.get("prompt", ""))
                self.agent_busy = False  # waiting for the caller's yes or no
            elif kind == "confirmed":
                self.agent_busy = True
            elif kind == "done":
                rest = self.chunker.flush()
                if rest:
                    self.say(rest)
                self.agent_busy = False
            elif kind == "error":
                self.say("문제가 생겼어요. 다시 말씀해 주세요.")

    async def _player(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            generation, task = await self.queue.get()
            try:
                samples = await task
            except Exception:
                logger.exception("synthesis failed")
                continue
            if generation != self.generation or not len(samples):
                continue
            pcm = float_to_pcm(samples)
            self.playing = True
            start = loop.time()
            for index in range(0, len(pcm), FRAME_BYTES):
                if generation != self.generation:
                    break
                chunk = pcm[index:index + FRAME_BYTES].ljust(FRAME_BYTES, b"\x00")
                self.writer.write(frame(KIND_AUDIO, chunk))
                await self.writer.drain()
                due = start + (index // FRAME_BYTES + 1) * FRAME_MS / 1000 - LEAD_SECONDS
                delay = due - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            self.playing = not self.queue.empty()


async def serve(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    if not settings.secretary_url:
        raise SystemExit("SECRETARY_URL is required")
    speech = HttpSpeech(settings)
    host, _, port = os.getenv("GATEWAY_LISTEN", "0.0.0.0:9092").rpartition(":")

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        call = Call(reader, writer, speech=speech, secretary=HttpSecretary(settings),
                    vad=SileroVad(settings.vad_model), settings=settings)
        await call.run()

    server = await asyncio.start_server(handle, host or "0.0.0.0", int(port))
    logger.info("AudioSocket gateway listening on %s", ", ".join(str(s.getsockname()) for s in server.sockets))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
