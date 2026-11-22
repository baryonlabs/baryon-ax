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
    DENOISE_MODEL             GTCRN speech denoiser (default /models/gtcrn_simple.onnx; see voicefilter.py)
    GATEWAY_VOICE_FILTER      strict (default: only the voice close to the mic), normal, or off
    VOICE_LANGUAGE            ko (default) or en
    VOICE_ID, VOICE_SPEED     Supertonic voice 0-9 and speed (default 0, 1.05)
    GREETING                  first sentence spoken when the call connects
"""

from __future__ import annotations

import asyncio
import difflib
import hmac
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import numpy as np

from earcons import earcon
from voicefilter import MODES as FILTER_MODES, VoiceFilter
from audio import (FRAME_BYTES, FRAME_MS, KIND_AUDIO, KIND_ERROR, KIND_HANGUP, KIND_UUID, PHONE_RATE,
                   SentenceChunker, float_to_pcm, frame, from_wav, pcm_to_float, read_frame, resample, speakable, to_wav)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("phone-gateway")
logging.getLogger("httpx").setLevel(logging.WARNING)  # request lines would log session ids on every utterance

VAD_RATE = 16000
# Speech models invent these sign-offs from road noise or silence (they were trained on video
# captions). Nobody says them to a secretary, so a short utterance that is only this is dropped
# and the app is told, so the caller can report it.
PHANTOM_PHRASES = re.compile(
    r"^(?:다음\s*영상에서\s*(?:만나요|뵙겠습니다)|영상\s*시청해\s*주셔서\s*감사합니다|시청해\s*주셔서\s*감사합니다|"
    r"구독과\s*좋아요(?:\s*부탁드립니다)?|좋아요와?\s*구독(?:\s*부탁드립니다)?|감사합니다|MBC\s*뉴스.*|자막\s*제공.*)[.!?\s]*$",
    re.I,
)
STOP_WORDS = re.compile(r"^(?:멈춰|그만|중지|스톱|stop)[.!?\s]*$", re.I)
# Secretary events shown on the app's screen (renamed so they do not clash with call events).
SCREEN_EVENTS = {"activity": "activity", "activity_done": "activity_done", "task": "task",
                 "text": "agent_text", "done": "agent_done", "confirmed": "confirmed"}
# Measured on a real call: speech segments had RMS ~0.015 and peaks ~0.5, while room noise that
# Silero let through (and Whisper turned into "다음 영상에서 만나요") stayed under RMS 0.0025 and
# peak 0.12. Segments that quiet are dropped before speech-to-text.
MIN_SPEECH_RMS = float(os.getenv("GATEWAY_MIN_SPEECH_RMS", "0.004"))
MIN_SPEECH_PEAK = float(os.getenv("GATEWAY_MIN_SPEECH_PEAK", "0.1"))
BARGE_IN_FRAME_RMS = 0.01   # only frames this loud count toward interrupting the secretary
BARGE_IN_FRAME_RMS_STRICT = 0.02  # "strict" filter: a voice across the room does not cut the secretary off
BARGE_IN_MS = 350           # this much speech while the secretary talks interrupts it (echo bursts are shorter)
ECHO_WINDOW_SECONDS = 15.0  # what the secretary said this recently may come back as echo
ECHO_SIMILARITY = 0.6
TYPING_QUIET_SECONDS = 2.0  # after the last key press, the mic is not treated as speech
LEAD_SECONDS = 0.08
THINKING_TICK_SECONDS = 1.2  # 동… 동… 동…: a bell while the secretary works and nothing has been heard for this long         # how far ahead of real time audio is written to Asterisk


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
    denoise_model: str = field(default_factory=lambda: os.getenv("DENOISE_MODEL", "/models/gtcrn_simple.onnx"))
    voice_filter: str = field(default_factory=lambda: os.getenv("GATEWAY_VOICE_FILTER", "strict"))
    # Log what callers said. Off by default; turn on only briefly on your own machine to debug.
    debug_transcripts: bool = field(default_factory=lambda: os.getenv("GATEWAY_DEBUG_TRANSCRIPTS", "") == "1")
    debug_audio_dir: str = field(default_factory=lambda: os.getenv("GATEWAY_DEBUG_AUDIO_DIR", ""))
    earcons: bool = field(default_factory=lambda: os.getenv("GATEWAY_EARCONS", "1") != "0")
    app_tokens: tuple[str, ...] = field(default_factory=lambda: tuple(
        token.strip() for token in os.getenv("GATEWAY_APP_TOKENS", "").split(",") if len(token.strip()) >= 24))


# --- collaborators (real ones talk HTTP; tests pass fakes) -----------------------------------

class Speech(Protocol):
    async def transcribe(self, samples: np.ndarray, rate: int) -> str: ...
    async def synthesize(self, text: str, voice: int | None = None, speed: float | None = None) -> tuple[np.ndarray, int]: ...


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

    async def synthesize(self, text: str, voice: int | None = None, speed: float | None = None):
        response = await self.client.post(f"{self.settings.stt_url}/v1/audio/speech", json={
            "input": text, "voice": self.settings.voice if voice is None else voice,
            "speed": self.settings.speed if speed is None else speed, "language": self.settings.language})
        response.raise_for_status()
        return from_wav(response.content)


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

class AudioSocketTransport:
    """Asterisk AudioSocket: 8 kHz signed linear frames over TCP (SIP calls)."""

    rate = PHONE_RATE
    audio = True

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer, self.id = reader, writer, ""

    async def start(self) -> bool:
        kind, payload = await read_frame(self.reader)
        if kind != KIND_UUID:
            logger.warning("call did not start with a UUID frame (kind %s)", kind)
            return False
        self.id = str(uuid.UUID(bytes=payload))
        return True

    async def receive(self) -> bytes | None:
        while True:
            kind, payload = await read_frame(self.reader)
            if kind == KIND_AUDIO:
                return payload
            if kind in (KIND_HANGUP, KIND_ERROR):
                return None

    async def send_audio(self, pcm: bytes) -> None:
        self.writer.write(frame(KIND_AUDIO, pcm))
        await self.writer.drain()

    async def send_event(self, event: dict) -> None:
        pass  # a plain phone call has no screen

    async def close(self) -> None:
        self.writer.close()


class WebSocketTransport:
    """The Baryon voice app: 16 kHz PCM16 over a WebSocket, plus JSON events for its screen.

    client -> server: binary PCM16LE mono 16 kHz; text {"type": "hangup"}
    server -> client: binary PCM16LE mono 16 kHz paced in real time;
                      text {"type": "heard"|"said"|"status"|"confirm"|"flush"|"state", ...}"""

    rate = VAD_RATE

    def __init__(self, websocket: Any) -> None:
        self.websocket, self.id = websocket, str(uuid.uuid4())
        # ?audio=0: a typed conversation (no microphone, no synthesized speech, text only)
        path = getattr(getattr(websocket, "request", None), "path", "") or ""
        self.audio = "audio=0" not in path

    async def start(self) -> bool:
        return True

    async def receive(self) -> bytes | dict | None:
        while True:
            try:
                message = await self.websocket.recv()
            except Exception:  # closed
                return None
            if isinstance(message, bytes):
                return message
            try:
                command = json.loads(message)
            except ValueError:
                continue
            if command.get("type") == "hangup":
                return None
            if command.get("type") == "text" and str(command.get("text", "")).strip():
                return {"text": str(command["text"])[:2000]}  # typed, or a tapped answer such as "응"
            if command.get("type") == "dictate":
                return {"dictate": bool(command.get("on"))}  # speech to text for the input box only
            if command.get("type") == "prefs":  # the caller's settings: earcons on/off, voice, speed
                return {"prefs": {key: command[key] for key in ("earcons", "voice", "speed", "filter") if key in command}}
            if command.get("type") == "typing":
                return {"typing": True}  # the caller is on the keyboard: what the mic hears is key clicks

    async def send_audio(self, pcm: bytes) -> None:
        await self.websocket.send(pcm)

    async def send_event(self, event: dict) -> None:
        try:
            await self.websocket.send(json.dumps(event, ensure_ascii=False))
        except Exception:
            pass

    async def close(self) -> None:
        try:
            await self.websocket.close()
        except Exception:
            pass


def similar(a: str, b: str) -> float:
    """How alike two utterances are, ignoring spaces and punctuation (0..1)."""
    clean = lambda text: re.sub(r"[\s.,!?~·]", "", text)
    a, b = clean(a), clean(b)
    if not a or not b:
        return 0.0
    if a in b:  # a fragment of what was said
        return 1.0 if len(a) >= 3 else 0.5
    return difflib.SequenceMatcher(None, a, b).ratio()


class Call:
    def __init__(self, transport: Any, *, speech: Speech, secretary: Secretary, vad: Any, settings: Settings,
                 voice_filter: VoiceFilter | None = None) -> None:
        self.transport = transport
        self.speech, self.secretary, self.vad, self.settings = speech, secretary, vad, settings
        self.voice_filter = voice_filter or VoiceFilter()
        self.filter_mode = settings.voice_filter if settings.voice_filter in FILTER_MODES else "strict"
        self.id = ""
        self.rate = transport.rate
        self.frame_bytes = self.rate * FRAME_MS // 1000 * 2
        self.speech_ms = 0.0
        self.recent_said: list[tuple[float, str]] = []
        self.playing_until = 0.0
        self.last_sound = 0.0
        self.vad_speaking = False
        self.earcons_on = True
        self.voice: int | None = None   # the caller's choice from the app; None = server default
        self.speed: float | None = None
        self.awaiting_answer = False  # a yes/no question is open: one-syllable answers ("네", "응") count
        self.audio = getattr(transport, "audio", True)
        self.dictating = False
        self.typing_until = 0.0
        self.dictation: list[np.ndarray] = []
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
            if not await self.transport.start():
                return
            self.id = self.transport.id
            logger.info("call %s connected (%d Hz)", self.id, self.rate)
            self.tasks.append(asyncio.create_task(self._player()))
            self.tasks.append(asyncio.create_task(self._thinking_ticks()))
            self.say(self.settings.greeting)
            await self.secretary.start()
            self.tasks.append(asyncio.create_task(self._listen_secretary()))
            while True:
                payload = await self.transport.receive()
                if payload is None:
                    break
                if isinstance(payload, dict) and "prefs" in payload:
                    self._apply_prefs(payload["prefs"])
                elif isinstance(payload, dict) and "typing" in payload:
                    self.typing_until = time.monotonic() + TYPING_QUIET_SECONDS
                elif isinstance(payload, dict) and "dictate" in payload:
                    await self._dictate(payload["dictate"])
                elif isinstance(payload, dict):
                    self.tasks.append(asyncio.create_task(self._handle_text(payload["text"], typed=True)))
                elif self.dictating:
                    self.dictation.append(pcm_to_float(payload))
                else:
                    self._hear(payload)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception:
            logger.exception("call %s failed", self.id)
        finally:
            logger.info("call %s ended", self.id)
            for task in self.tasks:
                task.cancel()
            await self.secretary.close()
            await self.transport.close()

    # --- hearing ---

    def _hear(self, payload: bytes) -> None:
        if time.monotonic() < self.typing_until:
            return  # key clicks, not speech
        incoming = pcm_to_float(payload)
        samples = resample(incoming, self.rate, VAD_RATE)
        speaking, utterances = self.vad.push(samples)
        if speaking != self.vad_speaking:  # the app's ear moves while you speak
            self.vad_speaking = speaking
            self.tasks.append(asyncio.create_task(self.transport.send_event({"type": "vad", "speaking": speaking})))
        threshold = BARGE_IN_FRAME_RMS_STRICT if self.filter_mode == "strict" else BARGE_IN_FRAME_RMS
        loud = float(np.sqrt(np.mean(incoming ** 2))) >= threshold if len(incoming) else False
        if speaking and loud and (self.playing or self.agent_busy):
            before = self.speech_ms
            self.speech_ms += len(incoming) * 1000 / self.rate
            if before < BARGE_IN_MS <= self.speech_ms:  # once per stretch of speech
                self.tasks.append(asyncio.create_task(self.interrupt()))
        elif not speaking:
            self.speech_ms = 0.0
        for utterance in utterances:
            self.tasks.append(asyncio.create_task(self._utterance(utterance)))

    async def _utterance(self, samples: np.ndarray) -> None:
        started = time.perf_counter()
        rms, peak = float(np.sqrt(np.mean(samples ** 2))), float(np.max(np.abs(samples)))
        if rms < MIN_SPEECH_RMS or peak < MIN_SPEECH_PEAK:
            logger.info("call %s dropped a quiet %.1fs segment (rms %.4f, peak %.2f)", self.id,
                        len(samples) / VAD_RATE, rms, peak)
            await self.transport.send_event({"type": "ignored", "reason": "quiet"})
            return
        verdict = await asyncio.to_thread(self.voice_filter.check, samples, VAD_RATE, self.filter_mode)
        if not verdict.keep:  # room noise, or someone talking near the caller
            logger.info("call %s dropped a %.1fs %s segment (denoised rms %.4f, kept %.0f%%)", self.id,
                        len(samples) / VAD_RATE, verdict.reason, verdict.rms, verdict.ratio * 100)
            await self.transport.send_event({"type": "ignored", "reason": verdict.reason})
            return
        samples = verdict.samples
        try:
            text = await self.speech.transcribe(samples, VAD_RATE)
        except Exception:
            logger.exception("transcription failed")
            self.cue("error")
            self.say("잘 못 들었어요. 다시 말씀해 주세요.")
            return
        if self.settings.debug_audio_dir:
            self._keep_for_debugging(samples, text)
        await self._handle_text(text, started=started)

    def _keep_for_debugging(self, samples: np.ndarray, text: str) -> None:
        """GATEWAY_DEBUG_AUDIO_DIR: keep each utterance and its transcript to measure recognition.
        Off by default; it records what callers say, so use it only briefly on your own machine."""
        folder = Path(self.settings.debug_audio_dir)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{self.id[:8]}-{len(list(folder.glob('*.wav'))):03d}"
        (folder / f"{name}.wav").write_bytes(to_wav(samples, VAD_RATE))
        (folder / f"{name}.txt").write_text(text, encoding="utf-8")

    async def _handle_text(self, text: str, *, typed: bool = False, started: float | None = None) -> None:
        if len(re.sub(r"[\s.,!?]", "", text)) < 1:
            return
        if not typed and self._is_echo(text):
            logger.info("call %s ignored %d chars of its own echo", self.id, len(text))
            return
        if not typed and not self.awaiting_answer and len(re.sub(r"[\s.,!?~]", "", text)) <= 1:
            logger.info("call %s dropped a one-syllable fragment", self.id)  # "아", "자", "음": not a request
            await self.transport.send_event({"type": "ignored", "reason": "fragment"})
            return
        self.awaiting_answer = False
        if not typed and PHANTOM_PHRASES.match(text.strip()):
            logger.info("call %s dropped a %d char phantom transcript", self.id, len(text))
            await self.transport.send_event({"type": "phantom", "text": text})
            return
        if not typed:
            self.cue("heard")
        self.log.append(f"heard: {text}")
        await self.transport.send_event({"type": "heard", "text": text})
        if self.settings.debug_transcripts:
            logger.info("call %s heard: %s", self.id, text)
        logger.info("call %s %s %d chars%s", self.id, "got typed" if typed else "heard", len(text),
                    f" in {time.perf_counter() - started:.2f}s" if started else "")
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

    def _apply_prefs(self, prefs: dict) -> None:
        if isinstance(prefs.get("earcons"), bool):
            self.earcons_on = prefs["earcons"]
        try:
            if "voice" in prefs:
                self.voice = max(0, min(9, int(prefs["voice"])))
            if "speed" in prefs:
                self.speed = max(0.7, min(1.5, float(prefs["speed"])))
        except (TypeError, ValueError):
            pass
        if prefs.get("filter") in FILTER_MODES:
            self.filter_mode = prefs["filter"]

    async def _dictate(self, on: bool) -> None:
        """The app's mic button in the text box: transcribe what was said and hand it back as text,
        without sending it to the secretary, so the caller can edit it first."""
        if on:
            self.dictating, self.dictation = True, []
            await self.transport.send_event({"type": "dictating", "on": True})
            return
        self.dictating = False
        audio, self.dictation = (np.concatenate(self.dictation) if self.dictation else np.zeros(0, np.float32)), []
        text = ""
        if len(audio) > self.rate // 4:
            try:
                text = await self.speech.transcribe(resample(audio, self.rate, VAD_RATE), VAD_RATE)
            except Exception:
                logger.exception("dictation failed")
        await self.transport.send_event({"type": "dictation", "text": text})

    def _is_echo(self, text: str) -> bool:
        """A phone on speaker can feed the secretary's own voice back. An utterance heard while
        (or just after) it talked that matches what it said is that echo, not the caller."""
        now = time.monotonic()
        self.recent_said = [(at, said) for at, said in self.recent_said if now - at < ECHO_WINDOW_SECONDS]
        if not (self.playing or now - self.playing_until < 1.5):
            return False
        spoken = " ".join(said for _, said in self.recent_said)
        return any(similar(text, said) >= ECHO_SIMILARITY for _, said in self.recent_said) or similar(text, spoken) >= 0.9

    async def interrupt(self) -> None:
        """Barge-in or "멈춰": stop talking now and stop the agent's current step."""
        self.generation += 1
        self.chunker.flush()
        await self.transport.send_event({"type": "flush"})  # the app drops audio it has buffered
        self.cue("interrupted")
        if self.agent_busy:
            self.discard_until_done = True
            try:
                await self.secretary.interrupt()
            except Exception:
                logger.debug("interrupt failed", exc_info=True)
        logger.info("call %s interrupted", self.id)

    # --- speaking ---

    def cue(self, name: str) -> None:
        """A non-verbal sound (earcons.py) queued like speech, so it reaches the car the same way."""
        if not self.settings.earcons or not self.earcons_on or not self.audio:
            return
        self.last_sound = time.monotonic()
        done: asyncio.Future = asyncio.get_running_loop().create_future()
        done.set_result((earcon(name, self.rate), ""))
        self.queue.put_nowait((self.generation, done))

    async def _thinking_ticks(self) -> None:
        """While the secretary works with nothing to say, a soft tick every few seconds tells the
        driver the line is alive."""
        while True:
            await asyncio.sleep(0.5)
            quiet = time.monotonic() - max(self.last_sound, self.playing_until) >= THINKING_TICK_SECONDS
            if self.agent_busy and not self.playing and self.queue.empty() and quiet:
                self.cue("thinking")

    def say(self, text: str) -> None:
        text = speakable(text)
        if not text:
            return
        self.log.append(f"said: {text}")
        self.recent_said.append((time.monotonic(), text))
        if not self.audio:  # typed conversation: show the words, do not speak them
            self.tasks.append(asyncio.create_task(self.transport.send_event({"type": "said", "text": text})))
            return
        self.queue.put_nowait((self.generation, asyncio.create_task(self._synthesize(text))))

    async def _synthesize(self, text: str) -> tuple[np.ndarray, str]:
        samples, rate = await self.speech.synthesize(text, voice=self.voice, speed=self.speed)
        return resample(samples, rate, self.rate), text

    async def _listen_secretary(self) -> None:
        async for event in self.secretary.events():
            kind = event.get("type")
            if kind in SCREEN_EVENTS:  # progress for the app's screen: steps, background tasks, live text
                await self.transport.send_event({**event, "type": SCREEN_EVENTS[kind]})
            if kind == "task" and event.get("tool") == "goal" and event.get("status") in ("done", "failed"):
                self.cue("task_done" if event["status"] == "done" else "error")
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
                self.cue("question")
                self.say(event.get("prompt", ""))
                await self.transport.send_event({"type": "confirm", "prompt": event.get("prompt", "")})
                self.agent_busy = False  # waiting for the caller's yes or no
                self.awaiting_answer = True
            elif kind == "confirmed":
                self.agent_busy = True
            elif kind == "done":
                rest = self.chunker.flush()
                if rest:
                    self.say(rest)
                self.agent_busy = False
            elif kind == "error":
                self.cue("error")
                self.say("문제가 생겼어요. 다시 말씀해 주세요.")

    async def _player(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            generation, task = await self.queue.get()
            try:
                samples, text = await task
            except Exception:
                logger.exception("synthesis failed")
                continue
            if generation != self.generation or not len(samples):
                continue
            pcm = float_to_pcm(samples)
            self.playing = True
            if text:  # earcons have no words
                await self.transport.send_event({"type": "said", "text": text})
            start = loop.time()
            step = self.frame_bytes
            for index in range(0, len(pcm), step):
                if generation != self.generation:
                    break
                await self.transport.send_audio(pcm[index:index + step].ljust(step, b"\x00"))
                due = start + (index // step + 1) * FRAME_MS / 1000 - LEAD_SECONDS
                delay = due - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            self.playing = not self.queue.empty()
            self.playing_until = time.monotonic()
            if not self.playing:
                await self.transport.send_event({"type": "idle"})  # finished talking: the app shows "listening"


SETTINGS_PATH = "/v1/voice/settings"
SETTINGS_OPS = {  # app request -> secretary HTTP call (apps/secretary/secretary/profile.py)
    "profile.get": ("GET", "/v1/profile"),
    "profile.put": ("PUT", "/v1/profile"),
    "memory.add": ("POST", "/v1/memories"),
    "memory.delete": ("DELETE", "/v1/memories/{id}"),
    "notify.add": ("POST", "/v1/notifications"),  # the app's "알림 테스트": a real notification, end to end
}


async def settings_request(client: httpx.AsyncClient, base_url: str, message: str) -> dict:
    """One request from the app's "비서에게 알려 주기" screen, relayed to the secretary.
    Only these operations pass; the device token never reaches the secretary, and the secretary
    key never reaches the phone."""
    try:
        request = json.loads(message)
    except ValueError:
        return {"ok": False, "error": "bad json"}
    request_id = request.get("id")
    op = SETTINGS_OPS.get(str(request.get("op", "")))
    if not op:
        return {"id": request_id, "ok": False, "error": "unknown op"}
    method, path = op
    memory_id = re.sub(r"[^0-9a-f]", "", str(request.get("memory", "")))[:32]
    if "{id}" in path and not memory_id:
        return {"id": request_id, "ok": False, "error": "memory id required"}
    body = {key: request[key] for key in ("about", "usage", "mcp", "text", "brain", "goal_brain", "title", "when", "alarm")
            if key in request}
    try:
        response = await client.request(method, base_url + path.format(id=memory_id),
                                        json=body if method in {"PUT", "POST"} else None)
    except httpx.HTTPError:
        return {"id": request_id, "ok": False, "error": "secretary unreachable"}
    if response.status_code >= 400:
        return {"id": request_id, "ok": False, "error": f"secretary said {response.status_code}"}
    return {"id": request_id, "ok": True, "data": response.json()}


NOTIFY_PATH = "/v1/voice/notify"


class NotifyRelay:
    """Secretary notifications -> every phone listening on /v1/voice/notify.

    The phone keeps this socket open in the background (its own foreground service, no FCM), shows each
    notification, rings, and for an alarm wakes the screen; then it sends {"type": "ack", "id"}. Until a
    phone acknowledges one, the relay keeps it and sends it again to the next phone that connects, so a
    notification due while the phone was out of coverage arrives when it is back."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url
        self.phones: set[Any] = set()
        self.unacked: dict[str, dict] = {}

    async def deliver(self, event: dict) -> None:
        if event.get("type") != "notify" or not event.get("id"):
            return
        self.unacked[str(event["id"])] = event
        for phone in list(self.phones):
            try:
                await phone.send(json.dumps(event, ensure_ascii=False))
            except Exception:  # noqa: BLE001 - a dropped phone reconnects and gets it again
                self.phones.discard(phone)
        logger.info("notification %s to %d phone(s)%s", event["id"], len(self.phones), " (alarm)" if event.get("alarm") else "")

    async def ack(self, notification_id: str) -> None:
        if self.unacked.pop(notification_id, None) is None:
            return
        try:
            await self.client.post(f"{self.base_url}/v1/notifications/{notification_id}/ack")
        except httpx.HTTPError:
            logger.warning("could not acknowledge notification %s to the secretary", notification_id)

    async def phone(self, websocket) -> None:
        self.phones.add(websocket)
        try:
            await websocket.send(json.dumps({"type": "ready"}))
            for event in list(self.unacked.values()):
                await websocket.send(json.dumps(event, ensure_ascii=False))
            async for message in websocket:
                try:
                    request = json.loads(message)
                except ValueError:
                    continue
                if isinstance(request, dict) and request.get("type") == "ack":
                    await self.ack(re.sub(r"[^0-9a-f]", "", str(request.get("id", "")))[:32])
        finally:
            self.phones.discard(websocket)

    async def follow(self) -> None:
        """Listen to the secretary's notification stream for as long as the gateway runs."""
        delay = 1.0
        while True:
            try:
                async with self.client.stream("GET", f"{self.base_url}/v1/notifications/stream",
                                              timeout=httpx.Timeout(10.0, read=60.0)) as response:
                    if response.status_code != 200:
                        raise httpx.HTTPError(f"secretary said {response.status_code}")
                    delay = 1.0
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                await self.deliver(json.loads(line[6:]))
                            except ValueError:
                                continue
            except (httpx.HTTPError, OSError) as error:
                logger.debug("notification stream: %s", error)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


def token_allowed(header: str | None, tokens: tuple[str, ...]) -> bool:
    supplied = (header or "").removeprefix("Bearer ").strip().encode()
    return bool(supplied) and any(hmac.compare_digest(supplied, token.encode()) for token in tokens)


async def serve(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    if not settings.secretary_url:
        raise SystemExit("SECRETARY_URL is required")
    speech = HttpSpeech(settings)
    voice_filter = VoiceFilter(settings.denoise_model)
    logger.info("voice filter: %s, denoiser %s", settings.voice_filter, "on" if voice_filter.denoising else "missing")

    def new_call(transport: Any) -> Call:
        return Call(transport, speech=speech, secretary=HttpSecretary(settings), vad=SileroVad(settings.vad_model),
                    settings=settings, voice_filter=voice_filter)

    async def audiosocket(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await new_call(AudioSocketTransport(reader, writer)).run()

    host, _, port = os.getenv("GATEWAY_LISTEN", "0.0.0.0:9092").rpartition(":")
    servers = [await asyncio.start_server(audiosocket, host or "0.0.0.0", int(port))]
    logger.info("AudioSocket (SIP calls) on %s:%s", host or "0.0.0.0", port)

    ws_listen = os.getenv("GATEWAY_WS_LISTEN", "")
    if ws_listen:
        if not settings.app_tokens:
            raise SystemExit("GATEWAY_WS_LISTEN needs GATEWAY_APP_TOKENS (one or more tokens of 24+ characters)")
        from websockets.asyncio.server import serve as ws_serve

        def check(connection, request):
            if request.path.split("?")[0] not in {"/v1/voice/call", SETTINGS_PATH, NOTIFY_PATH}:
                return connection.respond(404, "not found\n")
            if not token_allowed(request.headers.get("Authorization"), settings.app_tokens):
                return connection.respond(401, "invalid token\n")
            return None

        settings_client = httpx.AsyncClient(timeout=15.0,
                                            headers={"Authorization": f"Bearer {settings.secretary_key}"})

        relay = NotifyRelay(settings_client, settings.secretary_url)
        relay_task = asyncio.create_task(relay.follow())  # noqa: RUF006 - runs as long as the gateway

        async def app_call(websocket) -> None:
            if websocket.request.path.split("?")[0] == NOTIFY_PATH:
                await relay.phone(websocket)
                return
            if websocket.request.path.split("?")[0] == SETTINGS_PATH:
                async for message in websocket:  # the app's settings screen: request in, answer out
                    await websocket.send(json.dumps(
                        await settings_request(settings_client, settings.secretary_url, str(message)), ensure_ascii=False))
                return
            await new_call(WebSocketTransport(websocket)).run()

        ws_host, _, ws_port = ws_listen.rpartition(":")
        servers.append(await ws_serve(app_call, ws_host or "0.0.0.0", int(ws_port), process_request=check,
                                      max_size=1 << 16, ping_interval=20))
        logger.info("WebSocket (voice app) on %s/v1/voice/call, %s and %s", ws_listen, SETTINGS_PATH, NOTIFY_PATH)
    await asyncio.gather(*(server.serve_forever() for server in servers))


if __name__ == "__main__":
    asyncio.run(serve())
