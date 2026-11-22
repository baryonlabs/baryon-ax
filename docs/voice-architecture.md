# Voice secretary architecture (UML)

These UML views describe how a spoken request travels from the phone to the secretary and back.
They cover the [app](voice-app.md), [phone gateway](phone.md), [voice secretary](voice-secretary.md)
and [speech sidecar](voice.md). The diagrams are Mermaid, so GitHub renders them.

| View | UML diagram | Question it answers |
| --- | --- | --- |
| [1](#1-use-cases) | Use case | Who uses it, for what |
| [2](#2-components) | Component | Which parts exist and how they connect |
| [3](#3-deployment) | Deployment | What runs where |
| [4](#4-sequence-one-spoken-request) | Sequence | One spoken request, end to end |
| [5](#5-sequence-barge-in) | Sequence | Speaking over the secretary |
| [6](#6-sequence-spoken-confirmation) | Sequence | A side effect waits for a spoken yes |
| [7](#7-sequence-goal-agent) | Sequence | Background work by a goal agent |
| [8](#8-state-machine-call) | State machine | States of a call |
| [9](#9-state-machine-goal) | State machine | Lifecycle of a goal |
| [10](#10-activity-utterance-filter) | Activity | How an utterance becomes a request, or is dropped |
| [11](#11-classes) | Class | Main types in the gateway and the secretary |

## 1. Use cases

```mermaid
flowchart LR
    owner(["👤 Owner<br/>(driving, hands busy)"])
    goal_agent(["🤖 Goal agent"])
    fleet(["🖥 DureClaw fleet"])

    subgraph system["바리온 비서"]
        uc1([Ask for a summary<br/>mail · calendar · to-dos · PR/CI])
        uc2([Give a command])
        uc3([Hand off a goal])
        uc4([Answer yes or no])
        uc5([Interrupt / 멈춰])
        uc6([Type or dictate instead of speaking])
        uc7([Hear background results])
    end

    owner --- uc1 & uc2 & uc3 & uc4 & uc5 & uc6 & uc7
    uc2 -. include .-> uc4
    uc3 -. include .-> uc4
    uc3 --- goal_agent
    uc2 --- fleet
    goal_agent -. extend .-> uc7
```

## 2. Components

```mermaid
flowchart LR
    subgraph phone["📱 Android phone"]
        ui["Flutter UI<br/>chat · status chip · settings"]
        engine["VoiceEngine (Kotlin)<br/>mic AEC/NS · speaker · WebSocket"]
        telecom["Telecom self-managed call<br/>→ car Bluetooth"]
        ui <-->|MethodChannel / EventChannel| engine
        engine --- telecom
    end
    sip["📞 SIP app (Linphone)"]

    subgraph gateway["deploy/phone"]
        asterisk["Asterisk<br/>PJSIP · AudioSocket"]
        gw["Phone gateway<br/>Call · VAD · noise gate · echo filter<br/>earcons · chunker · barge-in"]
        asterisk -->|AudioSocket 8 kHz| gw
    end

    subgraph speech["deploy/stt (on-prem)"]
        stt["STT<br/>Whisper turbo · SenseVoice"]
        tts["TTS<br/>Supertonic 3"]
    end

    subgraph secretary["apps/secretary (workstation)"]
        session["Session<br/>Claude Agent SDK · policy"]
        goals["GoalManager<br/>goal agents · worktrees"]
        session --> goals
    end

    claude["☁️ Claude model"]
    mcp["MCP: Gmail · Calendar · to-dos · DureClaw · KB"]

    engine <-->|wss 16 kHz PCM + JSON| gw
    sip <-->|SIP/RTP over tailnet| asterisk
    gw -->|utterance WAV| stt
    gw -->|sentence| tts
    gw <-->|turn · SSE events| session
    session --> claude
    session --> mcp
    goals --> claude
```

## 3. Deployment

```mermaid
flowchart TB
    subgraph car["🚗 Car"]
        bt["Bluetooth hands-free"]
    end
    subgraph handset["Android phone"]
        app["바리온 비서 APK"]
    end
    subgraph server["On-prem server (Docker)"]
        c_gw["container: phone-gateway<br/>:9093 wss · :9092 AudioSocket"]
        c_ast["container: asterisk<br/>:5060/udp · RTP 10000-10019"]
        c_stt["container: stt<br/>:8000 (internal)"]
        c_api["Baryon AX API · web"]
    end
    subgraph workstation["Owner's workstation"]
        sec["apps/secretary :8765<br/>Claude Code sign-in · MCP"]
        wt[".baryon-goals/ worktrees"]
    end
    cloud["Claude API"]

    bt <--> app
    app <-->|wss via HTTPS tunnel or adb reverse| c_gw
    c_ast <--> c_gw
    c_gw --> c_stt
    c_gw <-->|HTTP + SSE, bearer key| sec
    c_api <-->|/v1/assistant, owner only| sec
    sec --> wt
    sec --> cloud
```

## 4. Sequence: one spoken request

```mermaid
sequenceDiagram
    autonumber
    actor Owner
    participant App as App (VoiceEngine)
    participant GW as Gateway (Call)
    participant STT as STT sidecar
    participant SEC as Secretary (Session)
    participant LLM as Claude + tools
    participant TTS as TTS sidecar

    Owner->>App: speaks "열린 PR이랑 CI 알려줘"
    App->>GW: PCM 16 kHz frames (20 ms)
    GW-->>App: {"type":"vad","speaking":true} (ear moves)
    GW->>GW: Silero VAD ends segment after 0.6 s silence
    GW->>GW: noise gate (RMS/peak)
    GW->>STT: POST /v1/audio/transcriptions
    STT-->>GW: text
    GW-->>App: 띠링 earcon + {"type":"heard"}
    GW->>SEC: POST /turn {text}
    SEC->>LLM: query (persona, policy)
    LLM-->>SEC: tool_use gh pr list (auto: read-only)
    SEC-->>GW: event activity + status "깃허브를 확인하고 있어요"
    GW->>TTS: synthesize filler
    TTS-->>GW: WAV
    GW-->>App: audio (paced in real time)
    LLM-->>SEC: answer text (streamed)
    SEC-->>GW: text deltas
    GW->>GW: SentenceChunker
    loop each sentence
        GW->>TTS: synthesize
        TTS-->>GW: WAV
        GW-->>App: audio + {"type":"said"}
    end
    SEC-->>GW: done
    GW-->>App: {"type":"idle"}
```

## 5. Sequence: barge-in

```mermaid
sequenceDiagram
    actor Owner
    participant App
    participant GW as Gateway
    participant SEC as Secretary

    GW-->>App: audio of a long answer
    Owner->>App: starts speaking over it
    App->>GW: loud frames while playing
    GW->>GW: 350 ms of loud speech → interrupt()
    GW-->>App: {"type":"flush"} (drop buffered audio) + 뚝 earcon
    GW->>SEC: POST /interrupt
    SEC->>SEC: client.interrupt(), discard rest of the turn
    Note over GW,SEC: newest request wins, nothing is queued
    GW->>SEC: POST /turn {new text}
```

## 6. Sequence: spoken confirmation

```mermaid
sequenceDiagram
    actor Owner
    participant GW as Gateway
    participant SEC as Secretary (Session)
    participant POL as policy.decide()
    participant LLM as Claude

    LLM->>SEC: tool_use mcp__dureclaw__send_task
    SEC->>POL: decide(tool, input)
    POL-->>SEC: confirm
    SEC-->>GW: {"type":"confirm","prompt":"builder@mini에게 이 작업을 보낼까요?"}
    GW-->>Owner: 딩-동? earcon + spoken question (app shows 응/아니)
    alt says "응" (or taps 응)
        Owner->>GW: "응"
        GW->>SEC: /turn "응" → parse_answer = yes
        SEC-->>LLM: PermissionResultAllow
    else says "아니" / anything else / 2 min silence
        SEC-->>LLM: PermissionResultDeny
    end
    Note over POL: rm -rf, force push, reset --hard → deny without asking
```

## 7. Sequence: goal agent

```mermaid
sequenceDiagram
    actor Owner
    participant SEC as Secretary
    participant GM as GoalManager
    participant GA as Goal agent (sandboxed)
    participant Git as git worktree

    Owner->>SEC: "로그인 버그 고쳐서 테스트 통과시켜 줘"
    SEC-->>Owner: "목표 에이전트에게 맡길까요?" → "응"
    SEC->>GM: start_goal(title, goal, done_when)
    GM->>Git: worktree add -b goal/<id>-<slug>
    GM->>GA: query(GOAL_PROMPT) (async, up to 3 in parallel)
    SEC-->>Owner: "맡겼어요, 끝나면 알려 드릴게요" (conversation goes on)
    loop plan → change → verify
        GA->>Git: edit files in worktree
        GA->>GA: run tests (sandbox)
        GM-->>SEC: task event (step) → app task chip
    end
    opt stuck
        GA->>GM: ask_user("세션 만료를 몇 분으로?")
        GM-->>SEC: needs_input → spoken when the line is free
        Owner->>SEC: "30분"
        SEC->>GM: answer_goal(id, "30분")
    end
    GA->>Git: commit on its branch (never push)
    GM-->>SEC: done + 2–3 sentence report
    SEC-->>Owner: 도미솔 + "목표 '로그인 버그' 작업이 끝났어요…"
```

## 8. State machine: call

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Connecting: call button / shortcut
    Idle --> Typing: send text (no call)
    Typing --> Connecting: call button
    Connecting --> Listening: socket open, greeting
    Listening --> Hearing: VAD speech
    Hearing --> Listening: dropped (quiet, phantom, echo, fragment)
    Hearing --> Thinking: utterance accepted (띠링)
    Thinking --> Speaking: first sentence synthesized
    Thinking --> Thinking: tick every 2.5 s
    Speaking --> Listening: idle
    Speaking --> Hearing: barge-in (뚝)
    Thinking --> AwaitingAnswer: confirm (딩-동?)
    AwaitingAnswer --> Thinking: 응 / 아니
    Listening --> Muted: mute
    Muted --> Listening: unmute
    Listening --> Ended: hang up
    Speaking --> Ended: hang up
    Ended --> [*]
```

## 9. State machine: goal

```mermaid
stateDiagram-v2
    [*] --> queued: start_goal
    queued --> running: a slot is free (max 3)
    running --> needs_input: ask_user
    needs_input --> running: answer_goal / 1 h timeout
    running --> done: report
    running --> failed: error
    queued --> cancelled: cancel_goal
    running --> cancelled: cancel_goal
    needs_input --> cancelled: cancel_goal
    running --> interrupted: secretary restarted
    done --> [*]
    failed --> [*]
    cancelled --> [*]
    interrupted --> [*]
```

## 10. Activity: utterance filter

```mermaid
flowchart TD
    A([PCM frame]) --> B{typing in the last 2 s?}
    B -- yes --> X1[ignore: key clicks]
    B -- no --> C[Silero VAD]
    C --> D{segment ended?}
    D -- no --> E{loud speech while the secretary talks ≥ 350 ms?}
    E -- yes --> I[interrupt: flush + 뚝]
    E -- no --> A
    D -- yes --> F{RMS ≥ 0.004 and peak ≥ 0.1?}
    F -- no --> X2[drop: quiet noise]
    F -- yes --> G[STT]
    G --> H{one syllable and no open question?}
    H -- yes --> X3[drop: fragment]
    H -- no --> J{phantom phrase?<br/>다음 영상에서 만나요…}
    J -- yes --> X4[drop: phantom]
    J -- no --> K{echo of what it just said?}
    K -- yes --> X5[drop: echo]
    K -- no --> L{멈춰 / 그만?}
    L -- yes --> I
    L -- no --> M[띠링 → secretary turn]
```

The thresholds come from a real call. Speech had RMS of about 0.015–0.09 and peaks of 0.3–0.9. Room noise that Whisper turned into "다음 영상에서 만나요" stayed under RMS 0.0025 and peak 0.12. Of 97 recorded segments, 78 were dropped before STT, and every real utterance was kept.

## 11. Classes

```mermaid
classDiagram
    direction LR
    class Transport {
        <<interface>>
        +rate: int
        +audio: bool
        +start() bool
        +receive() bytes|dict|None
        +send_audio(pcm)
        +send_event(event)
        +close()
    }
    class AudioSocketTransport {
        rate = 8000
    }
    class WebSocketTransport {
        rate = 16000
        audio: bool  (?audio=0)
    }
    class Call {
        -queue: Queue
        -generation: int
        -agent_busy: bool
        -awaiting_answer: bool
        +run()
        -_hear(pcm)
        -_utterance(samples)
        -_handle_text(text)
        +interrupt()
        +cue(name)
        +say(text)
        -_player()
        -_thinking_ticks()
    }
    class SileroVad {
        +push(samples) (speaking, utterances)
    }
    class HttpSpeech {
        +transcribe(samples, rate) str
        +synthesize(text, voice, speed)
    }
    class HttpSecretary {
        +start()
        +turn(text) str
        +interrupt()
        +events() AsyncIterator
    }
    class Session {
        +turn(text) str
        +interrupt()
        +notify(text)
        -_can_use_tool(tool, input)
    }
    class GoalManager {
        +start(title, goal, done_when, repo) Goal
        +list() Goal[]
        +answer(id, text) bool
        +cancel(id) bool
    }
    class Goal {
        id
        branch
        worktree
        status
        steps
        question
        summary
    }
    Transport <|.. AudioSocketTransport
    Transport <|.. WebSocketTransport
    Call --> Transport
    Call --> SileroVad
    Call --> HttpSpeech
    Call --> HttpSecretary
    HttpSecretary ..> Session : HTTP + SSE
    Session --> GoalManager
    GoalManager "1" o-- "*" Goal
```
