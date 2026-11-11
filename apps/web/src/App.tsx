import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Bot, Boxes, Brain, ChevronRight, Cpu, ExternalLink, FileCode, FileImage, FileSpreadsheet, FileText, FileType, FileUp, Globe2, HardDrive, MessagesSquare, Network, Paperclip, Plus, RefreshCw, Save, Search, Send, Server, Settings, ShieldCheck, Sparkles, Trash2, UploadCloud, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

export type Locale = "ko" | "en";
type Role = "member" | "admin";
type View = "chat" | "knowledge" | "memory" | "governance" | "gaps" | "tacit" | "settings";
type TraceStep = { step: string; status: string; ms: number; detail?: string };
type ChatTrace = { trace_id?: string; steps: TraceStep[]; total_ms?: number | null; memory_count?: number; retrieval_count?: number };
type ChatMessage = { role: "user" | "assistant"; content: string; citations?: Citation[]; trace?: ChatTrace };
type Citation = { document_id: string; title: string; chunk: number };
type CitedChunk = { number: number; content: string; heading_path?: string | null; page?: number | null; kind?: string | null };
type DocumentDetail = {
  id: string; title: string; owner_id: string; allowed_roles: string[]; created_at: string;
  content: string; chunks_count: number; cited_chunk?: CitedChunk | null;
  mime: string; parser: string; has_raw_file: boolean; raw_filename: string; raw_size: number;
};
type SourceState = { citation: Citation; detail?: DocumentDetail; loading: boolean; error?: string };
type KnowledgeDocument = { id: string; title: string; owner_id: string; allowed_roles: string[]; created_at: string; chunks: number };
type MemoryItem = { id: string; memory: string; created_at?: string; metadata?: Record<string, unknown>; score?: number };
type AuditEvent = {
  id: string; at: string; type: string; trace_id: string; organization_id: string; user_id: string;
  model?: string; latency_ms?: number; reason?: string; [key: string]: unknown;
};
type GapItem = {
  id: string; question: string; answer_excerpt: string; reasons: string[]; citations: number;
  occurrences: number; status: string; first_seen: string; last_seen: string; terms: string[];
};
type GapSummary = { total: number; open: number; dismissed: number; converted: number; resolved: number; occurrences: number; reasons: Record<string, number> };
type GapCluster = {
  id: string; label: string; size: number; gap_ids: string[]; questions: string[]; terms: string[];
  reasons: Record<string, number>; suggested_pack_name: string; required_sources: { kind: string; hint: string }[];
};
type PackVerification = {
  verified_at: string; coverage: number; passed: number; total: number; threshold: number;
  results: { question: string; passed: boolean; citations: number; documents: string[]; missing_terms: string[] }[];
};
type OntologyPack = {
  id: string; name: string; domain: string; status: string; created_at: string;
  entities: { name: string; type: string }[]; relations: { from: string; type: string; to: string }[];
  required_sources: { kind: string; hint: string }[]; acceptance_questions: string[]; source_gap_ids: string[];
  verification?: PackVerification;
};
type TacitQuestion = {
  id: string; question: string; source: string; gap_id?: string; category?: string;
  occurrences: number; reasons: string[]; terms: string[];
};
type TacitEntry = {
  id: string; question: string; answer: string; attachments: { document_id: string; title: string }[];
  status: string; source: string; gap_id?: string | null; created_at: string; published_document_id?: string | null;
};
type ContainerInfo = { name: string; image: string; status: string; health?: string | null; project?: string; ports: string[] };
type SystemInfo = {
  host: { hostname: string; platform: string; python: string; uptime_seconds: number };
  storage: { total_bytes: number; used_bytes: number; free_bytes: number; percent: number; documents_bytes: number; audit_bytes: number; mem0_bytes: number; fastembed_bytes: number; documents_count: number };
  memory: { total_bytes: number; used_bytes: number; available_bytes: number; percent: number };
  process: { total: number; api_pid: number; api_threads: number; api_rss_bytes: number; api_cpu_percent: number };
  cpu: { cores: number; load_1: number; load_5: number; load_15: number };
  containers: { available: boolean; project: string; total: number; running: number; platform_total: number; platform_running: number; items: ContainerInfo[] };
  services: {
    database?: { available: boolean; rows?: number; size_bytes?: number; backend?: string };
    vector?: { available: boolean; points?: number; vectors?: number | null; status?: string; backend?: string };
    cache?: { available: boolean; used_memory_bytes?: number; used_memory_human?: string; keys?: number };
  };
};
type AdminSettings = {
  version: string; organization_id: string;
  model: { name: string; adapter: string; base_url_configured: boolean; timeout_seconds: number; thinking_disabled: boolean; prompt_profile: string };
  storage: { documents: string; audit: string; max_document_bytes: number };
  policy: { blocked_patterns: number; pii_masking: boolean };
  retrieval: { mode: string; result_limit: number };
  integrations: { orchestration: string; chunking: string; vector: string; database: string; cache: string; tracing: string; memory: string };
  counts: { documents: number; audit_events: number };
  system: SystemInfo;
  links: Record<string, string>;
};
const API = "/api";
const CURRENT_USER = "local-user";
const CURRENT_ORG = "local-org";

function identity(role: Role): Record<string, string> {
  return { "X-User-Id": CURRENT_USER, "X-Organization-Id": CURRENT_ORG, "X-Roles": role };
}

const UPLOAD_ACCEPT = ".txt,.md,.markdown,.mdown,.pdf,.hwp,.hwpx,.hwpml,.docx,.doc,.xlsx,.xls,.pptx,.ppt,.png,.jpg,.jpeg,.webp,.gif,.tif,.tiff,.html,.htm,.csv,.json";

type UploadGroup = "docs" | "sheets" | "text" | "images";
const UPLOAD_FORMATS: { label: string; accept: string; group: UploadGroup }[] = [
  { label: "PDF", accept: ".pdf", group: "docs" },
  { label: "HWP", accept: ".hwp", group: "docs" },
  { label: "HWPX", accept: ".hwpx", group: "docs" },
  { label: "Word", accept: ".docx,.doc", group: "docs" },
  { label: "PPT", accept: ".pptx,.ppt", group: "docs" },
  { label: "Excel", accept: ".xlsx,.xls", group: "sheets" },
  { label: "CSV", accept: ".csv", group: "sheets" },
  { label: "JSON", accept: ".json", group: "sheets" },
  { label: "Markdown", accept: ".md,.markdown", group: "text" },
  { label: "TXT", accept: ".txt", group: "text" },
  { label: "HTML", accept: ".html,.htm", group: "text" },
  { label: "PNG", accept: ".png", group: "images" },
  { label: "JPG", accept: ".jpg,.jpeg", group: "images" },
  { label: "WEBP", accept: ".webp", group: "images" },
  { label: "TIFF", accept: ".tif,.tiff", group: "images" },
  { label: "GIF", accept: ".gif", group: "images" },
];

function formatGroupIcon(group: UploadGroup) {
  if (group === "sheets") return FileSpreadsheet;
  if (group === "images") return FileImage;
  if (group === "text") return FileCode;
  return FileType;
}

function documentFormat(title: string) {
  const dot = title.lastIndexOf(".");
  const ext = dot >= 0 ? title.slice(dot).toLowerCase() : "";
  const label = ext ? ext.slice(1).toUpperCase() : "DOC";
  if ([".xlsx", ".xls", ".csv"].includes(ext)) return { label, tone: "sheet", icon: FileSpreadsheet };
  if ([".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff"].includes(ext)) return { label, tone: "image", icon: FileImage };
  if ([".md", ".markdown", ".mdown", ".txt", ".html", ".htm", ".json"].includes(ext)) return { label, tone: "text", icon: FileCode };
  if (ext === ".pdf") return { label, tone: "pdf", icon: FileText };
  if ([".hwp", ".hwpx", ".hwpml"].includes(ext)) return { label, tone: "hwp", icon: FileType };
  return { label, tone: "doc", icon: FileType };
}

function DocumentFormatIcon({ title }: { title: string }) {
  const meta = documentFormat(title);
  const Icon = meta.icon;
  return <div className={`document-icon ${meta.tone}`} title={meta.label}><Icon size={18}/></div>;
}

function formatBytes(value: number): string {
  if (value >= 1_000_000_000_000) return `${(value / 1_000_000_000_000).toFixed(2)} TB`;
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)} GB`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} MB`;
  return `${Math.round(value / 1000)} KB`;
}

function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

const copy = {
  ko: {
    welcome: "Baryon AX를 시작할 준비가 되었습니다. 질문을 입력하거나 Markdown 문서를 업로드해 근거 기반 대화를 시작하세요.",
    indexed: (title: string, chunks: number) => `${title} 문서를 ${chunks}개 청크로 색인했습니다. 이제 권한 범위 안에서 검색할 수 있습니다.`,
    uploading: "업로드 및 색인 중…", uploadZoneTitle: "파일을 끌어다 놓거나 클릭하세요",
    uploadZoneBody: "문서를 파싱·청킹·임베딩해 채팅 근거로 바로 사용합니다.", formats: "지원 형식",
    formatDocs: "문서", formatSheets: "데이터", formatText: "텍스트", formatImages: "이미지",
    formatHint: "형식 배지를 누르면 해당 파일만 골라서 선택할 수 있습니다.",
    noSources: "이 답변에는 문서 출처가 없습니다.", sourceViewer: "출처 원문", citedSection: "답변에 사용된 부분",
    originalText: "문서 원문", openOriginal: "원본 파일 열기", downloadOriginal: "원본 다운로드", downloadText: "텍스트 저장",
    rawMissing: "이 문서는 원본 파일이 저장되지 않았습니다. (이전 업로드)", sourceLoading: "원문을 불러오는 중…",
    sourceFailed: "원문을 불러올 수 없습니다.", rawFailed: "원본 파일을 열 수 없습니다.", close: "닫기",
    uploadFailed: "문서를 업로드할 수 없습니다. UTF-8 Markdown 또는 텍스트 파일인지 확인해 주세요.",
    failed: "요청을 완료할 수 없습니다. API가 실행 중인지 확인해 주세요.",
    newChat: "+ 새 대화", chat: "채팅", knowledge: "지식", memory: "메모리", governance: "거버넌스", settings: "설정", workspace: "AI 작업 공간",
    groundedChat: "근거 기반 채팅", knowledgeLibrary: "지식 라이브러리", sources: "출처", ask: "무엇이든 질문하거나 업로드한 문서를 검색하세요…",
    traceTitle: "처리 과정", traceRateLimit: "속도 제한", tracePolicy: "정책 검사", traceMemory: "메모리 검색", traceRetrieval: "문서 검색", traceModel: "모델 생성", traceAudit: "감사 기록", traceMs: "ms", traceSkipped: "건너뜀",
    scope: "문서는 조직 및 역할 권한 범위 안에서만 검색됩니다.", indexedDocs: "색인된 문서", visibleDocs: "내 조직과 역할에서 조회 가능한 문서만 표시됩니다.",
    sendHint: "Shift+Enter로 전송",
    upload: "문서 업로드", noDocs: "아직 문서가 없습니다", noDocsBody: "PDF·HWP·Office·이미지·Markdown을 올리면 자동으로 파싱·색인되어 근거 기반 채팅에 사용됩니다.",
    chunks: "청크", roles: "역할", deleteDocument: "문서 삭제", onPremises: "온프레미스 프로필", language: "언어", profile: "local-org",
    role: "역할", member: "멤버", admin: "관리자",
    auditLog: "감사 로그", auditBody: "조직 단위로 기록된 최근 감사 이벤트입니다. 관리자 역할만 조회할 수 있습니다.",
    memoryBody: "Mem0에 사용자 선호와 중요한 사실을 저장하고 검색합니다.",
    memoryPlaceholder: "기억할 내용을 입력하세요…", saveMemory: "메모리 저장", searchMemory: "메모리 검색", search: "검색",
    noMemories: "저장된 메모리가 없습니다", noMemoriesBody: "선호나 중요한 사실을 저장해 보세요.", deleteMemory: "메모리 삭제",
    memoryDisabled: "메모리 기능이 비활성화되어 있습니다", memoryDisabledBody: "MEM0_ENABLED, Qdrant, Ollama 설정을 확인하세요.", saving: "저장 중…",
    adminRequired: "관리자 역할이 필요합니다", adminRequiredBody: "헤더의 역할을 관리자로 바꾸면 이 조직의 감사 이벤트를 볼 수 있습니다.",
    noEvents: "아직 감사 이벤트가 없습니다", noEventsBody: "채팅이나 문서 작업을 실행하면 여기에 감사 기록이 쌓입니다.", refresh: "새로고침",
    adminSettings: "관리자 설정", adminSettingsBody: "런타임 구성과 보안 정책, 저장소 상태를 확인하고 문서 권한을 관리합니다.",
    adminOnly: "관리자 전용", overview: "개요", runtime: "런타임", securityPolicy: "보안 정책", storage: "저장소", retrieval: "검색",
    versionLabel: "버전", modelLabel: "모델", adapterLabel: "어댑터", endpointLabel: "모델 엔드포인트", configured: "설정됨",
    notConfigured: "demo-local (미설정)", timeoutLabel: "타임아웃", documentCount: "문서", auditEventCount: "감사 이벤트",
    documentStore: "문서 저장소", auditStore: "감사 저장소", maxUpload: "최대 업로드", blockedPatterns: "차단 패턴",
    phoneMasking: "전화번호 마스킹", enabled: "사용", disabled: "사용 안 함", lexicalMode: "어휘 검색", vectorMode: "Qdrant + FastEmbed", resultLimit: "결과 수",
    integrations: "통합 구성", orchestration: "오케스트레이션", chunking: "청킹", vectorStore: "벡터 저장소", database: "데이터베이스", cache: "캐시", tracing: "트레이싱",
    localJson: "로컬 JSON 파일", jsonl: "JSONL (append-only)", organizationLabel: "조직",
    editRoles: "권한 편집", allowedRoles: "허용 역할", save: "저장", cancel: "취소",
    aclUpdated: "문서 권한을 저장했습니다.", aclFailed: "문서 권한을 저장할 수 없습니다.",
    settingsFailed: "관리자 설정을 불러올 수 없습니다.", ownerLabel: "소유자",
    host: "호스트", systemStatus: "시스템 상태", storageUsage: "저장공간", memoryUsage: "메모리", processCount: "프로세스", containers: "컨테이너",
    modelProvider: "모델 프로바이더", adminLinks: "관리자 링크", quickLinks: "빠른 이동", uptime: "가동 시간", apiProcess: "API 프로세스",
    threads: "스레드", cpuLoad: "CPU 부하", cores: "코어", documentsSize: "문서", auditSize: "감사 로그", mem0Size: "Mem0", fastembedSize: "FastEmbed",
    documentsCount: "문서 수", total: "전체", free: "여유", memoryAvailable: "여유 메모리", dockerUnavailable: "Docker 소켓에 연결할 수 없습니다.",
    platformContainers: "플랫폼", allContainers: "전체", open: "열기", docs: "문서", noLink: "링크 없음",
    providerAdapter: "어댑터", providerModel: "모델", providerEndpoint: "엔드포인트", thinking: "Thinking", promptProfile: "프롬프트",
    modelServer: "모델 서버", pgadmin: "pgAdmin", redisInsight: "Redis Insight", qdrant: "Qdrant", langfuse: "Langfuse",
    connected: "연결됨", notConnected: "미연결", dbSize: "DB 크기", auditRows: "감사 행", vectorPoints: "벡터 포인트", cacheKeys: "캐시 키", services: "서비스",
    reindex: "벡터 재색인", reindexing: "재색인 중…", reindexed: (count: number) => `${count}개 문서를 Qdrant에 재색인했습니다.`, reindexFailed: "벡터 재색인에 실패했습니다.",
    gaps: "지식 공백", gapsBody: "근거를 찾지 못했거나 답변이 모호했던 질문을 모아 필요한 데이터와 온톨로지 꾸러미를 제안합니다.",
    openGaps: "미해결 질문", clusters: "제안 클러스터", packs: "온톨로지 꾸러미", occurrences: "발생", citationsLabel: "출처",
    dismiss: "제외", createPack: "온톨로지 꾸러미 만들기", packCreated: (name: string) => `${name} 초안을 생성했습니다.`,
    noGaps: "아직 지식 공백이 없습니다", noGapsBody: "근거를 찾지 못한 질문이 생기면 여기에 쌓입니다.",
    noPacks: "아직 꾸러미가 없습니다", noPacksBody: "클러스터에서 꾸러미를 만들면 초안이 저장됩니다.",
    entities: "엔티티", relations: "관계", generatedFrom: (count: number) => `질문 ${count}건에서 생성`,
    gapCaptureFailed: "지식 공백을 불러올 수 없습니다.", packFailed: "온톨로지 꾸러미를 만들 수 없습니다.", statusDraft: "초안",
    approvePack: "승인", verifyPack: "재검증", verifying: "검증 중…", coverage: "커버리지", resolved: "해결",
    statusReviewed: "검토됨", statusActive: "활성", packVerified: (name: string, coverage: number) => `${name} 커버리지 ${Math.round(coverage * 100)}%`,
    verifyFailed: "재검증에 실패했습니다.", missingTerms: "부족한 용어",
    tacit: "암묵지", tacitBody: "문서에 없는 판단 기준·예외·용어를 질문으로 끌어내고, 답변을 검색 가능한 지식으로 게시합니다.",
    tacitQuestions: "질문", tacitMyAnswers: "내 답변", tacitAllAnswers: "전체 답변", answerThis: "답하기",
    tacitPlaceholder: "경험에서 나온 판단 기준과 이유를 적어 주세요…", attach: "첨부", submitAnswer: "답변 저장",
    tacitSaved: "답변을 저장했습니다.", publishAnswer: "지식으로 게시", tacitDraft: "작성됨", tacitPublished: "게시됨",
    tacitPublishedMessage: (title: string) => `${title} 문서로 게시했습니다. 이제 검색과 답변에 사용됩니다.`,
    tacitFailed: "답변을 저장할 수 없습니다.", noTacit: "아직 답변이 없습니다",
    noTacitBody: "질문에 답하면 여기에 쌓이고, 게시하면 검색 가능한 지식이 됩니다.",
    fromGap: "미해결 질문", fromTemplate: "템플릿", scopeMine: "내 답변", scopeAll: "전체 답변",
  },
  en: {
    welcome: "Baryon AX is ready. Ask a question or upload a Markdown document to ground the conversation.",
    indexed: (title: string, chunks: number) => `Indexed ${title} into ${chunks} chunk(s). It is now available for permission-scoped retrieval.`,
    uploading: "Uploading and indexing…", uploadZoneTitle: "Drop a file here or click to browse",
    uploadZoneBody: "Documents are parsed, chunked, and embedded so chat can cite them immediately.", formats: "Supported formats",
    formatDocs: "Documents", formatSheets: "Data", formatText: "Text", formatImages: "Images",
    formatHint: "Click a format badge to filter the file picker to that type.",
    noSources: "This answer has no document sources.", sourceViewer: "Source viewer", citedSection: "Cited passage",
    originalText: "Original text", openOriginal: "Open original file", downloadOriginal: "Download original", downloadText: "Save text",
    rawMissing: "The original file was not stored for this document (older upload).", sourceLoading: "Loading the source…",
    sourceFailed: "Could not load the source.", rawFailed: "Could not open the original file.", close: "Close",
    uploadFailed: "The document could not be uploaded. Check that it is a UTF-8 Markdown or text file.",
    failed: "The request could not be completed. Check that the API is running.",
    newChat: "+ New conversation", chat: "Chat", knowledge: "Knowledge", memory: "Memory", governance: "Governance", settings: "Settings", workspace: "AI WORKSPACE",
    groundedChat: "Grounded chat", knowledgeLibrary: "Knowledge library", sources: "Sources", ask: "Ask anything, or query an uploaded document…",
    traceTitle: "Pipeline", traceRateLimit: "Rate limit", tracePolicy: "Policy check", traceMemory: "Memory search", traceRetrieval: "Document search", traceModel: "Model generation", traceAudit: "Audit log", traceMs: "ms", traceSkipped: "skipped",
    scope: "Documents are searched only within your organization and role scope.", indexedDocs: "Indexed documents", visibleDocs: "Only documents visible to your organization and role are shown.",
    sendHint: "Shift+Enter to send",
    upload: "Upload document", noDocs: "No documents yet", noDocsBody: "Upload PDF, HWP, Office, image, or Markdown files; they are parsed and indexed for grounded chat.",
    chunks: "chunk(s)", roles: "roles", deleteDocument: "Delete document", onPremises: "On-premises profile", language: "Language", profile: "local-org",
    role: "Role", member: "Member", admin: "Administrator",
    auditLog: "Audit log", auditBody: "Recent audit events recorded for your organization. Administrator role required.",
    memoryBody: "Store and search user preferences and facts with Mem0.",
    memoryPlaceholder: "What should I remember?", saveMemory: "Save memory", searchMemory: "Search memories", search: "Search",
    noMemories: "No memories yet", noMemoriesBody: "Save a preference or an important fact.", deleteMemory: "Delete memory",
    memoryDisabled: "Memory is disabled", memoryDisabledBody: "Check MEM0_ENABLED, Qdrant, and Ollama configuration.", saving: "Saving…",
    adminRequired: "Administrator role required", adminRequiredBody: "Switch the header role to Administrator to view this organization's audit events.",
    noEvents: "No audit events yet", noEventsBody: "Chat and document activity will appear here as audit records.", refresh: "Refresh",
    adminSettings: "Administrator settings", adminSettingsBody: "Inspect runtime configuration, security policy, and storage, and manage document access.",
    adminOnly: "Administrator only", overview: "Overview", runtime: "Runtime", securityPolicy: "Security policy", storage: "Storage", retrieval: "Retrieval",
    versionLabel: "Version", modelLabel: "Model", adapterLabel: "Adapter", endpointLabel: "Model endpoint", configured: "Configured",
    notConfigured: "demo-local (unconfigured)", timeoutLabel: "Timeout", documentCount: "Documents", auditEventCount: "Audit events",
    documentStore: "Document store", auditStore: "Audit store", maxUpload: "Max upload", blockedPatterns: "Blocked patterns",
    phoneMasking: "Phone masking", enabled: "Enabled", disabled: "Disabled", lexicalMode: "Lexical", vectorMode: "Qdrant + FastEmbed", resultLimit: "Result limit",
    integrations: "Integrations", orchestration: "Orchestration", chunking: "Chunking", vectorStore: "Vector store", database: "Database", cache: "Cache", tracing: "Tracing",
    localJson: "Local JSON files", jsonl: "JSONL (append-only)", organizationLabel: "Organization",
    editRoles: "Edit roles", allowedRoles: "Allowed roles", save: "Save", cancel: "Cancel",
    aclUpdated: "Document roles saved.", aclFailed: "Could not save document roles.",
    settingsFailed: "Could not load administrator settings.", ownerLabel: "Owner",
    host: "Host", systemStatus: "System status", storageUsage: "Storage", memoryUsage: "Memory", processCount: "Processes", containers: "Containers",
    modelProvider: "Model provider", adminLinks: "Admin links", quickLinks: "Quick links", uptime: "Uptime", apiProcess: "API process",
    threads: "threads", cpuLoad: "CPU load", cores: "cores", documentsSize: "Documents", auditSize: "Audit log", mem0Size: "Mem0", fastembedSize: "FastEmbed",
    documentsCount: "Document count", total: "Total", free: "Free", memoryAvailable: "Available memory", dockerUnavailable: "Cannot reach the Docker socket.",
    platformContainers: "Platform", allContainers: "All", open: "Open", docs: "Docs", noLink: "No link",
    providerAdapter: "Adapter", providerModel: "Model", providerEndpoint: "Endpoint", thinking: "Thinking", promptProfile: "Prompt",
    modelServer: "Model server", pgadmin: "pgAdmin", redisInsight: "Redis Insight", qdrant: "Qdrant", langfuse: "Langfuse",
    connected: "Connected", notConnected: "Not connected", dbSize: "DB size", auditRows: "Audit rows", vectorPoints: "Vector points", cacheKeys: "Cache keys", services: "Services",
    reindex: "Reindex vectors", reindexing: "Reindexing…", reindexed: (count: number) => `Reindexed ${count} document(s) into Qdrant.`, reindexFailed: "Vector reindex failed.",
    gaps: "Knowledge gaps", gapsBody: "Cluster ungrounded or vague questions and turn them into data requirements and ontology-pack drafts.",
    openGaps: "Open questions", clusters: "Suggested clusters", packs: "Ontology packs", occurrences: "occurrences", citationsLabel: "citations",
    dismiss: "Dismiss", createPack: "Create ontology pack", packCreated: (name: string) => `Created draft ${name}.`,
    noGaps: "No knowledge gaps yet", noGapsBody: "Questions the platform cannot ground will appear here.",
    noPacks: "No packs yet", noPacksBody: "Create a pack from a cluster to store a draft.",
    entities: "entities", relations: "relations", generatedFrom: (count: number) => `From ${count} question(s)`,
    gapCaptureFailed: "Could not load knowledge gaps.", packFailed: "Could not create the ontology pack.", statusDraft: "draft",
    approvePack: "Approve", verifyPack: "Re-verify", verifying: "Verifying…", coverage: "Coverage", resolved: "Resolved",
    statusReviewed: "Reviewed", statusActive: "Active", packVerified: (name: string, coverage: number) => `${name} coverage ${Math.round(coverage * 100)}%`,
    verifyFailed: "Re-verification failed.", missingTerms: "Missing terms",
    tacit: "Tacit knowledge", tacitBody: "Elicit judgement rules, exceptions, and local vocabulary, then publish answers as searchable knowledge.",
    tacitQuestions: "Questions", tacitMyAnswers: "My answers", tacitAllAnswers: "All answers", answerThis: "Answer",
    tacitPlaceholder: "Describe the judgement rule and why it matters…", attach: "Attach", submitAnswer: "Save answer",
    tacitSaved: "Answer saved.", publishAnswer: "Publish as knowledge", tacitDraft: "Draft", tacitPublished: "Published",
    tacitPublishedMessage: (title: string) => `Published as ${title}. It is now searchable and used in answers.`,
    tacitFailed: "Could not save the answer.", noTacit: "No answers yet",
    noTacitBody: "Answers appear here; publishing makes them searchable knowledge.",
    fromGap: "Open gap", fromTemplate: "Template", scopeMine: "Mine", scopeAll: "All",
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
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadNote, setUploadNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [source, setSource] = useState<SourceState | null>(null);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [memoryInput, setMemoryInput] = useState("");
  const [memoryQuery, setMemoryQuery] = useState("");
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [memoryError, setMemoryError] = useState<"disabled" | "failed" | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [auditError, setAuditError] = useState<"forbidden" | "failed" | null>(null);
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [settingsError, setSettingsError] = useState(false);
  const [reindexBusy, setReindexBusy] = useState(false);
  const [reindexMessage, setReindexMessage] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [roleDraft, setRoleDraft] = useState("");
  const [aclMessage, setAclMessage] = useState<{ id: string; ok: boolean } | null>(null);
  const [gaps, setGaps] = useState<GapItem[]>([]);
  const [gapSummary, setGapSummary] = useState<GapSummary | null>(null);
  const [clusters, setClusters] = useState<GapCluster[]>([]);
  const [packs, setPacks] = useState<OntologyPack[]>([]);
  const [gapsError, setGapsError] = useState<"forbidden" | "failed" | null>(null);
  const [gapBusy, setGapBusy] = useState(false);
  const [gapMessage, setGapMessage] = useState("");
  const [tacitQuestions, setTacitQuestions] = useState<TacitQuestion[]>([]);
  const [tacitEntries, setTacitEntries] = useState<TacitEntry[]>([]);
  const [tacitSelected, setTacitSelected] = useState<TacitQuestion | null>(null);
  const [tacitAnswer, setTacitAnswer] = useState("");
  const [tacitAttachments, setTacitAttachments] = useState<KnowledgeDocument[]>([]);
  const [tacitBusy, setTacitBusy] = useState(false);
  const [tacitMessage, setTacitMessage] = useState("");
  const [tacitError, setTacitError] = useState<"failed" | null>(null);
  const [tacitScope, setTacitScope] = useState<"mine" | "all">("mine");
  const tacitFileInput = useRef<HTMLInputElement>(null);
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

  const loadMemories = useCallback(async () => {
    try {
      const response = await fetch(`${API}/v1/memories?limit=50`, { headers: identity(role) });
      if (response.status === 503) {
        setMemories([]);
        setMemoryError("disabled");
        return;
      }
      if (!response.ok) throw new Error("Memory list failed");
      setMemoryError(null);
      setMemories((await response.json()).data);
    } catch {
      setMemories([]);
      setMemoryError("failed");
    }
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

  const loadGaps = useCallback(async () => {
    if (role !== "admin") {
      setGaps([]); setClusters([]); setPacks([]); setGapsError("forbidden");
      return;
    }
    try {
      const [gapResponse, clusterResponse, packResponse] = await Promise.all([
        fetch(`${API}/v1/admin/gaps?status=open&limit=100`, { headers: identity(role) }),
        fetch(`${API}/v1/admin/gaps/clusters`, { headers: identity(role) }),
        fetch(`${API}/v1/admin/ontology-packs`, { headers: identity(role) }),
      ]);
      if (!gapResponse.ok || !clusterResponse.ok || !packResponse.ok) throw new Error("Gap load failed");
      const gapData = await gapResponse.json();
      setGaps(gapData.data); setGapSummary(gapData.summary);
      setClusters((await clusterResponse.json()).data);
      setPacks((await packResponse.json()).data);
      setGapsError(null);
    } catch {
      setGapsError("failed");
    }
  }, [role]);

  const dismissGap = useCallback(async (id: string) => {
    const response = await fetch(`${API}/v1/admin/gaps/${id}/dismiss`, { method: "POST", headers: identity(role) });
    if (response.ok) await loadGaps();
  }, [role, loadGaps]);

  const createPack = useCallback(async (cluster: GapCluster) => {
    if (gapBusy) return;
    setGapBusy(true); setGapMessage("");
    try {
      const response = await fetch(`${API}/v1/admin/ontology-packs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...identity(role) },
        body: JSON.stringify({ gap_ids: cluster.gap_ids, name: cluster.suggested_pack_name }),
      });
      if (!response.ok) throw new Error("Pack creation failed");
      const pack = await response.json();
      setGapMessage(t.packCreated(pack.name));
      await loadGaps();
    } catch {
      setGapMessage(t.packFailed);
    } finally {
      setGapBusy(false);
    }
  }, [gapBusy, role, loadGaps, t]);

  const updatePack = useCallback(async (pack: OntologyPack, action: "approve" | "verify") => {
    if (gapBusy) return;
    setGapBusy(true); setGapMessage("");
    try {
      const response = await fetch(`${API}/v1/admin/ontology-packs/${pack.id}/${action}`, { method: "POST", headers: identity(role) });
      if (!response.ok) throw new Error("Pack action failed");
      const updated = await response.json();
      if (action === "verify" && updated.verification) {
        setGapMessage(t.packVerified(updated.name, updated.verification.coverage));
      }
      await loadGaps();
    } catch {
      setGapMessage(t.verifyFailed);
    } finally {
      setGapBusy(false);
    }
  }, [gapBusy, role, loadGaps, t]);

  const loadTacit = useCallback(async () => {
    try {
      const [questionResponse, entryResponse] = await Promise.all([
        fetch(`${API}/v1/tacit/questions`, { headers: identity(role) }),
        fetch(`${API}/v1/tacit/entries?scope=${tacitScope}`, { headers: identity(role) }),
      ]);
      if (!questionResponse.ok || !entryResponse.ok) throw new Error("Tacit load failed");
      setTacitQuestions((await questionResponse.json()).data);
      setTacitEntries((await entryResponse.json()).data);
      setTacitError(null);
    } catch {
      setTacitError("failed");
    }
  }, [role, tacitScope]);

  const uploadTacitAttachment = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setTacitBusy(true);
    try {
      for (const file of Array.from(files)) {
        const data = new FormData(); data.append("file", file);
        const response = await fetch(`${API}/v1/documents`, { method: "POST", body: data, headers: identity(role) });
        if (!response.ok) throw new Error("Attachment upload failed");
        const uploaded = await response.json();
        setTacitAttachments(previous => [...previous, {
          id: uploaded.id, title: uploaded.title, owner_id: "", allowed_roles: [], created_at: "", chunks: uploaded.chunks,
        }]);
      }
    } catch {
      setTacitMessage(t.tacitFailed);
    } finally {
      setTacitBusy(false);
      if (tacitFileInput.current) tacitFileInput.current.value = "";
    }
  }, [role, t]);

  const submitTacit = useCallback(async () => {
    if (!tacitSelected || !tacitAnswer.trim() || tacitBusy) return;
    setTacitBusy(true); setTacitMessage("");
    try {
      const response = await fetch(`${API}/v1/tacit/entries`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...identity(role) },
        body: JSON.stringify({
          question: tacitSelected.question,
          answer: tacitAnswer,
          attachment_ids: tacitAttachments.map(item => item.id),
          gap_id: tacitSelected.gap_id ?? null,
          source: tacitSelected.source,
        }),
      });
      if (!response.ok) throw new Error("Tacit save failed");
      setTacitMessage(t.tacitSaved);
      setTacitSelected(null); setTacitAnswer(""); setTacitAttachments([]);
      await loadTacit();
    } catch {
      setTacitMessage(t.tacitFailed);
    } finally {
      setTacitBusy(false);
    }
  }, [tacitSelected, tacitAnswer, tacitAttachments, tacitBusy, role, loadTacit, t]);

  const publishTacit = useCallback(async (entry: TacitEntry) => {
    if (tacitBusy) return;
    setTacitBusy(true); setTacitMessage("");
    try {
      const response = await fetch(`${API}/v1/tacit/entries/${entry.id}/publish`, { method: "POST", headers: identity(role) });
      if (!response.ok) throw new Error("Publish failed");
      const published = await response.json();
      setTacitMessage(t.tacitPublishedMessage(published.published_document_id ?? entry.question));
      await loadTacit();
    } catch {
      setTacitMessage(t.tacitFailed);
    } finally {
      setTacitBusy(false);
    }
  }, [tacitBusy, role, loadTacit, t]);

  useEffect(() => { if (view === "knowledge") void loadKnowledge(); }, [view, loadKnowledge]);
  useEffect(() => { if (view === "tacit") void loadTacit(); }, [view, loadTacit]);
  useEffect(() => { if (view === "memory") void loadMemories(); }, [view, loadMemories]);
  useEffect(() => { if (view === "governance") void loadAudit(); }, [view, loadAudit]);
  useEffect(() => { if (view === "gaps") void loadGaps(); }, [view, loadGaps]);
  useEffect(() => { if (view === "settings") void loadSettings(); }, [view, loadSettings]);
  useEffect(() => {
    if (role !== "admin") {
      setEditingId(null);
      setAclMessage(null);
      if (view === "settings" || view === "gaps") setView("chat");
    }
  }, [role, view]);

  async function upload(file: File) {
    setUploadBusy(true);
    setUploadNote(null);
    try {
      const data = new FormData(); data.append("file", file);
      const response = await fetch(`${API}/v1/documents`, { method: "POST", body: data, headers: identity(role) });
      if (!response.ok) throw new Error("Document upload failed");
      const result = await response.json(); await loadKnowledge();
      const message = t.indexed(result.title, result.chunks);
      setUploadNote({ ok: true, text: message });
      setMessages(previous => [...previous, { role: "assistant", content: message }]);
    } catch {
      setUploadNote({ ok: false, text: t.uploadFailed });
      setMessages(previous => [...previous, { role: "assistant", content: t.uploadFailed }]);
    } finally {
      setUploadBusy(false);
    }
  }

  async function openSource(citation: Citation) {
    setSource({ citation, loading: true });
    setSourceBusy(false);
    try {
      const response = await fetch(`${API}/v1/documents/${citation.document_id}?chunk=${citation.chunk}`, { headers: identity(role) });
      if (!response.ok) throw new Error("Source fetch failed");
      const detail: DocumentDetail = await response.json();
      setSource({ citation, detail, loading: false });
    } catch {
      setSource({ citation, loading: false, error: t.sourceFailed });
    }
  }

  async function openOriginal(download: boolean) {
    if (!source?.detail) return;
    setSourceBusy(true);
    try {
      const response = await fetch(`${API}/v1/documents/${source.detail.id}/file`, { headers: identity(role) });
      if (!response.ok) throw new Error("Original file unavailable");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      if (download) {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = source.detail.raw_filename || source.detail.title;
        anchor.click();
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      setSource(previous => previous ? { ...previous, error: t.rawFailed } : previous);
    } finally {
      setSourceBusy(false);
    }
  }

  function downloadText() {
    if (!source?.detail) return;
    const blob = new Blob([source.detail.content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${source.detail.title || "document"}.md`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }

  function openFilePicker(accept: string = UPLOAD_ACCEPT) {
    if (!fileInput.current) return;
    fileInput.current.accept = accept;
    fileInput.current.value = "";
    fileInput.current.click();
  }

  async function removeDocument(id: string) {
    const response = await fetch(`${API}/v1/documents/${id}`, { method: "DELETE", headers: identity(role) });
    if (response.ok) {
      setKnowledge(previous => previous.filter(document => document.id !== id));
      if (editingId === id) setEditingId(null);
    }
  }

  async function addMemory() {
    const text = memoryInput.trim();
    if (!text || memoryBusy) return;
    setMemoryBusy(true);
    try {
      const response = await fetch(`${API}/v1/memories`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...identity(role) },
        body: JSON.stringify({ text }),
      });
      if (response.status === 503) {
        setMemoryError("disabled");
        return;
      }
      if (!response.ok) throw new Error("Memory add failed");
      setMemoryInput("");
      setMemoryError(null);
      await loadMemories();
    } catch {
      setMemoryError("failed");
    } finally {
      setMemoryBusy(false);
    }
  }

  async function searchMemories() {
    const query = memoryQuery.trim();
    if (!query) {
      await loadMemories();
      return;
    }
    try {
      const response = await fetch(`${API}/v1/memories/search?q=${encodeURIComponent(query)}&limit=20`, { headers: identity(role) });
      if (response.status === 503) {
        setMemoryError("disabled");
        return;
      }
      if (!response.ok) throw new Error("Memory search failed");
      setMemoryError(null);
      setMemories((await response.json()).data);
    } catch {
      setMemoryError("failed");
    }
  }

  async function deleteMemory(id: string) {
    const response = await fetch(`${API}/v1/memories/${id}`, { method: "DELETE", headers: identity(role) });
    if (response.ok) setMemories(previous => previous.filter(item => item.id !== id));
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
      await loadKnowledge();
      setAclMessage({ id, ok: true });
    } catch {
      setAclMessage({ id, ok: false });
    }
  }

  function composerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || !event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!busy && input.trim()) event.currentTarget.form?.requestSubmit();
  }

  async function reindexDocuments() {
    setReindexBusy(true);
    setReindexMessage("");
    try {
      const response = await fetch(`${API}/v1/admin/reindex`, { method: "POST", headers: identity(role) });
      if (!response.ok) throw new Error("Reindex failed");
      const result = await response.json();
      setReindexMessage(t.reindexed(result.indexed));
      await loadSettings();
    } catch {
      setReindexMessage(t.reindexFailed);
    } finally {
      setReindexBusy(false);
    }
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
          setMessages(previous => {
            const copied = [...previous]; const last = copied.length - 1; const current = copied[last];
            copied[last] = {
              ...current,
              content: current.content + (item.type === "token" ? item.content : ""),
              citations: item.type === "citations" ? item.data : current.citations,
              trace: item.type === "trace" ? item.data : current.trace,
            };
            return copied;
          });
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
    if (view === "memory") return t.memory;
    if (view === "governance") return t.auditLog;
    if (view === "gaps") return t.gaps;
    if (view === "tacit") return t.tacit;
    if (view === "settings") return t.adminSettings;
    return t.groundedChat;
  }

  function traceLabel(step: string) {
    const labels: Record<string, string> = {
      rate_limit: t.traceRateLimit, policy: t.tracePolicy, memory_search: t.traceMemory,
      document_search: t.traceRetrieval, model: t.traceModel, audit: t.traceAudit,
    };
    return labels[step] ?? step;
  }

  function integrationValue(value: string, href?: string) {
    return <dd>{value}{href && <a className="inline-link" href={href} target="_blank" rel="noreferrer" title={t.open}><ExternalLink size={12}/></a>}</dd>;
  }

  return <main className={`shell ${role === "admin" ? "admin" : ""}`}>
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Sparkles size={17}/></div><span>Baryon AX</span></div>
      <button className="new-chat" onClick={() => { setView("chat"); setMessages([{ role: "assistant", content: t.welcome }]); }}>{t.newChat}</button>
      <nav>
        <button className={view === "chat" ? "active" : ""} onClick={() => setView("chat")}><Bot size={16}/>{t.chat}</button>
        <button className={view === "knowledge" ? "active" : ""} onClick={() => setView("knowledge")}><BookOpen size={16}/>{t.knowledge}</button>
        <button className={view === "tacit" ? "active" : ""} onClick={() => setView("tacit")}><MessagesSquare size={16}/>{t.tacit}</button>
        <button className={view === "memory" ? "active" : ""} onClick={() => setView("memory")}><Brain size={16}/>{t.memory}</button>
        <button className={view === "governance" ? "active" : ""} onClick={() => setView("governance")}><ShieldCheck size={16}/>{t.governance}</button>
        {role === "admin" && <button className={view === "gaps" ? "active" : ""} onClick={() => setView("gaps")}><Network size={16}/>{t.gaps}</button>}
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
              <div className="markdown">
                {message.content
                  ? <ReactMarkdown
                      remarkPlugins={[remarkGfm, remarkBreaks]}
                      components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer"/> }}
                    >{message.content}</ReactMarkdown>
                  : <span className="cursor"/>}
              </div>
              {message.role === "assistant" && message.trace && message.trace.steps.length > 0 && <details className="trace">
                <summary><span>{t.traceTitle}</span><em>{typeof message.trace.total_ms === "number" ? `${message.trace.total_ms} ${t.traceMs}` : "…"}</em></summary>
                <ol>
                  {message.trace.steps.map((step, stepIndex) => <li className={step.status} key={`${step.step}-${stepIndex}`}>
                    <span className="trace-dot"/>
                    <strong>{traceLabel(step.step)}</strong>
                    {step.detail && <span className="trace-detail">{step.detail}</span>}
                    <em>{step.status === "skipped" ? t.traceSkipped : `${step.ms} ${t.traceMs}`}</em>
                  </li>)}
                </ol>
              </details>}
              {message.citations && message.citations.length > 0
                ? <div className="sources"><span>{t.sources} {message.citations.length}</span>{message.citations.map((citation, citationIndex) => <button key={`${citation.document_id}-${citation.chunk}`} onClick={() => void openSource(citation)} title={`${citation.title} · chunk ${citation.chunk}`}><span className="source-index">{citationIndex + 1}</span><BookOpen size={13}/>{citation.title}<small>#{citation.chunk}</small><ChevronRight size={13}/></button>)}</div>
                : message.role === "assistant" && message.content && typeof message.trace?.total_ms === "number" && <p className="no-sources">{t.noSources}</p>}
            </div>
          </article>)}
        </div>
        <footer>
          <input ref={fileInput} type="file" accept={UPLOAD_ACCEPT} hidden onChange={event => event.target.files?.[0] && upload(event.target.files[0])}/>
          <button className="upload" title={t.upload} aria-label={t.upload} onClick={() => openFilePicker()}><FileUp size={18}/></button>
          <form onSubmit={send}><textarea value={input} onChange={event => setInput(event.target.value)} onKeyDown={composerKeyDown} placeholder={t.ask} rows={1}/><button aria-label={t.chat} aria-keyshortcuts="Shift+Enter" disabled={busy || !input.trim()}><Send size={17}/></button></form>
          <p>{t.scope} · {t.sendHint}</p>
        </footer>
      </>}
      {view === "knowledge" && <div className="knowledge">
        <div className="knowledge-top">
          <div><h2>{t.indexedDocs}</h2><p>{t.visibleDocs}</p></div>
          <button className="primary" disabled={uploadBusy} onClick={() => openFilePicker()}><FileUp size={16}/>{uploadBusy ? t.uploading : t.upload}</button>
          <input ref={fileInput} type="file" accept={UPLOAD_ACCEPT} hidden onChange={event => event.target.files?.[0] && upload(event.target.files[0])}/>
        </div>
        <div
          className={`upload-zone ${dragging ? "dragging" : ""} ${uploadBusy ? "busy" : ""}`}
          role="button" tabIndex={0}
          onClick={() => { if (!uploadBusy) openFilePicker(); }}
          onKeyDown={event => { if (!uploadBusy && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); openFilePicker(); } }}
          onDragOver={event => { event.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={event => { event.preventDefault(); setDragging(false); const file = event.dataTransfer.files?.[0]; if (file && !uploadBusy) void upload(file); }}
        >
          <div className="upload-zone-head">
            <UploadCloud size={24}/>
            <div><strong>{uploadBusy ? t.uploading : t.uploadZoneTitle}</strong><p>{t.uploadZoneBody}</p></div>
          </div>
          <div className="format-groups">
            {(["docs", "sheets", "text", "images"] as const).map(group => {
              const GroupIcon = formatGroupIcon(group);
              const label = group === "docs" ? t.formatDocs : group === "sheets" ? t.formatSheets : group === "text" ? t.formatText : t.formatImages;
              return <div className="format-group" key={group}>
                <span className="format-group-label"><GroupIcon size={12}/>{label}</span>
                <div className="format-chips">
                  {UPLOAD_FORMATS.filter(format => format.group === group).map(format => (
                    <button type="button" key={format.label} className={`format-chip ${group}`} disabled={uploadBusy}
                      onClick={event => { event.stopPropagation(); openFilePicker(format.accept); }}>{format.label}</button>
                  ))}
                </div>
              </div>;
            })}
          </div>
          <small className="format-hint">{t.formatHint}</small>
        </div>
        {uploadNote && <p className={`upload-note ${uploadNote.ok ? "ok" : "error"}`}>{uploadNote.text}</p>}
        {knowledge.length === 0 ? <div className="empty"><BookOpen size={28}/><h3>{t.noDocs}</h3><p>{t.noDocsBody}</p></div> : <div className="document-list">
          {knowledge.map(document => <div className="document" key={document.id}>
            <DocumentFormatIcon title={document.title}/>
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
      {view === "memory" && <div className="knowledge">
        <div className="knowledge-top">
          <div><h2>{t.memory}</h2><p>{t.memoryBody}</p></div>
          <button className="primary" onClick={() => void loadMemories()}><RefreshCw size={16}/>{t.refresh}</button>
        </div>
        <div className="memory-form">
          <input value={memoryInput} onChange={event => setMemoryInput(event.target.value)} placeholder={t.memoryPlaceholder} aria-label={t.memoryPlaceholder}/>
          <button className="primary" disabled={memoryBusy || !memoryInput.trim()} onClick={() => void addMemory()}><Plus size={16}/>{memoryBusy ? t.saving : t.saveMemory}</button>
        </div>
        <div className="memory-search">
          <input value={memoryQuery} onChange={event => setMemoryQuery(event.target.value)} placeholder={t.searchMemory} aria-label={t.searchMemory}/>
          <button className="primary" onClick={() => void searchMemories()}><Search size={16}/>{t.search}</button>
          <button className="ghost" onClick={() => { setMemoryQuery(""); void loadMemories(); }}>{t.cancel}</button>
        </div>
        {memoryError ? <div className="empty"><Brain size={28}/><h3>{memoryError === "disabled" ? t.memoryDisabled : t.failed}</h3><p>{memoryError === "disabled" ? t.memoryDisabledBody : t.scope}</p></div> : memories.length === 0 ? <div className="empty"><Brain size={28}/><h3>{t.noMemories}</h3><p>{t.noMemoriesBody}</p></div> : <div className="memory-list">
          {memories.map(item => <div className="memory-item" key={item.id}>
            <Brain size={16}/>
            <div>
              <p>{item.memory}</p>
              {item.created_at && <small>{new Date(item.created_at).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")}</small>}
            </div>
            <button className="delete" title={t.deleteMemory} aria-label={t.deleteMemory} onClick={() => void deleteMemory(item.id)}><Trash2 size={16}/></button>
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
      {view === "tacit" && <div className="knowledge">
        <div className="knowledge-top">
          <div><h2>{t.tacit}</h2><p>{t.tacitBody}</p></div>
          <button className="primary" onClick={() => void loadTacit()}><RefreshCw size={16}/>{t.refresh}</button>
        </div>
        {tacitError ? <div className="empty"><MessagesSquare size={28}/><h3>{t.failed}</h3><p>{t.scope}</p></div> : <>
          <h3 className="section-label">{t.tacitQuestions}</h3>
          {tacitQuestions.length === 0 ? <div className="empty"><MessagesSquare size={28}/><h3>{t.noTacit}</h3><p>{t.noTacitBody}</p></div> : <div className="cluster-grid">
            {tacitQuestions.map(question => <article className={`cluster-card tacit-question ${tacitSelected?.id === question.id ? "selected" : ""}`} key={question.id}>
              <header><strong>{question.question}</strong><span className="badge">{question.source === "gap" ? t.fromGap : t.fromTemplate}</span></header>
              {question.category && <p className="muted">{question.category}{question.occurrences > 1 ? ` · ${t.occurrences} ${question.occurrences}` : ""}</p>}
              <button className="primary" onClick={() => { setTacitSelected(question); setTacitAnswer(""); setTacitAttachments([]); setTacitMessage(""); }}><MessagesSquare size={15}/>{t.answerThis}</button>
            </article>)}
          </div>}
          {tacitSelected && <section className="tacit-form">
            <h3>{tacitSelected.question}</h3>
            <textarea value={tacitAnswer} onChange={event => setTacitAnswer(event.target.value)} placeholder={t.tacitPlaceholder} rows={6}/>
            <div className="tacit-attachments">
              <input ref={tacitFileInput} type="file" multiple hidden onChange={event => void uploadTacitAttachment(event.target.files)}/>
              <button className="ghost" disabled={tacitBusy} onClick={() => tacitFileInput.current?.click()}><Paperclip size={15}/>{t.attach}</button>
              {tacitAttachments.map(item => <span className="chip" key={item.id}>{item.title}<button aria-label={t.cancel} onClick={() => setTacitAttachments(previous => previous.filter(item2 => item2.id !== item.id))}><X size={12}/></button></span>)}
            </div>
            <div className="tacit-actions">
              <button className="primary" disabled={tacitBusy || !tacitAnswer.trim()} onClick={() => void submitTacit()}><Send size={15}/>{t.submitAnswer}</button>
              <button className="ghost" onClick={() => { setTacitSelected(null); setTacitAnswer(""); setTacitAttachments([]); }}>{t.cancel}</button>
            </div>
            {tacitMessage && <p className="settings-note">{tacitMessage}</p>}
          </section>}
          <div className="knowledge-top tacit-entries-head">
            <h3 className="section-label">{role === "admin" && tacitScope === "all" ? t.tacitAllAnswers : t.tacitMyAnswers}</h3>
            {role === "admin" && <div className="scope-toggle">
              <button className={tacitScope === "mine" ? "active" : ""} onClick={() => setTacitScope("mine")}>{t.scopeMine}</button>
              <button className={tacitScope === "all" ? "active" : ""} onClick={() => setTacitScope("all")}>{t.scopeAll}</button>
            </div>}
          </div>
          {tacitEntries.length === 0 ? <div className="empty"><MessagesSquare size={28}/><h3>{t.noTacit}</h3><p>{t.noTacitBody}</p></div> : <div className="pack-list">
            {tacitEntries.map(entry => <article className="pack" key={entry.id}>
              <header><strong>{entry.question}</strong><span className="badge">{entry.status === "published" ? t.tacitPublished : t.tacitDraft}</span></header>
              <p>{entry.answer.slice(0, 280)}</p>
              {entry.attachments.length > 0 && <div className="chips">{entry.attachments.map(item => <span className="chip" key={item.document_id}>{item.title}</span>)}</div>}
              {entry.status !== "published" && <div className="pack-actions">
                <button className="primary" disabled={tacitBusy} onClick={() => void publishTacit(entry)}><Sparkles size={15}/>{t.publishAnswer}</button>
              </div>}
            </article>)}
          </div>}
        </>}
      </div>}
      {view === "gaps" && <div className="knowledge">
        <div className="knowledge-top">
          <div><h2>{t.gaps}</h2><p>{t.gapsBody}</p></div>
          <button className="primary" onClick={() => void loadGaps()}><RefreshCw size={16}/>{t.refresh}</button>
        </div>
        {gapsError ? <div className="empty"><Network size={28}/><h3>{gapsError === "forbidden" ? t.adminRequired : t.gapCaptureFailed}</h3><p>{gapsError === "forbidden" ? t.adminRequiredBody : t.scope}</p></div> : <>
          <div className="gap-summary">
            <div><strong>{gapSummary?.open ?? 0}</strong><span>{t.openGaps}</span></div>
            <div><strong>{clusters.length}</strong><span>{t.clusters}</span></div>
            <div><strong>{packs.length}</strong><span>{t.packs}</span></div>
            <div><strong>{gapSummary?.resolved ?? 0}</strong><span>{t.resolved}</span></div>
          </div>
          {gapMessage && <p className="settings-note">{gapMessage}</p>}
          <h3 className="section-label">{t.clusters}</h3>
          {clusters.length === 0 ? <div className="empty"><Network size={28}/><h3>{t.noGaps}</h3><p>{t.noGapsBody}</p></div> : <div className="cluster-grid">
            {clusters.map(cluster => <article className="cluster-card" key={cluster.id}>
              <header><strong>{cluster.label}</strong><span className="badge">{cluster.size}</span></header>
              <div className="chips">{cluster.terms.map(term => <span className="chip" key={term}>{term}</span>)}</div>
              <ul>{cluster.questions.slice(0, 3).map(question => <li key={question}>{question}</li>)}</ul>
              <p className="muted">{cluster.required_sources.map(source => source.hint).join(" · ")}</p>
              <button className="primary" disabled={gapBusy} onClick={() => void createPack(cluster)}><Plus size={15}/>{t.createPack}</button>
            </article>)}
          </div>}
          <h3 className="section-label">{t.openGaps}</h3>
          {gaps.length === 0 ? <div className="empty"><Network size={28}/><h3>{t.noGaps}</h3><p>{t.noGapsBody}</p></div> : <div className="document-list">
            {gaps.map(gap => <div className="document gap-row" key={gap.id}>
              <div>
                <strong>{gap.question}</strong>
                <p>{new Date(gap.last_seen).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")} · {t.occurrences} {gap.occurrences} · {t.citationsLabel} {gap.citations}</p>
                <div className="chips">{gap.reasons.map(reason => <span className="chip reason" key={reason}>{reason}</span>)}</div>
              </div>
              <button className="delete" title={t.dismiss} aria-label={t.dismiss} onClick={() => void dismissGap(gap.id)}><X size={16}/></button>
            </div>)}
          </div>}
          <h3 className="section-label">{t.packs}</h3>
          {packs.length === 0 ? <div className="empty"><Network size={28}/><h3>{t.noPacks}</h3><p>{t.noPacksBody}</p></div> : <div className="pack-list">
            {packs.map(pack => <article className="pack" key={pack.id}>
              <header>
                <strong>{pack.name}</strong>
                <span className="badge">{pack.status === "active" ? t.statusActive : pack.status === "reviewed" ? t.statusReviewed : t.statusDraft}</span>
              </header>
              <p>{t.generatedFrom(pack.source_gap_ids.length)} · {t.entities} {pack.entities.length} · {t.relations} {pack.relations.length}{pack.verification ? ` · ${t.coverage} ${Math.round(pack.verification.coverage * 100)}%` : ""}</p>
              {pack.verification && <div className="coverage">
                <div className="meter"><span style={{ width: `${Math.min(100, pack.verification.coverage * 100)}%` }}/></div>
                <small>{pack.verification.passed}/{pack.verification.total} · {Math.round(pack.verification.coverage * 100)}%</small>
              </div>}
              <div className="chips">{pack.entities.slice(0, 8).map(entity => <span className="chip" key={entity.name}>{entity.name}</span>)}</div>
              {pack.verification?.results.filter(result => !result.passed).slice(0, 2).map(result => <p className="muted" key={result.question}>
                {result.question}{result.missing_terms.length ? ` · ${t.missingTerms}: ${result.missing_terms.join(", ")}` : ""}
              </p>)}
              <p className="muted">{pack.required_sources.map(source => source.hint).join(" · ")}</p>
              <div className="pack-actions">
                {pack.status === "draft" && <button className="primary" disabled={gapBusy} onClick={() => void updatePack(pack, "approve")}><ShieldCheck size={15}/>{t.approvePack}</button>}
                {(pack.status === "reviewed" || pack.status === "active") && <button className="primary" disabled={gapBusy} onClick={() => void updatePack(pack, "verify")}><RefreshCw size={15}/>{gapBusy ? t.verifying : t.verifyPack}</button>}
              </div>
            </article>)}
          </div>}
        </>}
      </div>}
      {view === "settings" && <div className="knowledge">
        <div className="knowledge-top">
          <div><h2>{t.adminSettings}</h2><p>{t.adminSettingsBody}</p></div>
          <div className="setting-actions">
            <button className="primary" disabled={reindexBusy} onClick={() => void reindexDocuments()}><RefreshCw size={16}/>{reindexBusy ? t.reindexing : t.reindex}</button>
            <button className="primary" onClick={() => void loadSettings()}><RefreshCw size={16}/>{t.refresh}</button>
          </div>
        </div>
        <div className="quick-links">
          <button onClick={() => setView("chat")}><Bot size={15}/>{t.chat}</button>
          <button onClick={() => setView("knowledge")}><BookOpen size={15}/>{t.knowledge}</button>
          <button onClick={() => setView("memory")}><Brain size={15}/>{t.memory}</button>
          <button onClick={() => setView("governance")}><ShieldCheck size={15}/>{t.governance}</button>
          <button onClick={() => setView("gaps")}><Network size={15}/>{t.gaps}</button>
        </div>
        {reindexMessage && <p className="settings-note">{reindexMessage}</p>}
        {settingsError || !settings ? <div className="empty"><Settings size={28}/><h3>{t.adminOnly}</h3><p>{t.settingsFailed}</p></div> : <div className="settings-grid">
          <section className="setting-card system-card">
            <h3><HardDrive size={15}/>{t.storageUsage}</h3>
            <div className="metric-head"><strong>{formatBytes(settings.system.storage.used_bytes)}</strong><span>/ {formatBytes(settings.system.storage.total_bytes)} · {settings.system.storage.percent}%</span></div>
            <div className="meter"><span style={{ width: `${Math.min(100, settings.system.storage.percent)}%` }}/></div>
            <dl>
              <div><dt>{t.total}</dt><dd>{formatBytes(settings.system.storage.total_bytes)}</dd></div>
              <div><dt>{t.free}</dt><dd>{formatBytes(settings.system.storage.free_bytes)}</dd></div>
              <div><dt>{t.documentsSize}</dt><dd>{formatBytes(settings.system.storage.documents_bytes)} · {settings.system.storage.documents_count}</dd></div>
              <div><dt>{t.auditSize}</dt><dd>{formatBytes(settings.system.storage.audit_bytes)}</dd></div>
              <div><dt>{t.mem0Size}</dt><dd>{formatBytes(settings.system.storage.mem0_bytes)}</dd></div>
              <div><dt>{t.fastembedSize}</dt><dd>{formatBytes(settings.system.storage.fastembed_bytes)}</dd></div>
              <div><dt>{t.auditRows}</dt><dd>{settings.system.services.database?.rows ?? "—"}</dd></div>
              <div><dt>{t.vectorPoints}</dt><dd>{settings.system.services.vector?.points ?? "—"}</dd></div>
              <div><dt>{t.cacheKeys}</dt><dd>{settings.system.services.cache?.keys ?? "—"}</dd></div>
              <div><dt>{t.dbSize}</dt><dd>{typeof settings.system.services.database?.size_bytes === "number" ? formatBytes(settings.system.services.database.size_bytes) : "—"}</dd></div>
            </dl>
          </section>
          <section className="setting-card system-card">
            <h3><Cpu size={15}/>{t.memoryUsage} · {t.processCount}</h3>
            <div className="metric-head"><strong>{formatBytes(settings.system.memory.used_bytes)}</strong><span>/ {formatBytes(settings.system.memory.total_bytes)} · {settings.system.memory.percent}%</span></div>
            <div className="meter"><span style={{ width: `${Math.min(100, settings.system.memory.percent)}%` }}/></div>
            <dl>
              <div><dt>{t.memoryAvailable}</dt><dd>{formatBytes(settings.system.memory.available_bytes)}</dd></div>
              <div><dt>{t.processCount}</dt><dd>{settings.system.process.total}</dd></div>
              <div><dt>{t.apiProcess}</dt><dd>PID {settings.system.process.api_pid} · {settings.system.process.api_threads} {t.threads} · {formatBytes(settings.system.process.api_rss_bytes)}</dd></div>
              <div><dt>{t.cpuLoad}</dt><dd>{settings.system.cpu.load_1} / {settings.system.cpu.load_5} / {settings.system.cpu.load_15} · {settings.system.cpu.cores} {t.cores}</dd></div>
              <div><dt>{t.uptime}</dt><dd>{formatUptime(settings.system.host.uptime_seconds)}</dd></div>
              <div><dt>{t.host}</dt><dd>{settings.system.host.hostname}</dd></div>
            </dl>
          </section>
          <section className="setting-card system-card">
            <h3><Boxes size={15}/>{t.containers} <span className="badge">{settings.system.containers.platform_running}/{settings.system.containers.platform_total}</span></h3>
            {!settings.system.containers.available ? <p className="muted">{t.dockerUnavailable}</p> : <>
              <p className="muted">{t.platformContainers}: {settings.system.containers.platform_running}/{settings.system.containers.platform_total} · {t.allContainers}: {settings.system.containers.running}/{settings.system.containers.total}</p>
              <div className="container-list">
                {settings.system.containers.items.map(item => <div className="container-item" key={item.name}>
                  <span className={`status-dot ${item.status} ${item.health ?? ""}`}/>
                  <div><strong>{item.name}</strong><small>{item.image} · {item.status}{item.health ? ` / ${item.health}` : ""}</small>{item.ports.length > 0 && <small>{item.ports.join(" · ")}</small>}</div>
                </div>)}
              </div>
            </>}
          </section>
          <section className="setting-card system-card">
            <h3><Server size={15}/>{t.modelProvider}</h3>
            <dl>
              <div><dt>{t.providerAdapter}</dt><dd>{settings.model.adapter}</dd></div>
              <div><dt>{t.providerModel}</dt><dd>{settings.model.name}</dd></div>
              <div><dt>{t.providerEndpoint}</dt><dd>{settings.model.base_url_configured ? t.connected : t.notConnected}</dd></div>
              <div><dt>{t.thinking}</dt><dd>{settings.model.thinking_disabled ? t.disabled : t.enabled}</dd></div>
              <div><dt>{t.promptProfile}</dt><dd>{settings.model.prompt_profile}</dd></div>
              <div><dt>{t.timeoutLabel}</dt><dd>{settings.model.timeout_seconds}s</dd></div>
            </dl>
            <div className="link-row">
              {settings.links.model_server && <a href={settings.links.model_server} target="_blank" rel="noreferrer"><ExternalLink size={13}/>{t.modelServer}</a>}
              {settings.links.vllm && <a href={settings.links.vllm} target="_blank" rel="noreferrer"><ExternalLink size={13}/>vLLM {t.docs}</a>}
              {settings.links.ollama && <a href={settings.links.ollama} target="_blank" rel="noreferrer"><ExternalLink size={13}/>Ollama {t.docs}</a>}
            </div>
          </section>
          <section className="setting-card system-card">
            <h3><ExternalLink size={15}/>{t.adminLinks}</h3>
            <div className="link-grid">
              {[
                [t.qdrant, settings.links.qdrant],
                [t.langfuse, settings.links.langfuse],
                [t.pgadmin, settings.links.pgadmin],
                [t.redisInsight, settings.links.redis_insight],
                [t.modelServer, settings.links.model_server],
              ].filter(([, href]) => Boolean(href)).map(([label, href]) => <a key={label} href={href} target="_blank" rel="noreferrer">{label}<ExternalLink size={13}/></a>)}
            </div>
            {![settings.links.qdrant, settings.links.langfuse, settings.links.pgadmin, settings.links.redis_insight, settings.links.model_server].some(Boolean) && <p className="muted">{t.noLink}</p>}
          </section>
          <section className="setting-card">
            <h3>{t.integrations}</h3>
            <dl>
              <div><dt>{t.orchestration}</dt>{integrationValue(settings.integrations.orchestration, settings.links.langgraph)}</div>
              <div><dt>{t.chunking}</dt>{integrationValue(settings.integrations.chunking, settings.links.llamaindex)}</div>
              <div><dt>{t.vectorStore}</dt>{integrationValue(settings.integrations.vector, settings.links.qdrant)}</div>
              <div><dt>{t.database}</dt>{integrationValue(settings.integrations.database, settings.links.pgadmin)}</div>
              <div><dt>{t.cache}</dt>{integrationValue(settings.integrations.cache, settings.links.redis_insight)}</div>
              <div><dt>{t.tracing}</dt>{integrationValue(settings.integrations.tracing, settings.links.langfuse)}</div>
              <div><dt>{t.memory}</dt>{integrationValue(settings.integrations.memory, settings.links.mem0)}</div>
            </dl>
          </section>
          <section className="setting-card">
            <h3>{t.overview}</h3>
            <dl>
              <div><dt>{t.versionLabel}</dt><dd>{settings.version}</dd></div>
              <div><dt>{t.adapterLabel}</dt><dd>{settings.model.adapter}</dd></div>
              <div><dt>{t.documentCount}</dt><dd>{settings.counts.documents}</dd></div>
              <div><dt>{t.auditEventCount}</dt><dd>{settings.counts.audit_events}</dd></div>
              <div><dt>{t.organizationLabel}</dt><dd>{settings.organization_id}</dd></div>
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
              <div><dt>{t.retrieval}</dt><dd>{settings.retrieval.mode.startsWith("qdrant") ? t.vectorMode : t.lexicalMode}</dd></div>
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
    {source && <div className="source-overlay" onClick={() => setSource(null)}>
      <aside className="source-panel" role="dialog" aria-modal="true" aria-label={t.sourceViewer} onClick={event => event.stopPropagation()}>
        <header className="source-head">
          <div>
            <p className="eyebrow">{t.sourceViewer}</p>
            <h2>{source.citation.title}</h2>
            <p className="source-meta">chunk #{source.citation.chunk}{source.detail ? ` · ${source.detail.chunks_count} chunks · ${source.detail.parser || source.detail.mime}` : ""}</p>
          </div>
          <button className="source-close" aria-label={t.close} onClick={() => setSource(null)}><X size={17}/></button>
        </header>
        {source.loading ? <div className="source-loading">{t.sourceLoading}</div> : source.error ? <div className="source-loading error">{source.error}</div> : source.detail ? <>
          <div className="source-body">
            {source.detail.cited_chunk && source.detail.content.trim() !== source.detail.cited_chunk.content.trim() && <section className="source-chunk">
              <h3>{t.citedSection}</h3>
              <p>{source.detail.cited_chunk.content}</p>
              {(source.detail.cited_chunk.heading_path || source.detail.cited_chunk.page) && <small>
                {source.detail.cited_chunk.heading_path || ""}{source.detail.cited_chunk.heading_path && source.detail.cited_chunk.page ? " · " : ""}{source.detail.cited_chunk.page ? `p.${source.detail.cited_chunk.page}` : ""}
              </small>}
            </section>}
            <section className="source-original">
              <h3>{t.originalText}</h3>
              <div className="markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer"/> }}>{source.detail.content}</ReactMarkdown>
              </div>
            </section>
          </div>
          <footer className="source-actions">
            {source.detail.has_raw_file
              ? <>
                  <button className="primary" disabled={sourceBusy} onClick={() => void openOriginal(false)}><ExternalLink size={14}/>{t.openOriginal}</button>
                  <button className="ghost" disabled={sourceBusy} onClick={() => void openOriginal(true)}>{t.downloadOriginal}</button>
                </>
              : <span className="muted">{t.rawMissing}</span>}
            <button className="ghost" onClick={downloadText}>{t.downloadText}</button>
          </footer>
        </> : null}
      </aside>
    </div>}
  </main>;
}
