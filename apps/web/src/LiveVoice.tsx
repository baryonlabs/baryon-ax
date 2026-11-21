import { AudioLines, Mic, MicOff, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { samplesToWav } from "./audio";
import { EnergyVad, openMicrophone, SentenceChunker, SpeechPlayer, type Microphone } from "./live";
import type { VoiceCommand } from "./VoiceInput";

type Phase = "starting" | "listening" | "hearing" | "transcribing" | "thinking" | "speaking" | "muted" | "error";
type AskOptions = { style?: "text" | "voice"; onToken?: (token: string) => void; onDone?: () => void };
type LivePrefs = { voice: number; speed: number; bargeIn: boolean };
type Request = (path: string, init?: RequestInit) => Promise<Response>;

const PREFS_KEY = "baryon.live";
const DEFAULTS: LivePrefs = { voice: 0, speed: 1.05, bargeIn: true };

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

  // Mutable conversation state, read from audio callbacks.
  const live = useRef({
    phase: "starting" as Phase, turn: 0, muted: false, chatRunning: false, chatDone: Promise.resolve(),
    speechEndedAt: 0, firstAudio: true, prefs,
  });
  const props = useRef({ request, ask, stop, onCommand, locale });
  props.current = { request, ask, stop, onCommand, locale };
  live.current.prefs = prefs;

  const vadRef = useRef<EnergyVad | null>(null);
  const micRef = useRef<Microphone | null>(null);
  const playerRef = useRef<SpeechPlayer | null>(null);

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
      if (live.current.chatRunning) props.current.stop();
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

    const answer = async (question: string, turn: number) => {
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
        form.append("commands", "true");
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
        if (text.replace(/[\s.,!?]/g, "").length < 2) { go(live.current.muted ? "muted" : "listening"); return; } // noise, "음", "어"
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
      if (live.current.chatRunning) props.current.stop();
    };
  }, []);

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
        {notice && phase !== "error" && <p className="live-notice">{notice}</p>}
        <div className="live-controls">
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
        <small className="live-privacy">{t.privacy}</small>
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
