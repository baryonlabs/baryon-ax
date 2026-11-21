# Voice: dictation, voice commands and live conversation

Users can speak into the chat composer instead of typing, or hold a **live spoken
conversation**: ask by voice, hear the answer, and interrupt it by speaking. Speech
recognition and speech synthesis run on your own servers with open-source models.
No audio goes to a cloud service.

```
browser (MediaRecorder → 16 kHz mono WAV)
   → web (nginx /api/v1/audio/)
   → api  POST /v1/audio/transcriptions   (auth, rate limit, audit, voice commands)
   → stt  POST /v1/audio/transcriptions   (deploy/stt, sherpa-onnx, CPU)
```

## Turn it on

```bash
echo "STT_API_KEY=$(openssl rand -hex 24)" >> .env
docker compose -f docker-compose.yml -f docker-compose.voice.yml up -d --build
```

Then open **🎙 → Voice settings** in the composer. An operator (`admin`, or
`platform-admin` in pooled mode) installs a model with **Download**. Members can
pick among installed models but cannot install them.

The browser only allows the microphone on HTTPS or `localhost`. Serve the web app
over TLS for other hosts.

## Models

All models run offline on CPU through [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
(Apache-2.0). They download from the sherpa-onnx GitHub releases into the
`stt-models` volume. For an air-gapped site, copy the extracted folders into that
volume instead.

| Model | Size | Languages | Notes |
| --- | --- | --- | --- |
| Whisper Large v3 Turbo (int8) | 537 MB | 99 languages incl. ko | Recommended. Best Korean accuracy, slower on CPU. |
| SenseVoice (int8, 2024-07-17) | 155 MB | ko, en, ja, zh, yue | Default. Fast. Check the FunASR model licence before redistributing. |
| Whisper Tiny | 110 MB | 99 languages | Smallest. Lower accuracy. |
| Parakeet TDT v3 (int8) | 464 MB | 25 European languages | **No Korean.** For English and European teams. |
| Supertonic 3 (int8, speech synthesis) | 145 MB | 31 languages incl. ko | Speaks answers in live conversation. 10 voices, about 0.5 s per sentence on CPU. OpenRAIL-M model licence (use restrictions apply). |

What we measured with synthetic Korean speech:

- **Whisper turbo** produced correct sentences, e.g. "연차 2월 기준이 뭐예요?".
- **SenseVoice** recognized short commands well but its spacing was rough.
- The 2025 SenseVoice export decoded Korean as Cantonese, so the catalog pins the 2024-07-17 build.

The sidecar applies these fixes for you:

- The API always passes the language from the UI locale, because Whisper's auto-detect mislabels short Korean as Japanese.
- Whisper gets 0.5 s of trailing silence and a repetition guard.
- SenseVoice runs without inverse text normalization, which otherwise turned "새 대화" into "3 대화".
- SenseVoice gets one recognizer per language. It ignores a per-stream language setting, and its auto-detect heard "보내 줘" as Japanese.
- Supertonic's language must be set by assigning `GenerationConfig.extra = {"lang": ...}`. Assigning a single item edits a copy and is silently lost.
- Answers are cleaned before synthesis: Markdown, code blocks, links, URLs and citation numbers are removed (`voice.speakable`).

## Live conversation

Press the **≋ (live conversation)** button next to the mic. The panel above the
composer listens continuously:

```
you speak ──VAD detects the end (0.7 s of silence)──▶ STT (SenseVoice, ~0.1 s)
  ──▶ chat with RAG, response_style "voice" (2-3 spoken sentences, no Markdown)
  ──▶ answer split into sentences as it streams ──▶ TTS per sentence (Supertonic 3, ~0.5 s)
  ──▶ played back to back ──▶ listening again
speaking while it talks ──▶ playback stops, generation is cancelled, your new question is heard
```

- **Latency:** measured on an Apple M-series CPU with qwen2.5:7b on Ollama, the first audio started 1.5-1.8 s after the end of speech (plus the 0.7 s pause that marks the end). The first answer after a cold model load takes longer.
- **Interrupting (barge-in):** while the assistant talks, the detector needs louder speech so that its own voice, after browser echo cancellation, does not interrupt it. Earphones work best. Turn off **말하면 끼어들기** in a noisy room.
- **Commands** still work: "멈춰" stops the answer, "새 대화" starts over and "지식 화면 열어" switches views.
- **Choices:** ten voices, speed, and mute. Esc ends the conversation. Answers stay in the chat history with their sources.
- **Model required:** an operator installs **Supertonic 3** under *음성 합성* in Voice settings, in addition to a dictation model. The button stays disabled until both are installed.

The detector is energy-based with an adaptive noise floor (`apps/web/src/live.ts`). The
sentence chunker flushes the first chunk early at a comma so speech starts sooner.

## Dictation modes

| Mode | Behaviour |
| --- | --- |
| Auto (default) | Tap to start, tap to stop. Press and hold records until you let go. |
| Hold | Records only while the button is pressed. |
| Toggle | Press once to start and again to stop. |

The keyboard shortcut is **Alt+V**. A tap toggles recording; holding records until release. A recording is capped at 60 seconds.

## Voice commands

When **Voice commands** is on, an utterance that is *only* a command runs it instead
of being typed:

| Say | Does |
| --- | --- |
| 새 대화 / new chat | Start a new chat |
| 보내 / send | Send what is in the composer |
| 지워 / clear | Clear the composer |
| 멈춰 / stop | Stop the answer being generated |
| 지식 화면 열어 / open knowledge | Open a view: 채팅, 지식, 암묵지, 메모리, 거버넌스, 커넥터, 지식 공백, 설정 |

Politeness endings such as 줘, 주세요 and 해줘 are accepted. Sentences that only contain a
command word stay dictation. For example, "새 대화에서 연차 알려줘" is typed into the composer.
Admin-only views open only for admins. The parser is in `apps/api/src/baryon/voice.py`.

## Privacy and audit

- The API forwards audio to the sidecar in memory, and neither stores it.
- The audit log records `audio.transcribed` with the model, duration, character count and command name only. The transcript and the audio are never logged.
- Speech synthesis (`POST /v1/audio/speech`) reads text the user already received from chat. It is rate-limited per user and is not audited again.
- Requests are rate-limited per user, uploads are capped by `STT_MAX_UPLOAD_BYTES` (8 MB), and the sidecar rejects audio longer than `STT_MAX_SECONDS`.
- The sidecar requires `STT_API_KEY` and has no published port in the compose profile.

## Configuration

| Variable | Where | Default |
| --- | --- | --- |
| `STT_BASE_URL` | api | empty (voice off) |
| `STT_API_KEY` | api, stt | required |
| `STT_DEFAULT_MODEL` | api, stt | `sensevoice-ko` |
| `STT_MAX_UPLOAD_BYTES` | api | `8388608` |
| `TTS_DEFAULT_MODEL` | stt | `supertonic-3` |
| `STT_THREADS` / `STT_MAX_SECONDS` | stt | `4` / `120` |

## Testing

```bash
cd apps/web && npm test                                   # VAD and sentence chunker
E2E_BASE_URL=… VOICE_COMMAND_WAV=… VOICE_DICTATION_WAV=… node e2e/voice.mjs
E2E_BASE_URL=… LIVE_TURN_WAV=… LIVE_BARGEIN_WAV=… node e2e/live.mjs   # fake microphone, real models
```
