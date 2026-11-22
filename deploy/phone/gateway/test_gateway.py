"""Phone gateway tests: a fake Asterisk connects over a real socket and speaks AudioSocket.
Speech, the secretary and the VAD are fakes, so no models, network or Claude are needed."""

from __future__ import annotations

import asyncio
import uuid

import numpy as np
import pytest

import gateway
from audio import (FRAME_BYTES, KIND_AUDIO, KIND_HANGUP, KIND_UUID, PHONE_RATE, SentenceChunker, frame, from_wav,
                   read_frame, resample, speakable, to_wav)

LOUD = (np.full(160, 0.3, dtype=np.float32) * 32767).astype("<i2").tobytes()   # 20 ms of "speech"
QUIET = b"\x00" * FRAME_BYTES


class FakeVad:
    """Loud input is speech; 10 quiet frames after speech end the utterance."""

    def __init__(self) -> None:
        self.buffer: list[np.ndarray] = []
        self.quiet = 0

    def push(self, samples: np.ndarray):
        loud = float(np.sqrt(np.mean(samples ** 2))) > 0.05
        if loud:
            self.buffer.append(samples)
            self.quiet = 0
            return True, []
        if self.buffer:
            self.quiet += 1
            if self.quiet >= 10:
                utterance, self.buffer = np.concatenate(self.buffer), []
                return False, [utterance]
        return False, []


class FakeSpeech:
    def __init__(self, heard: list[str]) -> None:
        self.heard = list(heard)
        self.spoken: list[str] = []

    async def transcribe(self, samples, rate) -> str:
        assert rate == gateway.VAD_RATE and len(samples) > 0
        return self.heard.pop(0) if self.heard else ""

    async def synthesize(self, text: str) -> np.ndarray:
        self.spoken.append(text)
        return np.full(PHONE_RATE // 2 * max(1, len(text) // 10), 0.2, dtype=np.float32)  # longer text, longer audio


class FakeSecretary:
    def __init__(self, replies: dict[str, list[dict]]) -> None:
        self.replies = replies
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.turns: list[str] = []
        self.interrupted = 0
        self.closed = False

    async def start(self) -> None:
        pass

    async def turn(self, text: str) -> str:
        self.turns.append(text)
        for event in self.replies.get(text, [{"type": "text", "delta": f"{text}에 답할게요."}, {"type": "done"}]):
            self.queue.put_nowait(event)
        return "answer"

    async def interrupt(self) -> None:
        self.interrupted += 1
        self.queue.put_nowait({"type": "done"})

    async def close(self) -> None:
        self.closed = True

    async def events(self):
        while True:
            yield await self.queue.get()


async def start_call(speech, secretary):
    calls: list[gateway.Call] = []

    async def handle(reader, writer):
        call = gateway.Call(reader, writer, speech=speech, secretary=secretary, vad=FakeVad(), settings=gateway.Settings(secretary_url="x"))
        calls.append(call)
        await call.run()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(frame(KIND_UUID, uuid.uuid4().bytes))
    await writer.drain()
    received: list[bytes] = []

    async def collect():
        try:
            while True:
                kind, payload = await read_frame(reader)
                if kind == KIND_AUDIO:
                    received.append(payload)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    collector = asyncio.create_task(collect())
    await asyncio.sleep(0.05)
    return server, writer, received, collector, calls


async def send(writer, payload: bytes, count: int) -> None:
    for _ in range(count):
        writer.write(frame(KIND_AUDIO, payload))
    await writer.drain()


async def wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


def test_call_greets_hears_answers_and_hangs_up() -> None:
    async def run() -> None:
        speech, secretary = FakeSpeech(["열린 PR 알려줘"]), FakeSecretary({})
        server, writer, received, collector, calls = await start_call(speech, secretary)
        await wait_for(lambda: speech.spoken[:1] == ["네, 비서입니다. 말씀하세요."])
        await send(writer, LOUD, 20)
        await send(writer, QUIET, 12)
        await wait_for(lambda: secretary.turns == ["열린 PR 알려줘"])
        await wait_for(lambda: "열린 PR 알려줘에 답할게요." in speech.spoken)
        await wait_for(lambda: len(received) > 20)
        assert all(len(payload) == FRAME_BYTES for payload in received)
        writer.write(frame(KIND_HANGUP))
        await writer.drain()
        await wait_for(lambda: secretary.closed)
        collector.cancel()
        server.close()
    asyncio.run(run())


def test_speaking_over_the_answer_interrupts_it() -> None:
    async def run() -> None:
        long_answer = [{"type": "text", "delta": "첫 문장은 아주 길게 이어지는 설명입니다. " * 6}, {"type": "done"}]
        speech, secretary = FakeSpeech(["길게 설명해"]), FakeSecretary({"길게 설명해": long_answer})
        server, writer, received, collector, calls = await start_call(speech, secretary)
        await send(writer, LOUD, 20)
        await send(writer, QUIET, 12)
        await wait_for(lambda: calls[0].playing and len(received) > 60)
        before = calls[0].generation
        await send(writer, LOUD, 8)  # the caller talks over the answer
        await wait_for(lambda: calls[0].generation > before)
        await asyncio.sleep(0.2)
        settled = len(received)
        await asyncio.sleep(0.3)
        assert len(received) - settled < 5, "playback stopped after the interruption"
        collector.cancel()
        server.close()
    asyncio.run(run())


def test_stop_word_interrupts_the_agent() -> None:
    async def run() -> None:
        never_done = [{"type": "status", "text": "확인하고 있어요."}]
        speech, secretary = FakeSpeech(["메일 정리해", "멈춰"]), FakeSecretary({"메일 정리해": never_done})
        server, writer, received, collector, calls = await start_call(speech, secretary)
        for _ in range(2):
            await send(writer, LOUD, 20)
            await send(writer, QUIET, 12)
            await asyncio.sleep(0.1)
        await wait_for(lambda: secretary.interrupted >= 1)
        assert secretary.turns == ["메일 정리해"]  # "멈춰" was not sent to the agent as a request
        collector.cancel()
        server.close()
    asyncio.run(run())


def test_confirmation_prompt_is_spoken() -> None:
    async def run() -> None:
        ask = [{"type": "text", "delta": "빌더에게 맡길게요."}, {"type": "confirm", "id": "1", "prompt": "builder@mini에게 이 작업을 보낼까요?"}]
        speech, secretary = FakeSpeech(["버그 고치라고 해"]), FakeSecretary({"버그 고치라고 해": ask})
        server, writer, received, collector, calls = await start_call(speech, secretary)
        await send(writer, LOUD, 20)
        await send(writer, QUIET, 12)
        await wait_for(lambda: "builder@mini에게 이 작업을 보낼까요?" in speech.spoken)
        assert speech.spoken.index("빌더에게 맡길게요.") < speech.spoken.index("builder@mini에게 이 작업을 보낼까요?")
        assert calls[0].agent_busy is False  # waiting for the caller's answer, so speaking now is not a barge-in
        collector.cancel()
        server.close()
    asyncio.run(run())


def test_audio_helpers() -> None:
    tone = np.sin(np.arange(44100) / 10).astype(np.float32) * 0.5
    down = resample(tone, 44100, PHONE_RATE)
    assert len(down) == PHONE_RATE and float(np.max(np.abs(down))) <= 0.5
    samples, rate = from_wav(to_wav(down, PHONE_RATE))
    assert rate == PHONE_RATE and np.allclose(samples, down, atol=1e-3)
    chunker = SentenceChunker()
    out = [c for token in ["회의실 예약은 ", "사내 포털에서 ", "하시면 되고, ", "하루 전까지 ", "신청해야 합니다. "] for c in chunker.push(token)]
    assert out == ["회의실 예약은 사내 포털에서 하시면 되고,", "하루 전까지 신청해야 합니다."]
    assert speakable("**PR #84**는 https://github.com/baryonlabs/baryon-ax/pull/84 에 있어요 [1].") == "PR 84는 github.com 에 있어요."


@pytest.mark.parametrize("text,stop", [("멈춰", True), ("그만.", True), ("멈춰서 생각해 보면", False), ("Stop", True)])
def test_stop_words(text: str, stop: bool) -> None:
    assert bool(gateway.STOP_WORDS.match(text)) is stop


class WindowedSherpaVad:
    """Stands in for sherpa-onnx: one decision per 512-sample window, like Silero."""

    def __init__(self) -> None:
        self.detected = False
        self.queue: list = []

    def accept_waveform(self, window) -> None:
        self.detected = float(np.sqrt(np.mean(np.asarray(window) ** 2))) > 0.05

    def is_speech_detected(self) -> bool:
        return self.detected

    def empty(self) -> bool:
        return not self.queue


def test_silero_wrapper_keeps_speaking_between_windows() -> None:
    """20 ms phone frames (320 samples at 16 kHz) are shorter than a VAD window. Frames that run
    no window must still report speech, or barge-in never accumulates (the bug found on a real call)."""
    vad = gateway.SileroVad.__new__(gateway.SileroVad)
    vad.vad, vad.pending, vad.speaking = WindowedSherpaVad(), np.zeros(0, dtype=np.float32), False
    loud = resample(np.full(160, 0.3, dtype=np.float32), PHONE_RATE, gateway.VAD_RATE)
    flags = [vad.push(loud)[0] for _ in range(10)]
    assert flags[0] is False and all(flags[2:]), flags  # once speech is seen it stays on for every frame
    quiet = np.zeros(320, dtype=np.float32)
    assert [vad.push(quiet)[0] for _ in range(4)][-1] is False


def test_new_utterance_cuts_off_the_current_answer() -> None:
    async def run() -> None:
        long_answer = [{"type": "text", "delta": "아주 길게 이어지는 첫 번째 답변 문장입니다. " * 6}, {"type": "done"}]
        speech, secretary = FakeSpeech(["길게 설명해", "메일 알려줘"]), FakeSecretary({"길게 설명해": long_answer})
        server, writer, received, collector, calls = await start_call(speech, secretary)
        await send(writer, LOUD, 20)
        await send(writer, QUIET, 12)
        await wait_for(lambda: calls[0].playing)
        before = calls[0].generation
        await send(writer, LOUD, 20)   # the caller asks something else over the answer
        await send(writer, QUIET, 12)
        await wait_for(lambda: secretary.turns == ["길게 설명해", "메일 알려줘"])
        assert calls[0].generation > before
        await wait_for(lambda: "메일 알려줘에 답할게요." in speech.spoken)
        collector.cancel()
        server.close()
    asyncio.run(run())
