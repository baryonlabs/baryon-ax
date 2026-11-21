import { AudioLines, Mic, MicOff, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { samplesToWav } from "./audio";
import { EnergyVad, openMicrophone, SentenceChunker, SpeechPlayer, type Microphone } from "./live";
import type { VoiceCommand } from "./VoiceInput";

type Phase = "starting" | "listening" | "hearing" | "transcribing" | "thinking" | "speaking" | "muted" | "error";
type AskOptions = { style?: "text" | "voice"; onToken?: (token: string) => void; onDone?: () => void };
type LivePrefs = { voice: number; speed: number; bargeIn: boolean; mode: "docs" | "secretary" };
type SecretaryEvent = { type: "text"; delta: string } | { type: "status"; text: string } | { type: "confirm"; id: string; prompt: string }
  | { type: "confirmed"; allow: boolean } | { type: "done" } | { type: "error"; message: string };
type Request = (path: string, init?: RequestInit) => Promise<Response>;

const PREFS_KEY = "baryon.live";
const DEFAULTS: LivePrefs = { voice: 0, speed: 1.05, bargeIn: true, mode: "docs" };
const STOP_WORDS = /^(멈춰|그만|중지|스톱|stop)[.!?\s]*$/i;

const TEXT = {
  ko: {
    title: "라이브 대화", end: "대화 끝내기 (Esc)", mute: "마이크 끄기", unmute: "마이크 켜기",
    phases: {
      starting: "마이크를 여는 중…", listening: "듣고 있어요. 말씀하세요.", hearing: "듣는 중…", transcribing: "알아듣는 중…",
      thinking: "생각하는 중…", speaking: "말하는 중 · 말씀하시면 멈춥니다", muted: "마이크가 꺼져 있습니다", error: "",
    } as Record<Phase, string>,
    you: "나", latency: (ms: number) => `응답 시작 ${(ms / 1000).toFixed(1)}초`,
    voice: "목소리", speed: "속도", bargeIn: "말하면 끼어들기",
    micDenied: "마이크를 열 수 없습니다. 브라우저 권한과 HTTPS 연결을 확인하세요.",
    sttFailed: "음성을 알아듣지 못했습니다.", ttsFailed: "음성 합성 서버에 연결할 수 없습니다. 답변은 화면에 표시됩니다.",
    privacy: "음성은 사내 서버에서만 처리되며 저장하지 않습니다. 스피커 대신 이어폰을 쓰면 끼어들기가 더 정확합니다.",
    mode: "상대", modes: { docs: "문서 Q&A", secretary: "비서" }, connecting: "비서에 연결하는 중…",
    confirm: "확인 질문", secretaryFailed: "비서에 연결할 수 없습니다.",
    secretaryHint: "비서는 메일·일정·할 일·PR 요약과 DureClaw 작업 지시를 합니다. 변경이 생기는 일은 \"응\"이라고 답해야 실행됩니다.",
  },
  en: {
    title: "Live conversation", end: "End conversation (Esc)", mute: "Mute microphone", unmute: "Unmute microphone",
    phases: {
      starting: "Opening the microphone…", listening: "Listening. Go ahead.", hearing: "Hearing you…", transcribing: "Understanding…",
      thinking: "Thinking…", speaking: "Speaking · talk to interrupt", muted: "Microphone is off", error: "",
    } as Record<Phase, string>,
    you: "You", latency: (ms: number) => `Replied in ${(ms / 1000).toFixed(1)} s`,
    voice: "Voice", speed: "Speed", bargeIn: "Interrupt by speaking",
    micDenied: "Cannot open the microphone. Check the browser permission and that the page uses HTTPS.",
    sttFailed: "Could not understand that.", ttsFailed: "Cannot reach the speech server. The answer is shown on screen.",
    privacy: "Speech is processed on your own servers and never stored. Earphones make interruptions more reliable than speakers.",
    mode: "Talk to", modes: { docs: "Document Q&A", secretary: "Secretary" }, connecting: "Connecting to the secretary…",
    confirm: "Question", secretaryFailed: "Cannot reach the secretary.",
    secretaryHint: "The secretary summarizes mail, calendar, to-dos and PRs and dispatches DureClaw tasks. Anything that changes something runs only after you say yes.",
  },
};

function loadPrefs(): LivePrefs {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") }; } catch { return DEFAULTS; }
}

export default function LiveVoice({ locale, request, ask, stop, onCommand, onClose }: {
  locale: "ko" | "en"; request: Request;
  ask: (question: string, options: AskOptions) => Promise<void>;
  stop: () => void; onCommand: (command: VoiceCommand) => void; onClose: () => void;
}) {
  const t = TEXT[locale];
  const [phase, setPhase] = useState<Phase>("starting");
  const [prefs, setPrefs] = useState<LivePrefs>(loadPrefs);
  const [heard, setHeard] = useState("");
  const [notice, setNotice] = useState("");
  const [latency, setLatency] = useState<number | null>(null);
  const [level, setLevel] = useState(0);
  const [secretaryAllowed, setSecretaryAllowed] = useState(false);
  const [question, setQuestion] = useState("");

  // Mutable conversation state, read from audio callbacks.
  const live = useRef({
    phase: "starting" as Phase, turn: 0, muted: false, chatRunning: false, chatDone: Promise.resolve(),
    speechEndedAt: 0, firstAudio: true, prefs,
    session: "" as string, sessionStarting: null as Promise<string> | null, discard: false,
    abort: null as AbortController | null, chunker: new SentenceChunker(),
  });
  const props = useRef({ request, ask, stop, onCommand, locale });
  props.current = { request, ask, stop, onCommand, locale };
  live.current.prefs = prefs;

  const vadRef = useRef<EnergyVad | null>(null);
  const micRef = useRef<Microphone | null>(null);
  const playerRef = useRef<SpeechPlayer | null>(null);
  const secretaryRef = useRef<{ connect: () => void } | null>(null);

  const go = (next: Phase) => { live.current.phase = next; setPhase(next); };

  const savePrefs = (next: Partial<LivePrefs>) => setPrefs(previous => {
    const merged = { ...previous, ...next };
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(merged)); } catch { /* storage blocked */ }
    return merged;
  });

  useEffect(() => {
    let disposed = false;
    const player = new SpeechPlayer();
    playerRef.current = player;
    player.onStart = () => {
      const state = live.current;
      if (state.firstAudio && state.speechEndedAt) { setLatency(Date.now() - state.speechEndedAt); state.firstAudio = false; }
      if (vadRef.current) vadRef.current.strict = true;
      if (state.phase !== "hearing") go("speaking");
    };
    player.onIdle = () => {
      if (vadRef.current) vadRef.current.strict = false;
      const state = live.current;
      if (!state.chatRunning && (state.phase === "speaking" || state.phase === "thinking")) go(state.muted ? "muted" : "listening");
    };

    /** Stop talking and cancel the answer being generated (barge-in or "멈춰"). */
    const interrupt = () => {
      live.current.turn++;
      player.stop();
      if (live.current.prefs.mode === "secretary") {
        live.current.chunker.flush();
        if (live.current.chatRunning && live.current.session) {
          live.current.discard = true; // drop what the agent still sends for the interrupted turn
          void props.current.request(`/v1/assistant/sessions/${live.current.session}/interrupt`, { method: "POST" });
        }
      } else if (live.current.chatRunning) props.current.stop();
      if (vadRef.current) vadRef.current.strict = false;
    };

    const speak = (text: string, turn: number) => {
      const { prefs: current } = live.current;
      player.enqueue((async () => {
        if (turn !== live.current.turn) return null;
        try {
          const response = await props.current.request("/v1/audio/speech", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ input: text, voice: current.voice, speed: current.speed, locale: props.current.locale }),
          });
          if (!response.ok) throw new Error(String(response.status));
          return turn === live.current.turn ? await response.arrayBuffer() : null;
        } catch {
          setNotice(TEXT[props.current.locale].ttsFailed);
          return null;
        }
      })());
    };

    const idle = () => { if (!player.playing) go(live.current.muted ? "muted" : "listening"); };

    const onSecretaryEvent = (event: SecretaryEvent) => {
      const state = live.current;
      if (state.discard) {
        if (event.type === "done") { state.discard = false; state.chatRunning = false; }
        return;
      }
      if (event.type === "text") {
        for (const chunk of state.chunker.push(event.delta)) speak(chunk, state.turn);
      } else if (event.type === "status") {
        speak(event.text, state.turn); // short filler so a long lookup is never silent
      } else if (event.type === "confirm") {
        const rest = state.chunker.flush();
        if (rest) speak(rest, state.turn);
        speak(event.prompt, state.turn);
        setQuestion(event.prompt);
        state.chatRunning = false; // the agent waits for the user's yes or no
        idle();
      } else if (event.type === "confirmed") {
        setQuestion("");
        state.chatRunning = true;
      } else if (event.type === "done") {
        const rest = state.chunker.flush();
        if (rest) speak(rest, state.turn);
        state.chatRunning = false;
        if (state.phase === "thinking") idle();
      } else if (event.type === "error") {
        setNotice(event.message);
      }
    };

    const listenSecretary = async (session: string) => {
      const controller = new AbortController();
      live.current.abort = controller;
      try {
        const response = await props.current.request(`/v1/assistant/sessions/${session}/events`, { signal: controller.signal });
        if (!response.ok || !response.body) throw new Error(String(response.status));
        const reader = response.body.getReader(), decoder = new TextDecoder();
        let buffer = "";
        while (!disposed) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() || "";
          for (const frame of frames) {
            if (frame.startsWith("data: ")) onSecretaryEvent(JSON.parse(frame.slice(6)));
          }
        }
      } catch {
        if (!controller.signal.aborted) setNotice(TEXT[props.current.locale].secretaryFailed);
      }
      if (live.current.session === session) live.current.session = "";
    };

    const secretarySession = async (): Promise<string> => {
      if (live.current.session) return live.current.session;
      live.current.sessionStarting ??= (async () => {
        const response = await props.current.request("/v1/assistant/sessions", { method: "POST" });
        if (!response.ok) throw new Error(String(response.status));
        const { id } = await response.json() as { id: string };
        live.current.session = id;
        void listenSecretary(id);
        return id;
      })().finally(() => { live.current.sessionStarting = null; });
      return live.current.sessionStarting;
    };

    const askSecretary = async (text: string) => {
      const state = live.current;
      state.firstAudio = true;
      state.chatRunning = true;
      state.chunker = new SentenceChunker();
      go("thinking");
      try {
        const session = await secretarySession();
        const response = await props.current.request(`/v1/assistant/sessions/${session}/turn`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
        });
        if (!response.ok) throw new Error(String(response.status));
      } catch {
        state.chatRunning = false;
        setNotice(TEXT[props.current.locale].secretaryFailed);
        idle();
      }
    };
    secretaryRef.current = { connect: () => void secretarySession().catch(() => setNotice(TEXT[props.current.locale].secretaryFailed)) };

    const answer = async (question: string, turn: number) => {
      if (live.current.prefs.mode === "secretary") { await askSecretary(question); return; }
      await live.current.chatDone; // an interrupted answer must finish unwinding first
      if (turn !== live.current.turn || disposed) return;
      const chunker = new SentenceChunker();
      let resolveDone = () => {};
      live.current.chatDone = new Promise<void>(resolve => { resolveDone = resolve; });
      live.current.chatRunning = true;
      live.current.firstAudio = true;
      go("thinking");
      let finished = false;
      await props.current.ask(question, {
        style: "voice",
        onToken: token => {
          if (turn !== live.current.turn) return;
          for (const chunk of chunker.push(token)) speak(chunk, turn);
        },
        onDone: () => {
          finished = true;
          live.current.chatRunning = false;
          const rest = chunker.flush();
          if (turn === live.current.turn && rest) speak(rest, turn);
          if (turn === live.current.turn && !player.playing && !rest && live.current.phase === "thinking") {
            go(live.current.muted ? "muted" : "listening");
          }
          resolveDone();
        },
      });
      if (!finished) { // refused: a typed question is still being answered
        live.current.chatRunning = false;
        resolveDone();
        if (turn === live.current.turn) go(live.current.muted ? "muted" : "listening");
      }
    };

    const handleUtterance = async (samples: Float32Array, rate: number) => {
      const turn = ++live.current.turn;
      live.current.speechEndedAt = Date.now();
      go("transcribing");
      try {
        const form = new FormData();
        form.append("file", await samplesToWav(samples, rate), "utterance.wav");
        form.append("locale", props.current.locale);
        const secretary = live.current.prefs.mode === "secretary";
        form.append("commands", secretary ? "false" : "true");
        const response = await props.current.request("/v1/audio/transcriptions", { method: "POST", body: form });
        if (!response.ok) throw new Error(String(response.status));
        const result: { text: string; command: VoiceCommand | null } = await response.json();
        if (turn !== live.current.turn || disposed) return;
        const text = (result.text || "").trim();
        if (result.command) {
          setHeard(`🎙 ${result.command.phrase}`);
          if (result.command.action === "stop") interrupt();
          else if (result.command.action === "new_chat" || result.command.action === "open_view") props.current.onCommand(result.command);
          go(live.current.muted ? "muted" : "listening");
          return;
        }
        if (secretary && STOP_WORDS.test(text)) { setHeard(`🎙 ${text}`); interrupt(); go(live.current.muted ? "muted" : "listening"); return; }
        // Noise, "음", "어". In secretary mode a one-syllable "응"/"네" is a real answer.
        const minimum = secretary ? 1 : 2;
        if (text.replace(/[\s.,!?]/g, "").length < minimum) { go(live.current.muted ? "muted" : "listening"); return; }
        setHeard(text);
        setNotice("");
        await answer(text, turn);
      } catch {
        if (turn === live.current.turn) { setNotice(TEXT[props.current.locale].sttFailed); go("listening"); }
      }
    };

    (async () => {
      try {
        let lastLevel = 0;
        const mic = await openMicrophone(samples => {
          const vad = vadRef.current;
          if (!vad || live.current.muted) return;
          // Without barge-in, ignore the microphone while the assistant answers.
          const busy = ["transcribing", "thinking", "speaking"].includes(live.current.phase);
          if (busy && !live.current.prefs.bargeIn) return;
          let peak = 0;
          for (let i = 0; i < samples.length; i += 8) peak = Math.max(peak, Math.abs(samples[i]));
          const now = performance.now();
          if (now - lastLevel > 80) { lastLevel = now; setLevel(Math.min(1, peak * 4)); }
          for (const event of vad.push(samples)) {
            if (event.type === "speech-start") {
              if (live.current.phase === "speaking" || live.current.phase === "thinking") interrupt();
              go("hearing");
            } else if (event.type === "speech-end") {
              void handleUtterance(event.samples, mic.rate);
            } else if (live.current.phase === "hearing") {
              go("listening");
            }
          }
        });
        if (disposed) { mic.stop(); return; }
        micRef.current = mic;
        vadRef.current = new EnergyVad({ rate: mic.rate });
        go("listening");
      } catch {
        setNotice(TEXT[props.current.locale].micDenied);
        go("error");
      }
    })();

    return () => {
      disposed = true;
      live.current.turn++;
      micRef.current?.stop();
      micRef.current = null;
      vadRef.current = null;
      player.close();
      live.current.abort?.abort();
      if (live.current.session) void props.current.request(`/v1/assistant/sessions/${live.current.session}`, { method: "DELETE" });
      else if (live.current.chatRunning) props.current.stop();
    };
  }, []);

  useEffect(() => {
    props.current.request("/v1/assistant/status").then(response => response.ok ? response.json() : null)
      .then(result => setSecretaryAllowed(Boolean(result?.allowed))).catch(() => setSecretaryAllowed(false));
  }, []);
  useEffect(() => { if (prefs.mode === "secretary" && secretaryAllowed) secretaryRef.current?.connect(); }, [prefs.mode, secretaryAllowed]);

  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);

  function toggleMute() {
    const muted = !live.current.muted;
    live.current.muted = muted;
    vadRef.current?.reset();
    if (muted && (phase === "listening" || phase === "hearing")) go("muted");
    if (!muted && phase === "muted") go("listening");
    setLevel(0);
  }

  const status = phase === "error" ? notice : t.phases[phase];
  return (
    <section className={`live-voice ${phase}`} aria-label={t.title}>
      <div className="live-orb" style={{ ["--level" as string]: String(phase === "hearing" || phase === "listening" ? level : 0) }} aria-hidden="true">
        <AudioLines size={22}/>
      </div>
      <div className="live-main">
        <div className="live-status" role="status" aria-live="polite">
          <strong>{t.title}</strong>
          <span>{status}</span>
          {latency != null && phase !== "error" && <small>{t.latency(latency)}</small>}
        </div>
        {heard && <p className="live-heard"><span>{t.you}</span>{heard}</p>}
        {question && <p className="live-question"><span>{t.confirm}</span>{question}</p>}
        {notice && phase !== "error" && <p className="live-notice">{notice}</p>}
        <div className="live-controls">
          {secretaryAllowed && <div className="live-mode" role="radiogroup" aria-label={t.mode}>
            {(["docs", "secretary"] as const).map(mode => <button type="button" role="radio" key={mode} aria-checked={prefs.mode === mode}
              className={prefs.mode === mode ? "active" : ""} onClick={() => savePrefs({ mode })}>{t.modes[mode]}</button>)}
          </div>}
          <label>{t.voice}
            <select value={prefs.voice} onChange={event => savePrefs({ voice: Number(event.target.value) })}>
              {Array.from({ length: 10 }, (_, index) => <option key={index} value={index}>{index + 1}</option>)}
            </select>
          </label>
          <label>{t.speed}
            <select value={prefs.speed} onChange={event => savePrefs({ speed: Number(event.target.value) })}>
              {[0.9, 1.0, 1.05, 1.15, 1.3].map(value => <option key={value} value={value}>{value}×</option>)}
            </select>
          </label>
          <label className="live-toggle"><input type="checkbox" checked={prefs.bargeIn} onChange={event => savePrefs({ bargeIn: event.target.checked })}/>{t.bargeIn}</label>
        </div>
        <small className="live-privacy">{prefs.mode === "secretary" && secretaryAllowed ? t.secretaryHint : t.privacy}</small>
      </div>
      <div className="live-actions">
        <button type="button" className="live-mute" onClick={toggleMute} disabled={phase === "error" || phase === "starting"}
          aria-pressed={phase === "muted"} aria-label={phase === "muted" ? t.unmute : t.mute} title={phase === "muted" ? t.unmute : t.mute}>
          {phase === "muted" ? <MicOff size={17}/> : <Mic size={17}/>}
        </button>
        <button type="button" className="live-end" onClick={onClose} aria-label={t.end} title={t.end}><X size={18}/></button>
      </div>
    </section>
  );
}
