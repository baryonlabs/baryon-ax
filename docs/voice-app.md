# 바리온 비서: the Android app

Call your [voice secretary](voice-secretary.md) from your phone. It is a real phone call
as far as Android is concerned, so the car's Bluetooth hands-free, steering-wheel buttons and
car display work, and you need no VPN.

```
apps/voice_app (Flutter UI + native Kotlin call engine)
   ── wss:// 16 kHz PCM + JSON events, device token ──▶ deploy/phone/gateway (WebSocket)
                                                           VAD → STT → secretary → TTS
```

Design rules: [voice-app-design.md](voice-app-design.md). Architecture in UML: [voice-architecture.md](voice-architecture.md).

## What it does

- **One-tap call:** the **비서에게 전화** home-screen shortcut, or the green button. The call is
  a self-managed Telecom call (`CallConnectionService`), so Android routes audio to Bluetooth and shows it
  as a call. A foreground service keeps it going with the screen off.
- **Clean audio:** the microphone uses the `VOICE_COMMUNICATION` source with the platform echo canceller,
  noise suppressor and gain control, at 16 kHz. Plain SIP calls use 8 kHz, and on speaker their echo
  made the secretary cut itself off.
- **Barge-in:** start talking and the secretary stops; the newest request wins. A `flush` event drops the
  audio the app has already buffered.
- **What is happening, on screen and by ear:**
  - The progress card shows the current step, a tick or cross per finished step, and the answer as it forms.
  - Background goal agents appear as task cards.
  - Earcons say the same without a screen: 띠링 (heard), a soft tick (working), 뚝 (stopped), 딩-동? (needs a yes or no), 도미솔 (a goal finished).
- **Typing and dictation:** type a question with or without a call. Without a call the conversation is
  text-only: `?audio=0`, nothing is synthesized. The 🎤 in the box records, the on-prem STT
  transcribes, and the text goes into the box to edit before sending.
- **Confirmations:** "응, 진행해 / 아니" buttons answer the secretary's yes/no questions. Saying it works too.
- **Speaker and route:** a speaker button. The current route (수화부, 스피커, 블루투스, 이어폰) and whether the echo canceller is on are shown.
- **Layout:** the conversation fills the screen. Status is a top-right chip with an ear that moves while it listens, call buttons float, and history (the last 200 messages) stays on the phone.
- **Settings:** theme, text size, steps on or off, earcons, voice 1–10, speaking speed, call on open, keep the screen on, haptics, home-screen shortcut, clear history and server. All are saved on the phone. Earcons, voice, speed and the voice filter are sent to the gateway for each call (`{"type": "prefs", "earcons": true, "voice": 0, "speed": 1.05, "filter": "strict"}`).
- **Noise handling:** quiet segments, one-syllable fragments, phantom phrases and echoes never reach the secretary. The status tooltip shows how many were dropped.
- **비서에게 알려 주기 (Settings, top):** a short introduction of what the secretary does, then four things it keeps on the server and reads at the start of every call:
  - **나에 대해:** who you are (name, role, what you work on).
  - **이렇게 써 줘:** how you want it to work (answer length, tone, what to check first).
  - **기억:** short facts. Saying "이거 기억해 줘" in a call adds one too.
  - **두뇌:** the model for the conversation (빠르게 / 똑똑하게 / 최고) and for goal agents (기본 / 깊게 / 최고). See [voice-secretary.md](voice-secretary.md#what-it-knows-about-you-profile-memory-connections).
  - **연결 (MCP):** which of the MCP servers configured in Claude Code on the workstation it may use. Names only; their commands and credentials stay on the workstation.

  Changes apply from the next call.
- **Your voice only:** Settings → 듣기 → 주변 소리 거르기. 내 목소리만 (default) denoises each utterance and passes only a voice close to the phone, so people talking nearby are not taken as requests. 또렷한 말 passes any clear voice, and 끄기 turns the filter off. Dropped voices are counted as "주변 말소리 N건 거름".

## Server side

The gateway accepts app connections when both of these are set:

```
GATEWAY_WS_LISTEN=0.0.0.0:9093
GATEWAY_APP_TOKENS=<one or more device tokens, 24+ characters, comma-separated>
```

Put HTTPS in front of it, for example a Cloudflare Tunnel or a reverse proxy with a certificate. The app
uses `wss://<host>/v1/voice/call`. It allows plain `ws://` only to `127.0.0.1`, which you reach over USB
with `adb reverse` during development.

## Build and install

```bash
cd apps/voice_app
flutter test
flutter build apk --debug
adb install -r build/app/outputs/flutter-apk/app-debug.apk
```

Point the app at your gateway. Either open the floating gear, choose 서버 설정 and enter the server
URL and device token, or provision it over USB without typing:

```bash
adb shell am start -n ai.baryon.baryon_voice/.MainActivity \
  --es server "wss://voice.example.com/v1/voice/call" --es token "$DEVICE_TOKEN"
```

For development without any network setup:

```bash
adb reverse tcp:9093 tcp:9093   # the app's default server is ws://127.0.0.1:9093/v1/voice/call
```

## Protocol

Messages from the client:

| Message | Meaning |
| --- | --- |
| binary | PCM16LE mono 16 kHz audio |
| `{"type":"text","text":…}` | A typed message or a tapped answer |
| `{"type":"dictate","on":true\|false}` | Dictation into the input box |
| `{"type":"typing"}` | The caller is typing: mic input is not speech |
| `{"type":"hangup"}` | End the call |

Settings channel: `wss://…/v1/voice/settings`, same device token. One JSON request per message, answered with `{"id", "ok", "data" | "error"}`. The gateway relays only these to the secretary:

| `op` | Secretary call |
| --- | --- |
| `profile.get` | `GET /v1/profile` |
| `profile.put` (`about`, `usage`, `mcp`) | `PUT /v1/profile` |
| `memory.add` (`text`) | `POST /v1/memories` |
| `memory.delete` (`memory`) | `DELETE /v1/memories/{id}` |

Messages from the server:

| Message | Meaning |
| --- | --- |
| binary | PCM16LE mono 16 kHz, paced in real time (speech and earcons) |
| `heard` | What you said |
| `said` | What the secretary said |
| `agent_text` | The answer as it forms |
| `activity`, `activity_done` | Steps the secretary takes |
| `task` | Background work, including goal agents |
| `confirm` | A yes/no question |
| `flush` | Drop buffered audio (barge-in) |
| `idle` | Finished talking |
| `dictation` | Transcribed text for the input box |
| `phantom` | A dropped false transcript |

## Status

- Tested on a Galaxy A34 (Android 16) over USB (`adb reverse`).
- Verified: calls, barge-in, typing, dictation, the confirmation buttons, the speaker, the home-screen shortcut and themes.
- Not yet tested in a car over Bluetooth.
- There is no signed release build yet; build it yourself as above.
- iOS is not supported yet: the call engine is Android-only (CallKit would be the iOS equivalent).
