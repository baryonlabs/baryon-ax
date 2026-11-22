# 바리온 비서 app: UX guidelines

These are the rules the app follows. They come from the owner's own requests, made by voice on
real calls and recorded during a debugging session. Keep them when you change the app or build
the next version.

## The situation the app is for

The owner is often driving or has their hands full. The phone is mounted or in a pocket. The
app must work **without looking at it**, and be readable **at a glance** when they do look.

## Rules

1. **The conversation is the screen.** The chat list takes all the space that is left. Nothing
   permanent sits above or below it except a one-line top bar and the input field. *(Requested on a call:
   "채팅의 영역이 가장 크게".)*
2. **Status is small and to the right.** The status is a chip in the top-right corner: an icon and two or three words
   ("듣고 있어요", "생각하는 중", "말하는 중", "대답이 필요해요"). Details such as the audio route, echo
   canceller and ignored noise go into its tooltip. *(Requested: "위에 … 듣고 있어요 … 오른쪽으로 좀 빼서".)*
3. **Show that it is listening.** While the line is open and the secretary is not talking, an ear
   icon pulses. It moves more while the VAD hears speech. The animation stops when it is not
   listening, to save battery. *(Requested: "듣고 있다는 표시를 … 귀로 계속 움직이게".)*
4. **Call controls float.** The call and hang-up button floats in the bottom-right corner. Speaker and mute
   float above it during a call. They hide while the keyboard is up. *(Requested: "하단에 있는 통화하는
   것들은 플로팅으로".)*
5. **Every state has a sound.** The same states are audible without a screen:
   띠링 (heard), a soft tick (working), 뚝 (stopped), 딩-동? (needs a yes or no), 도미솔 (a background goal finished)
   and a low buzz (error). Sounds stay short (under 0.5 s) and quiet, so they never trigger barge-in or STT.
6. **Work shows inside the conversation.** While the secretary works, an outlined bubble at the end of the chat
   lists its steps and the answer as it forms. It disappears when the answer is complete. Background goals are
   chips in the top bar.
7. **Answers are one tap.** A yes/no question is a card at the end of the chat with large 응 / 아니 buttons,
   at least 52 dp tall. Saying the answer works too.
8. **History stays.** The last 200 messages are kept on the phone, with a timestamp after a 10-minute gap.
   They can be cleared in settings.
9. **Colours come from the theme.** Use only `ColorScheme` roles (`primaryContainer`,
   `surfaceContainerHigh`, `tertiaryContainer`, `error`, …), never fixed colours. A light theme must look
   right with no dark leftovers. *(Reported: "밝게 바꿔놓으면 칼라 색깔이 안 맞는 부분".)*
10. **Settings are generous.** The owner asked for many settings ("더 많은 설정"). There are three groups:
    - **화면:** theme, text size, show steps
    - **소리:** earcons, voice (1–10), speaking speed
    - **통화:** call on open, keep the screen on, haptics, home-screen shortcut, plus clear history and server

    Settings that matter to the gateway (earcons, voice, speed) are sent at the start of a call and whenever
    they change.
11. **Noise is not a request.** Sounds that are too quiet, one-syllable fragments when no question is open,
    phantom phrases and echoes are dropped before they reach the secretary. The status tooltip counts them.
    Never interrupt the secretary or start a turn on them.
12. **Hands-free start.** The **비서에게 전화** home-screen shortcut starts a call in one tap. "앱을 열면 바로
    전화" does it on launch, for a phone mounted in the car.

## Checklist for a change

- [ ] Does it still work with the screen off, using sounds alone?
- [ ] Is the chat list still the largest element?
- [ ] Does it look right in the 밝게 theme and at 160% text size?
- [ ] Are new colours taken from `ColorScheme`?
- [ ] Is there a widget test (`apps/voice_app/test/widget_test.dart`)?
