import { AudioLines, Download, Loader2, Mic, MicOff, Settings2, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { recordingToWav } from "./audio";

export type VoiceCommand = { action: "new_chat" | "send" | "clear" | "stop" | "open_view"; view: string | null; phrase: string };
type VoiceMode = "auto" | "hold" | "toggle";
type VoicePrefs = { enabled: boolean; mode: VoiceMode; model: string; commands: boolean };
type VoiceModel = {
  id: string; name: string; task?: "stt" | "tts"; size_mb: number; languages: string[]; badges: string[]; license: string;
  notes: string; status: "installed" | "available" | "downloading" | "failed"; progress: number | null; error: string | null;
};
type ModelsResponse = { enabled: boolean; reachable: boolean; can_manage: boolean; default: string | null; models: VoiceModel[]; error?: string };
type Request = (path: string, init?: RequestInit) => Promise<Response>;

const PREFS_KEY = "baryon.voice";
const MAX_SECONDS = 60;
const HOLD_MS = 400;
const DEFAULT_PREFS: VoicePrefs = { enabled: true, mode: "auto", model: "", commands: true };

const TEXT = {
  ko: {
    start: "음성으로 입력", stop: "녹음 끝내기", recording: (s: number) => `녹음 중 ${s}초`, transcribing: "받아쓰는 중…",
    settings: "음성 설정", title: "음성", enable: "음성 받아쓰기 활성화",
    enableHint: "입력창에 말로 텍스트를 입력합니다.",
    mode: "받아쓰기 모드", modes: { auto: "자동", hold: "길게 누르기", toggle: "토글" },
    modeHelp: "자동: 탭하여 시작하고 중지합니다. 길게 누르면 손을 뗄 때까지 녹음합니다. 길게 누르기: 누르고 있는 동안 받아씁니다. 토글: 한 번 누르면 시작하고, 다시 누르면 중지합니다. 단축키 Alt+V.",
    commands: "음성 명령", commandsHint: "\"새 대화\", \"보내\", \"지워\", \"멈춰\", \"지식 화면 열어\"처럼 말하면 바로 실행합니다.",
    model: "음성 모델", serverDefault: "서버 기본값", notInstalled: "설치 필요", installing: "설치 중",
    install: "내려받기", failed: "설치 실패", recommended: "권장", fast: "빠름", english: "영어·유럽어",
    privacy: "음성은 사내 서버에서만 받아쓰며 저장하지 않습니다.",
    unavailable: "음성 인식 서버가 설정되지 않았습니다.", unreachable: "음성 인식 서버에 연결할 수 없습니다.",
    micDenied: "마이크 권한이 필요합니다.", failedToTranscribe: "받아쓰지 못했습니다.", empty: "말소리를 알아듣지 못했습니다.",
    adminOnly: "모델 설치는 관리자만 할 수 있습니다.",
    live: "라이브 대화 — 말로 묻고 음성으로 답을 듣습니다", liveNeedsTts: "라이브 대화에는 음성 합성 모델(Supertonic 3) 설치가 필요합니다.",
    speechModels: "음성 합성 (라이브 대화)",
  },
  en: {
    start: "Dictate", stop: "Stop recording", recording: (s: number) => `Recording ${s}s`, transcribing: "Transcribing…",
    settings: "Voice settings", title: "Voice", enable: "Enable voice dictation",
    enableHint: "Type into the composer by speaking.",
    mode: "Dictation mode", modes: { auto: "Auto", hold: "Hold", toggle: "Toggle" },
    modeHelp: "Auto: tap to start and stop; press and hold to record until you let go. Hold: dictates while pressed. Toggle: press once to start, again to stop. Shortcut Alt+V.",
    commands: "Voice commands", commandsHint: "Say \"new chat\", \"send\", \"clear\", \"stop\" or \"open knowledge\" to run them.",
    model: "Voice model", serverDefault: "Server default", notInstalled: "Not installed", installing: "Installing",
    install: "Download", failed: "Install failed", recommended: "Recommended", fast: "Fast", english: "English/EU",
    privacy: "Speech is transcribed on your own servers and never stored.",
    unavailable: "No speech-to-text server is configured.", unreachable: "Cannot reach the speech-to-text server.",
    micDenied: "Microphone permission is required.", failedToTranscribe: "Could not transcribe.", empty: "No speech was recognized.",
    adminOnly: "Only operators can install models.",
    live: "Live conversation — ask by voice and hear the answer", liveNeedsTts: "Live conversation needs a speech model (Supertonic 3) installed.",
    speechModels: "Speech synthesis (live conversation)",
  },
};

function loadPrefs(): VoicePrefs {
  try {
    return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") };
  } catch {
    return DEFAULT_PREFS;
  }
}

export default function VoiceInput({ locale, request, disabled, onTranscript, onCommand, onLive, liveActive }: {
  locale: "ko" | "en"; request: Request; disabled?: boolean;
  onTranscript: (text: string) => void; onCommand: (command: VoiceCommand) => void;
  onLive?: () => void; liveActive?: boolean;
}) {
  const t = TEXT[locale];
  const [prefs, setPrefs] = useState<VoicePrefs>(loadPrefs);
  const [catalog, setCatalog] = useState<ModelsResponse | null>(null);
  const [state, setState] = useState<"idle" | "recording" | "transcribing">("idle");
  const [seconds, setSeconds] = useState(0);
  const [message, setMessage] = useState("");
  const [panelOpen, setPanelOpen] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const pressedAt = useRef(0);
  const holdMode = useRef(false);
  const timer = useRef<number | null>(null);

  const savePrefs = (next: Partial<VoicePrefs>) => setPrefs(previous => {
    const merged = { ...previous, ...next };
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(merged)); } catch { /* storage blocked */ }
    return merged;
  });

  const loadCatalog = useCallback(async () => {
    try {
      const response = await request("/v1/audio/models");
      if (response.ok) setCatalog(await response.json());
    } catch { /* offline: keep the button usable for a later retry */ }
  }, [request]);

  useEffect(() => { void loadCatalog(); }, [loadCatalog]);
  useEffect(() => {
    if (!catalog?.models.some(model => model.status === "downloading")) return;
    const id = window.setInterval(() => void loadCatalog(), 3000);
    return () => window.clearInterval(id);
  }, [catalog, loadCatalog]);

  const ready = Boolean(catalog?.enabled && catalog.reachable) && prefs.enabled && !disabled && !liveActive;

  const transcribe = useCallback(async (blob: Blob) => {
    setState("transcribing");
    try {
      const wav = await recordingToWav(blob);
      const form = new FormData();
      form.append("file", wav, "recording.wav");
      form.append("locale", locale);
      form.append("commands", String(prefs.commands));
      if (prefs.model) form.append("model", prefs.model);
      const response = await request("/v1/audio/transcriptions", { method: "POST", body: form });
      if (!response.ok) throw new Error(String(response.status));
      const result: { text: string; command: VoiceCommand | null } = await response.json();
      if (result.command) {
        onCommand(result.command);
        setMessage(`🎙 ${result.command.phrase}`);
      } else if (result.text) {
        onTranscript(result.text);
        setMessage("");
      } else {
        setMessage(t.empty);
      }
    } catch {
      setMessage(t.failedToTranscribe);
    } finally {
      setState("idle");
    }
  }, [locale, onCommand, onTranscript, prefs.commands, prefs.model, request, t]);

  const stop = useCallback(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = null;
    if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
  }, []);

  const start = useCallback(async () => {
    if (!ready || state !== "idle") return;
    setMessage("");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    } catch {
      setMessage(t.micDenied);
      return;
    }
    chunks.current = [];
    const media = new MediaRecorder(stream);
    media.ondataavailable = event => { if (event.data.size) chunks.current.push(event.data); };
    media.onstop = () => {
      stream.getTracks().forEach(track => track.stop());
      const blob = new Blob(chunks.current, { type: media.mimeType });
      if (blob.size) void transcribe(blob); else setState("idle");
    };
    recorder.current = media;
    media.start();
    if (prefs.mode === "hold" && !holdMode.current && pressedAt.current) { media.stop(); return; } // released before the mic opened
    setState("recording");
    setSeconds(0);
    const began = Date.now();
    timer.current = window.setInterval(() => {
      const elapsed = Math.floor((Date.now() - began) / 1000);
      setSeconds(elapsed);
      if (elapsed >= MAX_SECONDS) stop();
    }, 250);
  }, [prefs.mode, ready, state, stop, t, transcribe]);

  // Pointer handling implements the three modes.
  const onPointerDown = () => {
    pressedAt.current = Date.now();
    if (prefs.mode === "hold") { holdMode.current = true; void start(); return; }
    if (state === "recording") { stop(); return; }
    if (prefs.mode === "auto") { holdMode.current = false; void start(); }
  };
  const onPointerUp = () => {
    const held = Date.now() - pressedAt.current;
    if (prefs.mode === "hold") { holdMode.current = false; stop(); return; }
    if (prefs.mode === "auto" && state === "recording" && held >= HOLD_MS) stop(); // long press in auto = hold to talk
    if (prefs.mode === "toggle" && state === "idle") void start();
  };

  // Keyboard shortcut Alt+V: tap toggles, holding records until release.
  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      if (!(event.altKey && event.code === "KeyV") || event.repeat) return;
      event.preventDefault();
      pressedAt.current = Date.now();
      if (state === "recording") stop(); else void start();
    };
    const up = (event: KeyboardEvent) => {
      if (event.code !== "KeyV" || state !== "recording") return;
      if (Date.now() - pressedAt.current >= HOLD_MS) stop();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [start, state, stop]);

  useEffect(() => () => stop(), [stop]);

  async function install(model: VoiceModel) {
    const response = await request(`/v1/admin/audio/models/${encodeURIComponent(model.id)}/download`, { method: "POST" });
    if (!response.ok) setMessage(t.failed);
    await loadCatalog();
  }

  const badge = (name: string) => name === "recommended" ? t.recommended : name === "fast" ? t.fast : name === "english" ? t.english : name;
  const sttModels = catalog?.models.filter(model => (model.task ?? "stt") === "stt") ?? [];
  const ttsModels = catalog?.models.filter(model => model.task === "tts") ?? [];
  const installed = sttModels.filter(model => model.status === "installed");
  const liveReady = Boolean(catalog?.reachable) && installed.length > 0 && ttsModels.some(model => model.status === "installed");
  const modelItem = (model: VoiceModel, selectable: boolean) => <li key={model.id}>
    <label>
      {selectable && <input type="radio" name="voice-model" disabled={model.status !== "installed"} checked={prefs.model === model.id}
        onChange={() => savePrefs({ model: model.id })}/>}
      <span className="voice-model-text">
        <span className="voice-model-name">{model.name}{model.badges.map(name => <span className={`voice-badge ${name}`} key={name}>{badge(name)}</span>)}</span>
        <small>{model.size_mb} MB · {model.languages.join(", ")}</small>
      </span>
    </label>
    {model.status === "installed" ? null
      : model.status === "downloading" ? <small>{t.installing}{model.progress != null ? ` ${Math.round(model.progress * 100)}%` : ""}</small>
      : catalog?.can_manage ? <button type="button" className="ghost small" onClick={() => void install(model)} aria-label={`${t.install} ${model.name}`}>
          <Download size={13}/>{model.status === "failed" ? t.failed : t.install}</button>
      : <small>{t.notInstalled}</small>}
  </li>;
  const title = !catalog?.enabled ? t.unavailable : !catalog.reachable ? t.unreachable
    : state === "recording" ? t.stop : t.start;

  return (
    <div className="voice-input">
      <button type="button" className={`voice-mic ${state}`} disabled={!ready || state === "transcribing"}
        aria-label={title} title={title} aria-pressed={state === "recording"}
        onPointerDown={onPointerDown} onPointerUp={onPointerUp}
        onKeyDown={(event: ReactKeyboardEvent) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (state === "recording") stop(); else void start(); } }}>
        {state === "transcribing" ? <Loader2 size={17} className="spin"/> : state === "recording" ? <Square size={15}/> : ready ? <Mic size={17}/> : <MicOff size={17}/>}
      </button>
      {onLive && <button type="button" className={`voice-live ${liveActive ? "active" : ""}`} disabled={!liveReady || liveActive || disabled}
        aria-label={liveReady ? t.live : t.liveNeedsTts} title={liveReady ? t.live : t.liveNeedsTts} onClick={onLive}>
        <AudioLines size={17}/>
      </button>}
      <button type="button" className="voice-settings-toggle" aria-label={t.settings} title={t.settings}
        aria-expanded={panelOpen} onClick={() => { setPanelOpen(open => !open); void loadCatalog(); }}>
        <Settings2 size={14}/>
      </button>
      <span className="voice-status" role="status" aria-live="polite">
        {state === "recording" ? t.recording(seconds) : state === "transcribing" ? t.transcribing : message}
      </span>

      {panelOpen && <div className="voice-panel" role="dialog" aria-label={t.title}>
        <h4>{t.title}</h4>
        <label className="voice-row">
          <span><strong>{t.enable}</strong><small>{t.enableHint}</small></span>
          <input type="checkbox" role="switch" checked={prefs.enabled} onChange={event => savePrefs({ enabled: event.target.checked })}/>
        </label>
        <div className="voice-row column">
          <strong>{t.mode}</strong>
          <div className="voice-segment" role="radiogroup" aria-label={t.mode}>
            {(["auto", "hold", "toggle"] as VoiceMode[]).map(mode => <button type="button" role="radio" aria-checked={prefs.mode === mode}
              key={mode} className={prefs.mode === mode ? "active" : ""} onClick={() => savePrefs({ mode })}>{t.modes[mode]}</button>)}
          </div>
          <small>{t.modeHelp}</small>
        </div>
        <label className="voice-row">
          <span><strong>{t.commands}</strong><small>{t.commandsHint}</small></span>
          <input type="checkbox" role="switch" checked={prefs.commands} onChange={event => savePrefs({ commands: event.target.checked })}/>
        </label>
        <div className="voice-row column">
          <strong>{t.model}</strong>
          {!catalog?.enabled && <small>{t.unavailable}</small>}
          {catalog?.enabled && !catalog.reachable && <small>{t.unreachable}</small>}
          {catalog?.reachable && <ul className="voice-models">
            <li>
              <label><input type="radio" name="voice-model" checked={!prefs.model} onChange={() => savePrefs({ model: "" })}/>
                {t.serverDefault}{catalog.default ? ` (${catalog.default})` : ""}</label>
            </li>
            {sttModels.map(model => modelItem(model, true))}
          </ul>}
          {catalog?.reachable && !catalog.can_manage && installed.length === 0 && <small>{t.adminOnly}</small>}
        </div>
        {catalog?.reachable && ttsModels.length > 0 && <div className="voice-row column">
          <strong>{t.speechModels}</strong>
          <ul className="voice-models">{ttsModels.map(model => modelItem(model, false))}</ul>
          {!liveReady && <small>{t.liveNeedsTts}</small>}
        </div>}
        <p className="voice-privacy">{t.privacy}</p>
      </div>}
    </div>
  );
}
