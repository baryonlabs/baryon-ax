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
   PHONE_DOCKER_NET=<docker network subnet>    # only the container network, never the phone's Wi-Fi range
   SECRETARY_URL=http://<workstation tailnet IP>:8765
   SECRETARY_API_KEY=<same key as the secretary>
   ```

4. Start it:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.voice.yml -f docker-compose.phone.yml up -d --build
   ```

The ports are UDP 5060 for SIP and UDP 10000–10019 for audio (ten calls). The password must be at
least 16 characters, and Asterisk refuses to start otherwise.

**Your voice only.** Each utterance is denoised with GTCRN (a 0.5 MB speech-enhancement model,
MIT, about 40 ms per second of audio on one CPU thread) before speech-to-text. Room noise and the
voices of people near the phone are dropped, and STT hears the cleaned audio. `GATEWAY_VOICE_FILTER`
sets the default (`strict`: only a voice close to the microphone; `normal`: any clear voice; `off`),
and the app can change it per call. If a hands-free car microphone sits far from you and your
own requests are dropped, use `normal` or lower `GATEWAY_NEAR_FIELD_RMS` (default 0.025). The gateway
logs each dropped segment with its level, so the threshold can be read off a real call.

Optional: let the phone also connect on the same Wi-Fi with `PHONE_LAN_ADDRESS=<server LAN IP>`.
This adds a second SIP transport that advertises the LAN address, so the audio stays on the
LAN. Docker Desktop cannot publish one container port on two host addresses, so publish the audio
range on all interfaces (`-p 10000-10019:10000-10019/udp`) and SIP once per address. Strict RTP
only accepts audio from the call's own peer.

## Joining a self-hosted control server (headscale)

The Tailscale app can join a headscale tailnet next to your existing account:

1. In Tailscale, open **Accounts**, tap **⋮**, then **Use an alternate server**.
2. Enter your headscale URL and tap **Add account**. A browser page shows
   `headscale auth register --auth-id hskey-authreq-… --user USERNAME`.
3. Run that command on the headscale server with your user. The phone joins and gets a `100.64.x.x` address.

Alternatively, **⋮ → Use an auth key** accepts a one-time preauth key
(`headscale preauthkeys create --user <user> --expiration 1h`).

## Android (Linphone)

1. Install **Tailscale** and **Linphone** from the Play Store, and sign in to Tailscale.
2. In Linphone, choose **Use a SIP account** and enter:
   - Username: `PHONE_USER`
   - Password: `PHONE_PASSWORD`
   - Domain: `<server tailnet IP>`
   - Transport: **UDP**
3. Linphone 6 registers itself with Android's call system (a self-managed phone account with Bluetooth, earpiece, speaker and wired routes), so calls already behave like normal phone calls in the car. Older versions have a Telecom manager switch under **Settings → Calls**.
4. Add a contact named **비서** with the SIP address `sip:100@<server tailnet IP>`. Put it in favourites or add a home-screen shortcut, so starting a call is one tap.

In the car, connect Bluetooth as usual and call **비서**. You hear "네, 비서입니다. 말씀하세요."

## Tips

- **Interrupt any time.** Speaking for about 0.16 s while the secretary talks stops it at once, and your newest sentence replaces the old request instead of waiting in a queue. "멈춰" stops what it is doing.
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
