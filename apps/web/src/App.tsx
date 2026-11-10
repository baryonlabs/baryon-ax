import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Bot, ChevronRight, FileUp, Globe2, RefreshCw, Save, Send, Settings, ShieldCheck, Sparkles, Trash2, X } from "lucide-react";

export type Locale = "ko" | "en";
type Role = "member" | "admin";
type View = "chat" | "knowledge" | "governance" | "settings";
type ChatMessage = { role: "user" | "assistant"; content: string; citations?: Citation[] };
type Citation = { document_id: string; title: string; chunk: number };
type KnowledgeDocument = { id: string; title: string; owner_id: string; allowed_roles: string[]; created_at: string; chunks: number };
type AuditEvent = {
  id: string; at: string; type: string; trace_id: string; organization_id: string; user_id: string;
  model?: string; latency_ms?: number; reason?: string; [key: string]: unknown;
};
type AdminSettings = {
  version: string; organization_id: string;
  model: { name: string; adapter: string; base_url_configured: boolean; timeout_seconds: number };
  storage: { documents: string; audit: string; max_document_bytes: number };
  policy: { blocked_patterns: number; pii_masking: boolean };
  retrieval: { mode: string; result_limit: number };
  counts: { documents: number; audit_events: number };
};
const API = "/api";
const CURRENT_USER = "local-user";
const CURRENT_ORG = "local-org";

function identity(role: Role): Record<string, string> {
  return { "X-User-Id": CURRENT_USER, "X-Organization-Id": CURRENT_ORG, "X-Roles": role };
}

function formatBytes(value: number): string {
  return value >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)} MB` : `${Math.round(value / 1000)} KB`;
}

const copy = {
  ko: {
    welcome: "Baryon AX를 시작할 준비가 되었습니다. 질문을 입력하거나 Markdown 문서를 업로드해 근거 기반 대화를 시작하세요.",
    indexed: (title: string, chunks: number) => `${title} 문서를 ${chunks}개 청크로 색인했습니다. 이제 권한 범위 안에서 검색할 수 있습니다.`,
    uploadFailed: "문서를 업로드할 수 없습니다. UTF-8 Markdown 또는 텍스트 파일인지 확인해 주세요.",
    failed: "요청을 완료할 수 없습니다. API가 실행 중인지 확인해 주세요.",
    newChat: "+ 새 대화", chat: "채팅", knowledge: "지식", governance: "거버넌스", settings: "설정", workspace: "AI 작업 공간",
    groundedChat: "근거 기반 채팅", knowledgeLibrary: "지식 라이브러리", sources: "출처", ask: "무엇이든 질문하거나 업로드한 문서를 검색하세요…",
    scope: "문서는 조직 및 역할 권한 범위 안에서만 검색됩니다.", indexedDocs: "색인된 문서", visibleDocs: "내 조직과 역할에서 조회 가능한 문서만 표시됩니다.",
    sendHint: "Shift+Enter로 전송",
    upload: "문서 업로드", noDocs: "아직 문서가 없습니다", noDocsBody: "UTF-8 Markdown 또는 텍스트 파일을 업로드해 근거 기반 채팅에 활용하세요.",
    chunks: "청크", roles: "역할", deleteDocument: "문서 삭제", onPremises: "온프레미스 프로필", language: "언어", profile: "local-org",
    role: "역할", member: "멤버", admin: "관리자",
    auditLog: "감사 로그", auditBody: "조직 단위로 기록된 최근 감사 이벤트입니다. 관리자 역할만 조회할 수 있습니다.",
    adminRequired: "관리자 역할이 필요합니다", adminRequiredBody: "헤더의 역할을 관리자로 바꾸면 이 조직의 감사 이벤트를 볼 수 있습니다.",
    noEvents: "아직 감사 이벤트가 없습니다", noEventsBody: "채팅이나 문서 작업을 실행하면 여기에 감사 기록이 쌓입니다.", refresh: "새로고침",
    adminSettings: "관리자 설정", adminSettingsBody: "런타임 구성과 보안 정책, 저장소 상태를 확인하고 문서 권한을 관리합니다.",
    adminOnly: "관리자 전용", overview: "개요", runtime: "런타임", securityPolicy: "보안 정책", storage: "저장소", retrieval: "검색",
    versionLabel: "버전", modelLabel: "모델", adapterLabel: "어댑터", endpointLabel: "모델 엔드포인트", configured: "설정됨",
    notConfigured: "demo-local (미설정)", timeoutLabel: "타임아웃", documentCount: "문서", auditEventCount: "감사 이벤트",
    documentStore: "문서 저장소", auditStore: "감사 저장소", maxUpload: "최대 업로드", blockedPatterns: "차단 패턴",
    phoneMasking: "전화번호 마스킹", enabled: "사용", disabled: "사용 안 함", lexicalMode: "어휘 검색", resultLimit: "결과 수",
    localJson: "로컬 JSON 파일", jsonl: "JSONL (append-only)", organizationLabel: "조직",
    editRoles: "권한 편집", allowedRoles: "허용 역할", save: "저장", cancel: "취소",
    aclUpdated: "문서 권한을 저장했습니다.", aclFailed: "문서 권한을 저장할 수 없습니다.",
    settingsFailed: "관리자 설정을 불러올 수 없습니다.", ownerLabel: "소유자",
  },
  en: {
    welcome: "Baryon AX is ready. Ask a question or upload a Markdown document to ground the conversation.",
    indexed: (title: string, chunks: number) => `Indexed ${title} into ${chunks} chunk(s). It is now available for permission-scoped retrieval.`,
    uploadFailed: "The document could not be uploaded. Check that it is a UTF-8 Markdown or text file.",
    failed: "The request could not be completed. Check that the API is running.",
    newChat: "+ New conversation", chat: "Chat", knowledge: "Knowledge", governance: "Governance", settings: "Settings", workspace: "AI WORKSPACE",
    groundedChat: "Grounded chat", knowledgeLibrary: "Knowledge library", sources: "Sources", ask: "Ask anything, or query an uploaded document…",
    scope: "Documents are searched only within your organization and role scope.", indexedDocs: "Indexed documents", visibleDocs: "Only documents visible to your organization and role are shown.",
    sendHint: "Shift+Enter to send",
    upload: "Upload document", noDocs: "No documents yet", noDocsBody: "Upload a UTF-8 Markdown or text file to make it available for grounded chat.",
    chunks: "chunk(s)", roles: "roles", deleteDocument: "Delete document", onPremises: "On-premises profile", language: "Language", profile: "local-org",
    role: "Role", member: "Member", admin: "Administrator",
    auditLog: "Audit log", auditBody: "Recent audit events recorded for your organization. Administrator role required.",
    adminRequired: "Administrator role required", adminRequiredBody: "Switch the header role to Administrator to view this organization's audit events.",
    noEvents: "No audit events yet", noEventsBody: "Chat and document activity will appear here as audit records.", refresh: "Refresh",
    adminSettings: "Administrator settings", adminSettingsBody: "Inspect runtime configuration, security policy, and storage, and manage document access.",
    adminOnly: "Administrator only", overview: "Overview", runtime: "Runtime", securityPolicy: "Security policy", storage: "Storage", retrieval: "Retrieval",
    versionLabel: "Version", modelLabel: "Model", adapterLabel: "Adapter", endpointLabel: "Model endpoint", configured: "Configured",
    notConfigured: "demo-local (unconfigured)", timeoutLabel: "Timeout", documentCount: "Documents", auditEventCount: "Audit events",
    documentStore: "Document store", auditStore: "Audit store", maxUpload: "Max upload", blockedPatterns: "Blocked patterns",
    phoneMasking: "Phone masking", enabled: "Enabled", disabled: "Disabled", lexicalMode: "Lexical", resultLimit: "Result limit",
    localJson: "Local JSON files", jsonl: "JSONL (append-only)", organizationLabel: "Organization",
    editRoles: "Edit roles", allowedRoles: "Allowed roles", save: "Save", cancel: "Cancel",
    aclUpdated: "Document roles saved.", aclFailed: "Could not save document roles.",
    settingsFailed: "Could not load administrator settings.", ownerLabel: "Owner",
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
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [settingsError, setSettingsError] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [roleDraft, setRoleDraft] = useState("");
  const [aclMessage, setAclMessage] = useState<{ id: string; ok: boolean } | null>(null);
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

  const loadSettings = useCallback(async () => {
    if (role !== "admin") {
      setSettings(null);
      setSettingsError(true);
      return;
    }
    try {
      const response = await fetch(`${API}/v1/admin/settings`, { headers: identity(role) });
      if (!response.ok) {
        setSettings(null);
        setSettingsError(true);
        return;
      }
      setSettings(await response.json());
      setSettingsError(false);
    } catch {
      setSettings(null);
      setSettingsError(true);
    }
  }, [role]);

  useEffect(() => { if (view === "knowledge") void loadKnowledge(); }, [view, loadKnowledge]);
  useEffect(() => { if (view === "governance") void loadAudit(); }, [view, loadAudit]);
  useEffect(() => { if (view === "settings") void loadSettings(); }, [view, loadSettings]);
  useEffect(() => {
    if (role !== "admin") {
      setEditingId(null);
      setAclMessage(null);
      if (view === "settings") setView("chat");
    }
  }, [role, view]);

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
    if (response.ok) {
      setKnowledge(previous => previous.filter(document => document.id !== id));
      if (editingId === id) setEditingId(null);
    }
  }

  function startEditingRoles(document: KnowledgeDocument) {
    setEditingId(document.id);
    setRoleDraft(document.allowed_roles.join(", "));
    setAclMessage(null);
  }

  async function saveRoles(id: string) {
    const allowed_roles = roleDraft.split(",").map(item => item.trim()).filter(Boolean);
    try {
      const response = await fetch(`${API}/v1/documents/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...identity(role) },
        body: JSON.stringify({ allowed_roles }),
      });
      if (!response.ok) throw new Error("Document ACL update failed");
      setEditingId(null);
      setAclMessage({ id, ok: true });
      await loadKnowledge();
    } catch {
      setAclMessage({ id, ok: false });
    }
  }

  function composerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || !event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!busy && input.trim()) event.currentTarget.form?.requestSubmit();
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
    if (view === "settings") return t.adminSettings;
    return t.groundedChat;
  }

  return <main className={`shell ${role === "admin" ? "admin" : ""}`}>
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Sparkles size={17}/></div><span>Baryon AX</span></div>
      <button className="new-chat" onClick={() => { setView("chat"); setMessages([{ role: "assistant", content: t.welcome }]); }}>{t.newChat}</button>
      <nav>
        <button className={view === "chat" ? "active" : ""} onClick={() => setView("chat")}><Bot size={16}/>{t.chat}</button>
        <button className={view === "knowledge" ? "active" : ""} onClick={() => setView("knowledge")}><BookOpen size={16}/>{t.knowledge}</button>
        <button className={view === "governance" ? "active" : ""} onClick={() => setView("governance")}><ShieldCheck size={16}/>{t.governance}</button>
        {role === "admin" && <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}><Settings size={16}/>{t.settings}</button>}
      </nav>
      <div className={`environment ${role === "admin" ? "admin" : ""}`}>
        <span className="dot"/>{t.onPremises}<br/>
        <small>{t.profile} · {t[role]}</small>
        {role === "admin" && <span className="admin-badge">ADMIN</span>}
      </div>
    </aside>
    <section className="workspace">
      <header>
        <div><p className="eyebrow">{t.workspace}</p><h1>{title()}</h1></div>
        <div className="header-actions">
          <label className="locale-select role-select"><ShieldCheck size={15}/><span className="sr-only">{t.role}</span><select aria-label={t.role} value={role} onChange={event => setRole(event.target.value as Role)}><option value="member">{t.member}</option><option value="admin">{t.admin}</option></select></label>
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
          <form onSubmit={send}><textarea value={input} onChange={event => setInput(event.target.value)} onKeyDown={composerKeyDown} placeholder={t.ask} rows={1}/><button aria-label={t.chat} aria-keyshortcuts="Shift+Enter" disabled={busy || !input.trim()}><Send size={17}/></button></form>
          <p>{t.scope} · {t.sendHint}</p>
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
            <div className="document-main">
              <strong>{document.title}</strong>
              <p>{document.chunks} {t.chunks} · {t.roles}: {document.allowed_roles.join(", ") || "—"} · {t.ownerLabel}: {document.owner_id}</p>
              {role === "admin" && editingId === document.id && <div className="acl-editor">
                <input aria-label={t.allowedRoles} value={roleDraft} onChange={event => setRoleDraft(event.target.value)} placeholder="member, admin"/>
                <button className="primary small" onClick={() => void saveRoles(document.id)}><Save size={14}/>{t.save}</button>
                <button className="ghost small" onClick={() => setEditingId(null)}><X size={14}/>{t.cancel}</button>
              </div>}
              {role === "admin" && editingId !== document.id && <button className="acl" onClick={() => startEditingRoles(document)}>{t.editRoles}</button>}
              {aclMessage?.id === document.id && <small className={aclMessage.ok ? "acl-ok" : "acl-error"}>{aclMessage.ok ? t.aclUpdated : t.aclFailed}</small>}
            </div>
            {(role === "admin" || document.owner_id === CURRENT_USER) && <button className="delete" title={t.deleteDocument} aria-label={t.deleteDocument} onClick={() => removeDocument(document.id)}><Trash2 size={16}/></button>}
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
      {view === "settings" && <div className="knowledge">
        <div className="knowledge-top">
          <div><h2>{t.adminSettings}</h2><p>{t.adminSettingsBody}</p></div>
          <button className="primary" onClick={() => void loadSettings()}><RefreshCw size={16}/>{t.refresh}</button>
        </div>
        {settingsError || !settings ? <div className="empty"><Settings size={28}/><h3>{t.adminOnly}</h3><p>{t.settingsFailed}</p></div> : <div className="settings-grid">
          <section className="setting-card">
            <h3>{t.overview}</h3>
            <dl>
              <div><dt>{t.versionLabel}</dt><dd>{settings.version}</dd></div>
              <div><dt>{t.adapterLabel}</dt><dd>{settings.model.adapter}</dd></div>
              <div><dt>{t.documentCount}</dt><dd>{settings.counts.documents}</dd></div>
              <div><dt>{t.auditEventCount}</dt><dd>{settings.counts.audit_events}</dd></div>
            </dl>
          </section>
          <section className="setting-card">
            <h3>{t.runtime}</h3>
            <dl>
              <div><dt>{t.modelLabel}</dt><dd>{settings.model.name}</dd></div>
              <div><dt>{t.endpointLabel}</dt><dd>{settings.model.base_url_configured ? t.configured : t.notConfigured}</dd></div>
              <div><dt>{t.timeoutLabel}</dt><dd>{settings.model.timeout_seconds}s</dd></div>
              <div><dt>{t.maxUpload}</dt><dd>{formatBytes(settings.storage.max_document_bytes)}</dd></div>
            </dl>
          </section>
          <section className="setting-card">
            <h3>{t.securityPolicy}</h3>
            <dl>
              <div><dt>{t.blockedPatterns}</dt><dd>{settings.policy.blocked_patterns}</dd></div>
              <div><dt>{t.phoneMasking}</dt><dd>{settings.policy.pii_masking ? t.enabled : t.disabled}</dd></div>
              <div><dt>{t.retrieval}</dt><dd>{settings.retrieval.mode === "lexical" ? t.lexicalMode : settings.retrieval.mode}</dd></div>
              <div><dt>{t.resultLimit}</dt><dd>{settings.retrieval.result_limit}</dd></div>
            </dl>
          </section>
          <section className="setting-card">
            <h3>{t.storage}</h3>
            <dl>
              <div><dt>{t.documentStore}</dt><dd>{settings.storage.documents === "local-json" ? t.localJson : settings.storage.documents}</dd></div>
              <div><dt>{t.auditStore}</dt><dd>{settings.storage.audit === "jsonl" ? t.jsonl : settings.storage.audit}</dd></div>
              <div><dt>{t.organizationLabel}</dt><dd>{settings.organization_id}</dd></div>
            </dl>
          </section>
        </div>}
      </div>}
    </section>
  </main>;
}
