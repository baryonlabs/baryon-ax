# Voice dictation and voice commands

Users can speak into the chat composer instead of typing. Speech is transcribed on
your own servers by an open-source engine. No audio goes to a cloud service.

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

What we measured with synthetic Korean speech:

- **Whisper turbo** produced correct sentences, e.g. "연차 2월 기준이 뭐예요?".
- **SenseVoice** recognized short commands well but its spacing was rough.
- The 2025 SenseVoice export decoded Korean as Cantonese, so the catalog pins the 2024-07-17 build.

The sidecar applies these fixes for you:

- The API always passes the language from the UI locale, because Whisper's auto-detect mislabels short Korean as Japanese.
- Whisper gets 0.5 s of trailing silence and a repetition guard.
- SenseVoice runs without inverse text normalization, which otherwise turned "새 대화" into "3 대화".

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
- Requests are rate-limited per user, uploads are capped by `STT_MAX_UPLOAD_BYTES` (8 MB), and the sidecar rejects audio longer than `STT_MAX_SECONDS`.
- The sidecar requires `STT_API_KEY` and has no published port in the compose profile.

## Configuration

| Variable | Where | Default |
| --- | --- | --- |
| `STT_BASE_URL` | api | empty (voice off) |
| `STT_API_KEY` | api, stt | required |
| `STT_DEFAULT_MODEL` | api, stt | `sensevoice-ko` |
| `STT_MAX_UPLOAD_BYTES` | api | `8388608` |
| `STT_THREADS` / `STT_MAX_SECONDS` | stt | `4` / `120` |
