"""Phone gateway tests: a fake Asterisk connects over a real socket and speaks AudioSocket.
Speech, the secretary and the VAD are fakes, so no models, network or Claude are needed."""

from __future__ import annotations

import asyncio
import json
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
        self.voices: list = []

    async def transcribe(self, samples, rate) -> str:
        assert rate == gateway.VAD_RATE and len(samples) > 0
        return self.heard.pop(0) if self.heard else ""

    async def synthesize(self, text: str, voice=None, speed=None):
        self.spoken.append(text)
        self.voices.append((voice, speed))
        rate = 24000  # like Supertonic: the call resamples to its own rate
        return np.full(rate // 2 * max(1, len(text) // 10), 0.2, dtype=np.float32), rate  # longer text, longer audio


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
        call = gateway.Call(gateway.AudioSocketTransport(reader, writer), speech=speech, secretary=secretary, vad=FakeVad(),
                            settings=gateway.Settings(secretary_url="x"))
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
        await send(writer, LOUD, 20)  # the caller talks over the answer (400 ms)
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


def test_echo_of_its_own_words_is_not_a_request() -> None:
    async def run() -> None:
        long_answer = [{"type": "text", "delta": "지금 열려 있는 PR은 하나도 없어요. " * 4}, {"type": "done"}]
        speech = FakeSpeech(["PR 알려줘", "열려 있는 PR은 하나도"])  # the second is the phone echoing the answer
        secretary = FakeSecretary({"PR 알려줘": long_answer})
        server, writer, received, collector, calls = await start_call(speech, secretary)
        await send(writer, LOUD, 20)
        await send(writer, QUIET, 12)
        await wait_for(lambda: calls[0].playing and any("열려 있는 PR" in said for _, said in calls[0].recent_said))
        await send(writer, LOUD, 10)  # 200 ms burst: below the barge-in threshold
        await send(writer, QUIET, 12)
        await asyncio.sleep(0.3)
        assert secretary.turns == ["PR 알려줘"], "echo was not sent to the agent"
        collector.cancel()
        server.close()
    asyncio.run(run())


@pytest.mark.parametrize("text,phantom", [
    ("다음 영상에서 만나요.", True),
    ("시청해 주셔서 감사합니다", True),
    ("구독과 좋아요 부탁드립니다", True),
    ("다음 영상 만들 때 쓸 일정 알려줘", False),
    ("오늘 일정 알려줘", False),
])
def test_phantom_transcripts_are_recognised(text: str, phantom: bool) -> None:
    assert bool(gateway.PHANTOM_PHRASES.match(text.strip())) is phantom


@pytest.mark.parametrize("a,b,expected", [
    ("열려 있는 PR은 하나도", "지금 열려 있는 PR은 하나도 없어요.", True),
    ("메일 알려줘", "지금 열려 있는 PR은 하나도 없어요.", False),
    ("네", "네, 비서입니다. 말씀하세요.", False),
])
def test_echo_similarity(a: str, b: str, expected: bool) -> None:
    assert (gateway.similar(a, b) >= gateway.ECHO_SIMILARITY) is expected


def test_app_websocket_call_streams_16k_audio_and_events() -> None:
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve as ws_serve

    async def run() -> None:
        speech, secretary = FakeSpeech(["할 일 알려줘"]), FakeSecretary({})
        tokens = ("t" * 32,)

        def check(connection, request):
            if not gateway.token_allowed(request.headers.get("Authorization"), tokens):
                return connection.respond(401, "invalid token\n")
            return None

        async def app_call(websocket) -> None:
            await gateway.Call(gateway.WebSocketTransport(websocket), speech=speech, secretary=secretary, vad=FakeVad(),
                               settings=gateway.Settings(secretary_url="x")).run()

        async with ws_serve(app_call, "127.0.0.1", 0, process_request=check) as server:
            port = server.sockets[0].getsockname()[1]
            with pytest.raises(Exception):
                async with connect(f"ws://127.0.0.1:{port}/v1/voice/call", additional_headers={"Authorization": "Bearer nope"}):
                    pass
            async with connect(f"ws://127.0.0.1:{port}/v1/voice/call", additional_headers={"Authorization": f"Bearer {tokens[0]}"}) as ws:
                loud16 = (np.full(320, 0.3, dtype=np.float32) * 32767).astype("<i2").tobytes()   # 20 ms at 16 kHz
                for _ in range(20):
                    await ws.send(loud16)
                for _ in range(12):
                    await ws.send(b"\x00" * 640)
                audio, events = 0, []
                while not any(e.get("type") == "said" and "할 일 알려줘에" in e.get("text", "") for e in events):
                    message = await asyncio.wait_for(ws.recv(), 3)
                    if isinstance(message, bytes):
                        assert len(message) == 640  # 20 ms frames at 16 kHz
                        audio += 1
                    else:
                        events.append(__import__("json").loads(message))
                assert {"type": "heard", "text": "할 일 알려줘"} in events and audio > 0
                await ws.send('{"type": "text", "text": "응"}')  # tapped answer on the screen
                await wait_for(lambda: secretary.turns[-1:] == ["응"])
                await ws.send('{"type": "hangup"}')
            await wait_for(lambda: secretary.closed)
    asyncio.run(run())


def test_typed_session_dictation_and_progress_events() -> None:
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve as ws_serve

    async def run() -> None:
        reply = [{"type": "activity", "id": "a1", "tool": "Bash", "label": "깃허브를 확인하고 있어요."},
                 {"type": "text", "delta": "PR은 없어요."}, {"type": "activity_done", "id": "a1", "error": False},
                 {"type": "task", "id": "t1", "to": "builder@mini", "summary": "버그 수정", "status": "sent"},
                 {"type": "done"}]
        speech, secretary = FakeSpeech(["메일 정리해줘"]), FakeSecretary({"PR 알려줘": reply})

        async def app_call(websocket) -> None:
            await gateway.Call(gateway.WebSocketTransport(websocket), speech=speech, secretary=secretary, vad=FakeVad(),
                               settings=gateway.Settings(secretary_url="x")).run()

        async with ws_serve(app_call, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}/v1/voice/call?audio=0") as ws:
                events: list[dict] = []

                async def until(predicate):
                    while not any(predicate(e) for e in events):
                        message = await asyncio.wait_for(ws.recv(), 3)
                        assert not isinstance(message, bytes), "a typed session gets no audio"
                        events.append(json.loads(message))

                await ws.send('{"type": "text", "text": "PR 알려줘"}')
                await until(lambda e: e["type"] == "agent_done")
                await until(lambda e: e["type"] == "said" and e["text"] == "PR은 없어요.")  # last sentence flushes at done
                types = [e["type"] for e in events]
                assert {"activity", "agent_text", "activity_done", "task"} <= set(types)
                assert any(e["type"] == "said" and e["text"] == "PR은 없어요." for e in events)
                assert speech.spoken == []  # nothing was synthesized

                # dictation: audio becomes text for the input box, and is not sent to the secretary
                await ws.send('{"type": "dictate", "on": true}')
                loud16 = (np.full(320, 0.3, dtype=np.float32) * 32767).astype("<i2").tobytes()
                for _ in range(25):
                    await ws.send(loud16)
                await ws.send('{"type": "dictate", "on": false}')
                await until(lambda e: e["type"] == "dictation")
                assert next(e for e in events if e["type"] == "dictation")["text"] == "메일 정리해줘"
                assert secretary.turns == ["PR 알려줘"]
    asyncio.run(run())


def test_earcons_mark_heard_question_and_interruption() -> None:
    import earcons

    async def run() -> None:
        ask = [{"type": "confirm", "id": "1", "prompt": "보낼까요?"}]
        speech, secretary = FakeSpeech(["보내줘"]), FakeSecretary({"보내줘": ask})
        server, writer, received, collector, calls = await start_call(speech, secretary)
        cued: list[str] = []
        original = calls[0].cue
        calls[0].cue = lambda name: (cued.append(name), original(name))
        await send(writer, LOUD, 20)
        await send(writer, QUIET, 12)
        await wait_for(lambda: "question" in cued)
        assert cued[:2] == ["interrupted", "heard"] or cued[0] == "heard"  # the greeting may be cut off first
        assert "question" in cued
        collector.cancel()
        server.close()
    asyncio.run(run())
    for name in earcons.PATTERNS:
        sound = earcons.earcon(name, 8000)
        assert 0.05 < len(sound) / 8000 < 0.5 and float(np.max(np.abs(sound))) <= 0.25  # short and quiet


def test_thinking_ticks_only_while_working_in_silence() -> None:
    async def run() -> None:
        never_done = [{"type": "activity", "id": "a", "tool": "Bash", "label": "확인"}]
        speech, secretary = FakeSpeech(["오래 걸리는 일"]), FakeSecretary({"오래 걸리는 일": never_done})
        gateway.THINKING_TICK_SECONDS = 0.6
        try:
            server, writer, received, collector, calls = await start_call(speech, secretary)
            cued: list[str] = []
            original = calls[0].cue
            calls[0].cue = lambda name: (cued.append(name), original(name))
            await send(writer, LOUD, 20)
            await send(writer, QUIET, 12)
            await wait_for(lambda: cued.count("thinking") >= 2, timeout=5)
            collector.cancel()
            server.close()
        finally:
            gateway.THINKING_TICK_SECONDS = 2.5
    asyncio.run(run())


def test_quiet_noise_and_fragments_never_reach_the_secretary() -> None:
    """From a real call: room noise passed the VAD and Whisper turned it into "다음 영상에서 만나요"
    61 times in 10 minutes; "아" and "자" interrupted the secretary. None of it is a request."""
    async def run() -> None:
        faint = (np.full(160, 0.06, dtype=np.float32) * 32767).astype("<i2").tobytes()  # VAD says speech, peak 0.06
        speech, secretary = FakeSpeech(["자", "메일 알려줘"]), FakeSecretary({})
        server, writer, received, collector, calls = await start_call(speech, secretary)
        await send(writer, faint, 20)
        await send(writer, QUIET, 12)
        await asyncio.sleep(0.1)
        assert speech.heard == ["자", "메일 알려줘"], "the quiet segment never went to STT"
        for _ in range(2):  # "자", then a real request
            await send(writer, LOUD, 20)
            await send(writer, QUIET, 12)
            await asyncio.sleep(0.1)
        await wait_for(lambda: secretary.turns == ["메일 알려줘"])
        collector.cancel()
        server.close()
    asyncio.run(run())


def test_one_syllable_answer_counts_when_a_question_is_open() -> None:
    async def run() -> None:
        ask = [{"type": "confirm", "id": "1", "prompt": "보낼까요?"}]
        speech, secretary = FakeSpeech(["보내줘", "응"]), FakeSecretary({"보내줘": ask})
        server, writer, received, collector, calls = await start_call(speech, secretary)
        for _ in range(2):
            await send(writer, LOUD, 20)
            await send(writer, QUIET, 12)
            await asyncio.sleep(0.15)
        await wait_for(lambda: secretary.turns == ["보내줘", "응"])
        collector.cancel()
        server.close()
    asyncio.run(run())


def test_app_prefs_choose_voice_speed_and_silence_earcons() -> None:
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve as ws_serve

    async def run() -> None:
        speech, secretary = FakeSpeech(["할 일 알려줘"]), FakeSecretary({})
        calls: list = []

        async def app_call(websocket) -> None:
            call = gateway.Call(gateway.WebSocketTransport(websocket), speech=speech, secretary=secretary, vad=FakeVad(),
                                settings=gateway.Settings(secretary_url="x"))
            calls.append(call)
            await call.run()

        async with ws_serve(app_call, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}/v1/voice/call") as ws:
                await ws.send('{"type": "prefs", "earcons": false, "voice": 4, "speed": 1.2}')
                await wait_for(lambda: calls and calls[0].voice == 4)
                cued: list = []
                original = calls[0].cue
                calls[0].cue = lambda name: (cued.append(name), original(name))
                await ws.send('{"type": "text", "text": "메일 알려줘"}')
                await wait_for(lambda: "메일 알려줘에 답할게요." in speech.spoken)
                assert speech.voices[-1] == (4, 1.2)
                assert calls[0].earcons_on is False
    asyncio.run(run())


# --- voice filter: the caller's voice only -------------------------------------------------

import os  # noqa: E402

from voicefilter import Verdict, VoiceFilter  # noqa: E402


def tone(level: float, seconds: float = 1.0, rate: int = 16000) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * 220 * t) * level * np.sqrt(2)).astype(np.float32)  # RMS == level


def test_voice_filter_keeps_the_near_voice_and_drops_noise_and_distant_voices() -> None:
    # a stand-in denoiser: "noise" (a 3 kHz hiss) is removed, voice (220 Hz) is kept
    def denoise(samples: np.ndarray, rate: int) -> np.ndarray:
        spectrum = np.fft.rfft(samples)
        spectrum[np.fft.rfftfreq(len(samples), 1 / rate) > 1000] = 0
        return np.fft.irfft(spectrum, len(samples)).astype(np.float32)

    voice_filter = VoiceFilter(denoise=denoise)
    t = np.arange(16000) / 16000
    hiss = (np.sin(2 * np.pi * 3000 * t) * 0.05).astype(np.float32)
    near, distant = tone(0.07), tone(0.012)
    assert voice_filter.check(near, 16000).keep
    assert voice_filter.check(hiss, 16000).reason == "noise"
    assert voice_filter.check(distant, 16000, "strict").reason == "distant"
    assert voice_filter.check(distant, 16000, "normal").keep          # "normal" lets other clear voices in
    assert voice_filter.check(hiss, 16000, "off").keep                 # "off": the plain level gate only
    assert voice_filter.check(near + hiss, 16000).keep                 # speech over noise survives
    cleaned = voice_filter.check(near + hiss, 16000).samples
    assert abs(float(np.sqrt(np.mean(cleaned ** 2))) - 0.07) < 0.005   # and STT hears it without the hiss


@pytest.mark.skipif(not os.path.exists(os.getenv("DENOISE_MODEL", "/models/gtcrn_simple.onnx")),
                    reason="GTCRN model not downloaded")
def test_real_denoiser_removes_white_noise() -> None:
    voice_filter = VoiceFilter(os.getenv("DENOISE_MODEL", "/models/gtcrn_simple.onnx"))
    noise = np.random.default_rng(0).normal(0, 0.02, 32000).astype(np.float32)
    assert not voice_filter.check(noise, 16000).keep


def test_a_neighbours_voice_never_reaches_the_secretary_and_the_app_can_relax_the_filter() -> None:
    class Neighbour:
        """The first utterance is someone talking nearby; the filter hands STT the cleaned audio."""
        def __init__(self) -> None:
            self.modes: list[str] = []

        def check(self, samples, rate, mode):
            self.modes.append(mode)
            if len(self.modes) == 1:
                return Verdict(False, samples, "distant", 0.012, 0.9)
            return Verdict(True, samples * 0.5, "", 0.15, 0.98)

    async def run() -> None:
        speech, secretary = FakeSpeech(["메일 알려줘"]), FakeSecretary({})
        server, writer, received, collector, calls = await start_call(speech, secretary)
        await wait_for(lambda: calls)
        neighbour = calls[0].voice_filter = Neighbour()
        for _ in range(2):
            await send(writer, LOUD, 20)
            await send(writer, QUIET, 12)
            await asyncio.sleep(0.1)
        await wait_for(lambda: secretary.turns == ["메일 알려줘"])
        assert speech.heard == [] and neighbour.modes == ["strict", "strict"]
        calls[0]._apply_prefs({"filter": "normal"})
        assert calls[0].filter_mode == "normal"
        calls[0]._apply_prefs({"filter": "bogus"})
        assert calls[0].filter_mode == "normal"
        collector.cancel()
        server.close()
    asyncio.run(run())


def test_settings_requests_are_relayed_to_the_secretary_and_nothing_else() -> None:
    import httpx

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content.decode() or None))
        if request.url.path == "/v1/profile":
            return httpx.Response(200, json={"about": "나", "memories": [], "mcp": []})
        if request.method == "DELETE":
            return httpx.Response(404, json={"detail": "no such memory"})
        return httpx.Response(200, json={"id": "abc123", "text": "기억"})

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        relay = lambda message: gateway.settings_request(client, "http://secretary", message)
        assert (await relay('{"id": 1, "op": "profile.get"}')) == {"id": 1, "ok": True,
                                                                   "data": {"about": "나", "memories": [], "mcp": []}}
        await relay('{"id": 2, "op": "profile.put", "about": "새 소개", "mcp": ["kb"], "token": "x"}')
        assert seen[-1] == ("PUT", "/v1/profile", '{"about":"새 소개","mcp":["kb"]}')  # only known fields pass
        assert (await relay('{"id": 3, "op": "memory.add", "text": "기억"}'))["data"]["id"] == "abc123"
        assert (await relay('{"id": 4, "op": "memory.delete", "memory": "../../v1/sessions"}'))["ok"] is False
        assert seen[-1][1] == "/v1/memories/1e"  # "../../v1/sessions" reduced to its hex letters: no path tricks
        assert (await relay('{"id": 5, "op": "sessions.delete"}')) == {"id": 5, "ok": False, "error": "unknown op"}
        assert (await relay("not json"))["ok"] is False
    asyncio.run(run())
