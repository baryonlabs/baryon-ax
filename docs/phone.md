# Phone: call the voice secretary

You call the [voice secretary](voice-secretary.md) from your phone, and it sounds
like an ordinary phone call. SIP apps hand calls to the phone's own call system,
which means:

- The car's Bluetooth hands-free picks the call up.
- Steering-wheel buttons answer and hang up.
- The car display shows the call.

No custom app is needed. A browser page cannot do this: it plays as media and the
microphone stops when the screen locks.

```
📱 Linphone (Android/iOS) ──SIP + RTP over your tailnet──▶ Asterisk (deploy/phone/asterisk)
                                                              │ AudioSocket, 8 kHz
                                                              ▼
                                         phone gateway (deploy/phone/gateway)
                       Silero VAD → STT (Whisper turbo) → voice secretary → TTS (Supertonic) → back to the call
                       speaking over the answer interrupts it · "멈춰" stops the current step
```

## What a call sounds like

This is a real SIP call made through Asterisk and the gateway. The audio the caller
heard was transcribed afterwards with Whisper:

```
비서  네, 비서입니다. 말씀하세요.
나    열린 PR이랑 CI 상태 알려줘
비서  깃허브를 확인하고 있어요.
비서  열려있는 PR은 지금 하나도 없어요.
비서  최근 CI 5건도 전부 성공이고, 가장 최근 건은 어제 저녁 메인에 들어간 음성 비서 기능이에요.
```

A side effect asks first:

```
비서  웹클로에게 이 작업을 보낼까요? 열기 naver.com
나    아니
비서  네이버 열기 요청은 거절되어서 보내지 않았어요.
```

## Server

1. Run the [voice profile](voice.md). It needs a dictation model and Supertonic 3. Install **Whisper turbo** too: phone audio is 8 kHz, and Whisper hears it far better than SenseVoice.
2. Run the [voice secretary](voice-secretary.md#setup) on your workstation.
3. Put the server on your tailnet (or VPN), then set these in `.env`:

   ```
   PHONE_USER=driver
   PHONE_PASSWORD=<openssl rand -hex 16>
   PHONE_BIND=<server tailnet IP>              # SIP must never face the internet
   PHONE_EXTERNAL_ADDRESS=<server tailnet IP>  # the address put in SDP for the phone's audio
   SECRETARY_URL=http://<workstation tailnet IP>:8765
   SECRETARY_API_KEY=<same key as the secretary>
   ```

4. Start it:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.voice.yml -f docker-compose.phone.yml up -d --build
   ```

The ports are UDP 5060 for SIP and UDP 10000–10049 for audio. The password must be at
least 16 characters, and Asterisk refuses to start otherwise.

## Android (Linphone)

1. Install **Tailscale** and **Linphone** from the Play Store, and sign in to Tailscale.
2. In Linphone, choose **Use a SIP account** and enter:
   - Username: `PHONE_USER`
   - Password: `PHONE_PASSWORD`
   - Domain: `<server tailnet IP>`
   - Transport: **UDP**
3. Go to **Settings → Calls** and turn on the option that uses the Android Telecom manager (its exact label varies by Linphone version). The call then behaves like a normal phone call, including car Bluetooth.
4. Add a contact named **비서** with the SIP address `sip:100@<server tailnet IP>`. Put it in favourites or add a home-screen shortcut, so starting a call is one tap.

In the car, connect Bluetooth as usual and call **비서**. You hear "네, 비서입니다. 말씀하세요."

## Tips

- **Speak after the greeting.** Talking over any answer stops it ("barge-in"). "멈춰" stops what the secretary is doing.
- **Answer questions with yes or no.** When it asks "~할까요?", say "응", "네" or "보내줘" to go ahead, or "아니" or "취소" to stop. Anything else counts as no and is taken as your next request.
- **Latency.** Whisper turbo takes about 1–2.5 s to understand you on CPU, and the first spoken filler follows about 2 s later. A GPU or a faster model shortens this. `PHONE_STT_MODEL=sensevoice-ko` answers in about 0.1 s but mishears more on phone audio.
- **Privacy.** The gateway logs only call start and end, utterance lengths and timings. It never logs what was said. The call carries audio only between your phone, Asterisk and the gateway, inside your tailnet.

## Not yet

- **The secretary calling you** when a task finishes. Ringing a sleeping Android phone needs push notifications in Linphone.
- **TLS/SRTP for SIP.** The tailnet already encrypts the traffic.
- **More than one caller.** There is one SIP account per deployment, and the secretary is owner-only.

## Tests

```bash
cd deploy/phone/gateway
uv run --no-project --with-requirements requirements.txt --with pytest python -m pytest -q test_gateway.py
```

These tests connect a fake Asterisk over a real socket. They cover the greeting, an answer,
barge-in, "멈춰", the spoken confirmation and hang-up.

For a full call on one machine, start Asterisk and the gateway as containers on one
Docker network, then dial extension 100 from a `baresip` container. Use an 8 kHz WAV
as the microphone (`audio_source aufile,…`) and the `sndfile` module to record what
the caller hears.
