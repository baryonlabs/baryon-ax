import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Bot, ChevronRight, FileUp, Globe2, RefreshCw, Send, ShieldCheck, Sparkles, Trash2 } from "lucide-react";

export type Locale = "ko" | "en";
type Role = "member" | "admin";
type View = "chat" | "knowledge" | "governance";
type ChatMessage = { role: "user" | "assistant"; content: string; citations?: Citation[] };
type Citation = { document_id: string; title: string; chunk: number };
type KnowledgeDocument = { id: string; title: string; owner_id: string; allowed_roles: string[]; created_at: string; chunks: number };
type AuditEvent = {
  id: string; at: string; type: string; trace_id: string; organization_id: string; user_id: string;
  model?: string; latency_ms?: number; reason?: string; [key: string]: unknown;
};
const API = "/api";

function identity(role: Role): Record<string, string> {
  return { "X-User-Id": "local-user", "X-Organization-Id": "local-org", "X-Roles": role };
}

const copy = {
  ko: {
    welcome: "Baryon AX를 시작할 준비가 되었습니다. 질문을 입력하거나 Markdown 문서를 업로드해 근거 기반 대화를 시작하세요.",
    indexed: (title: string, chunks: number) => `${title} 문서를 ${chunks}개 청크로 색인했습니다. 이제 권한 범위 안에서 검색할 수 있습니다.`,
    uploadFailed: "문서를 업로드할 수 없습니다. UTF-8 Markdown 또는 텍스트 파일인지 확인해 주세요.",
    failed: "요청을 완료할 수 없습니다. API가 실행 중인지 확인해 주세요.",
    newChat: "+ 새 대화", chat: "채팅", knowledge: "지식", governance: "거버넌스", workspace: "AI 작업 공간",
    groundedChat: "근거 기반 채팅", knowledgeLibrary: "지식 라이브러리", sources: "출처", ask: "무엇이든 질문하거나 업로드한 문서를 검색하세요…",
    scope: "문서는 조직 및 역할 권한 범위 안에서만 검색됩니다.", indexedDocs: "색인된 문서", visibleDocs: "내 조직과 역할에서 조회 가능한 문서만 표시됩니다.",
    upload: "문서 업로드", noDocs: "아직 문서가 없습니다", noDocsBody: "UTF-8 Markdown 또는 텍스트 파일을 업로드해 근거 기반 채팅에 활용하세요.",
    chunks: "청크", roles: "역할", deleteDocument: "문서 삭제", onPremises: "온프레미스 프로필", language: "언어", profile: "local-org",
    role: "역할", member: "멤버", admin: "관리자",
    auditLog: "감사 로그", auditBody: "조직 단위로 기록된 최근 감사 이벤트입니다. 관리자 역할만 조회할 수 있습니다.",
    adminRequired: "관리자 역할이 필요합니다", adminRequiredBody: "헤더의 역할을 관리자로 바꾸면 이 조직의 감사 이벤트를 볼 수 있습니다.",
    noEvents: "아직 감사 이벤트가 없습니다", noEventsBody: "채팅이나 문서 작업을 실행하면 여기에 감사 기록이 쌓입니다.", refresh: "새로고침",
  },
  en: {
    welcome: "Baryon AX is ready. Ask a question or upload a Markdown document to ground the conversation.",
    indexed: (title: string, chunks: number) => `Indexed ${title} into ${chunks} chunk(s). It is now available for permission-scoped retrieval.`,
    uploadFailed: "The document could not be uploaded. Check that it is a UTF-8 Markdown or text file.",
    failed: "The request could not be completed. Check that the API is running.",
    newChat: "+ New conversation", chat: "Chat", knowledge: "Knowledge", governance: "Governance", workspace: "AI WORKSPACE",
    groundedChat: "Grounded chat", knowledgeLibrary: "Knowledge library", sources: "Sources", ask: "Ask anything, or query an uploaded document…",
    scope: "Documents are searched only within your organization and role scope.", indexedDocs: "Indexed documents", visibleDocs: "Only documents visible to your organization and role are shown.",
    upload: "Upload document", noDocs: "No documents yet", noDocsBody: "Upload a UTF-8 Markdown or text file to make it available for grounded chat.",
    chunks: "chunk(s)", roles: "roles", deleteDocument: "Delete document", onPremises: "On-premises profile", language: "Language", profile: "local-org",
    role: "Role", member: "Member", admin: "Administrator",
    auditLog: "Audit log", auditBody: "Recent audit events recorded for your organization. Administrator role required.",
    adminRequired: "Administrator role required", adminRequiredBody: "Switch the header role to Administrator to view this organization's audit events.",
    noEvents: "No audit events yet", noEventsBody: "Chat and document activity will appear here as audit records.", refresh: "Refresh",
  },
} as const;

function initialLocale(): Locale {
  const saved = localStorage.getItem("baryon-locale");
  if (saved === "ko" || saved === "en") return saved;
  return navigator.language.toLowerCase().startsWith("ko") ? "ko" : "en";
}

function initialRole(): Role {
  return localStorage.getItem("baryon-role") === "admin" ? "admin" : "member";
}

export default function App() {
  const [locale, setLocale] = useState<Locale>(initialLocale);
  const t = copy[locale];
  const [role, setRole] = useState<Role>(initialRole);
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: "assistant", content: t.welcome }]);
  const [input, setInput] = useState("");
  const [model, setModel] = useState("demo-local");
  const [retrieval, setRetrieval] = useState(true);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View>("chat");
  const [knowledge, setKnowledge] = useState<KnowledgeDocument[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [auditError, setAuditError] = useState<"forbidden" | "failed" | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => { fetch(`${API}/v1/models`).then(response => response.json()).then(result => result.data?.[0] && setModel(result.data[0].id)).catch(() => undefined); }, []);
  useEffect(() => { document.documentElement.lang = locale; localStorage.setItem("baryon-locale", locale); }, [locale]);
  useEffect(() => { localStorage.setItem("baryon-role", role); }, [role]);

  function changeLocale(next: Locale) {
    setLocale(next);
    setMessages(previous => previous.length === 1 && previous[0].role === "assistant" ? [{ role: "assistant", content: copy[next].welcome }] : previous);
  }

  const loadKnowledge = useCallback(async () => {
    const response = await fetch(`${API}/v1/documents`, { headers: identity(role) });
    if (response.ok) setKnowledge((await response.json()).data);
  }, [role]);

  const loadAudit = useCallback(async () => {
    if (role !== "admin") {
      setAuditEvents([]);
      setAuditError("forbidden");
      return;
    }
    try {
      const response = await fetch(`${API}/v1/audit-events?limit=50`, { headers: identity(role) });
      if (!response.ok) {
        setAuditEvents([]);
        setAuditError(response.status === 403 ? "forbidden" : "failed");
        return;
      }
      setAuditError(null);
      setAuditEvents((await response.json()).data);
    } catch {
      setAuditEvents([]);
      setAuditError("failed");
    }
  }, [role]);

  useEffect(() => { if (view === "knowledge") void loadKnowledge(); }, [view, loadKnowledge]);
  useEffect(() => { if (view === "governance") void loadAudit(); }, [view, loadAudit]);

  async function upload(file: File) {
    try {
      const data = new FormData(); data.append("file", file);
      const response = await fetch(`${API}/v1/documents`, { method: "POST", body: data, headers: identity(role) });
      if (!response.ok) throw new Error("Document upload failed");
      const result = await response.json(); await loadKnowledge();
      setMessages(previous => [...previous, { role: "assistant", content: t.indexed(result.title, result.chunks) }]);
    } catch {
      setMessages(previous => [...previous, { role: "assistant", content: t.uploadFailed }]);
    }
  }

  async function removeDocument(id: string) {
    const response = await fetch(`${API}/v1/documents/${id}`, { method: "DELETE", headers: identity(role) });
    if (response.ok) setKnowledge(previous => previous.filter(document => document.id !== id));
  }

  async function send(event: FormEvent) {
    event.preventDefault(); if (!input.trim() || busy) return;
    const question = input.trim(); setInput(""); setBusy(true);
    const next = [...messages, { role: "user" as const, content: question }];
    setMessages([...next, { role: "assistant", content: "" }]);
    try {
      const response = await fetch(`${API}/v1/chat/completions`, { method: "POST", headers: { "Content-Type": "application/json", ...identity(role) }, body: JSON.stringify({ model, messages: next, stream: true, use_retrieval: retrieval, locale }) });
      if (!response.ok || !response.body) throw new Error("Chat request failed");
      const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true }); const frames = buffer.split("\n\n"); buffer = frames.pop() || "";
        for (const frame of frames) {
          const raw = frame.replace(/^data: /, ""); if (raw === "[DONE]") continue;
          const item = JSON.parse(raw);
          if (item.type === "error") throw new Error(item.message);
          setMessages(previous => { const copied = [...previous]; const last = copied.length - 1; copied[last] = { ...copied[last], content: copied[last].content + (item.type === "token" ? item.content : ""), citations: item.type === "citations" ? item.data : copied[last].citations }; return copied; });
        }
      }
    } catch {
      setMessages(previous => {
        const copied = [...previous]; const last = copied.length - 1;
        const partial = copied[last]?.content.trim();
        copied[last] = { role: "assistant", content: partial ? `${partial}\n\n${t.failed}` : t.failed };
        return copied;
      });
    }
    finally { setBusy(false); }
  }

  function title() {
    if (view === "knowledge") return t.knowledgeLibrary;
    if (view === "governance") return t.auditLog;
    return t.groundedChat;
  }

  return <main className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Sparkles size={17}/></div><span>Baryon AX</span></div>
      <button className="new-chat" onClick={() => { setView("chat"); setMessages([{ role: "assistant", content: t.welcome }]); }}>{t.newChat}</button>
      <nav>
        <button className={view === "chat" ? "active" : ""} onClick={() => setView("chat")}><Bot size={16}/>{t.chat}</button>
        <button className={view === "knowledge" ? "active" : ""} onClick={() => setView("knowledge")}><BookOpen size={16}/>{t.knowledge}</button>
        <button className={view === "governance" ? "active" : ""} onClick={() => setView("governance")}><ShieldCheck size={16}/>{t.governance}</button>
      </nav>
      <div className="environment"><span className="dot"/>{t.onPremises}<br/><small>{t.profile} · {t[role]}</small></div>
    </aside>
    <section className="workspace">
      <header>
        <div><p className="eyebrow">{t.workspace}</p><h1>{title()}</h1></div>
        <div className="header-actions">
          <label className="locale-select"><ShieldCheck size={15}/><span className="sr-only">{t.role}</span><select aria-label={t.role} value={role} onChange={event => setRole(event.target.value as Role)}><option value="member">{t.member}</option><option value="admin">{t.admin}</option></select></label>
          <label className="locale-select"><Globe2 size={15}/><span className="sr-only">{t.language}</span><select aria-label={t.language} value={locale} onChange={event => changeLocale(event.target.value as Locale)}><option value="ko">한국어</option><option value="en">English</option></select></label>
          {view === "chat" && <><select value={model} onChange={event => setModel(event.target.value)}><option value={model}>{model}</option></select><label className="toggle"><input type="checkbox" checked={retrieval} onChange={event => setRetrieval(event.target.checked)}/><span/>RAG</label></>}
        </div>
      </header>
      {view === "chat" && <>
        <div className="conversation">
          {messages.map((message, index) => <article className={`message ${message.role}`} key={index}>
            <div className="avatar">{message.role === "user" ? "U" : <Sparkles size={15}/>}</div>
            <div className="message-body">
              <p>{message.content || <span className="cursor"/>}</p>
              {message.citations && message.citations.length > 0 && <div className="sources"><span>{t.sources}</span>{message.citations.map(citation => <button key={`${citation.document_id}-${citation.chunk}`}><BookOpen size={13}/>{citation.title}<ChevronRight size={13}/></button>)}</div>}
            </div>
          </article>)}
        </div>
        <footer>
          <input ref={fileInput} type="file" accept=".txt,.md" hidden onChange={event => event.target.files?.[0] && upload(event.target.files[0])}/>
          <button className="upload" title={t.upload} aria-label={t.upload} onClick={() => fileInput.current?.click()}><FileUp size={18}/></button>
          <form onSubmit={send}><textarea value={input} onChange={event => setInput(event.target.value)} placeholder={t.ask} rows={1}/><button aria-label={t.chat} disabled={busy || !input.trim()}><Send size={17}/></button></form>
          <p>{t.scope}</p>
        </footer>
      </>}
      {view === "knowledge" && <div className="knowledge">
        <div className="knowledge-top">
          <div><h2>{t.indexedDocs}</h2><p>{t.visibleDocs}</p></div>
          <button className="primary" onClick={() => fileInput.current?.click()}><FileUp size={16}/>{t.upload}</button>
          <input ref={fileInput} type="file" accept=".txt,.md" hidden onChange={event => event.target.files?.[0] && upload(event.target.files[0])}/>
        </div>
        {knowledge.length === 0 ? <div className="empty"><BookOpen size={28}/><h3>{t.noDocs}</h3><p>{t.noDocsBody}</p></div> : <div className="document-list">
          {knowledge.map(document => <div className="document" key={document.id}>
            <div className="document-icon"><BookOpen size={18}/></div>
            <div><strong>{document.title}</strong><p>{document.chunks} {t.chunks} · {t.roles}: {document.allowed_roles.join(", ")}</p></div>
            <button className="delete" title={t.deleteDocument} aria-label={t.deleteDocument} onClick={() => removeDocument(document.id)}><Trash2 size={16}/></button>
          </div>)}
        </div>}
      </div>}
      {view === "governance" && <div className="knowledge">
        <div className="knowledge-top">
          <div><h2>{t.auditLog}</h2><p>{t.auditBody}</p></div>
          <button className="primary" onClick={() => void loadAudit()}><RefreshCw size={16}/>{t.refresh}</button>
        </div>
        {auditError ? <div className="empty"><ShieldCheck size={28}/><h3>{auditError === "forbidden" ? t.adminRequired : t.failed}</h3><p>{auditError === "forbidden" ? t.adminRequiredBody : t.scope}</p></div> : auditEvents.length === 0 ? <div className="empty"><ShieldCheck size={28}/><h3>{t.noEvents}</h3><p>{t.noEventsBody}</p></div> : <div className="event-list">
          {auditEvents.map(event => <div className="event" key={event.id}>
            <div>
              <strong>{event.type}</strong>
              <p>{new Date(event.at).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")} · {event.user_id}{event.model ? ` · ${event.model}` : ""}{typeof event.latency_ms === "number" ? ` · ${event.latency_ms} ms` : ""}</p>
            </div>
            <code title={event.trace_id}>{event.trace_id}</code>
          </div>)}
        </div>}
      </div>}
    </section>
  </main>;
}
