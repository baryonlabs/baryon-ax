# Baryon AX 기능 목록

> 기준 버전: **0.6.0** · 문서 작성일: 2026-11-10  
> 범위: 현재 저장소에 구현된 **Basic Core** 기능 인벤토리

## 범례

| 표시 | 의미 |
| --- | --- |
| ✅ | 구현 및 검증 완료 |
| 🔶 | 부분 구현 또는 개발용/제한적 구현 |
| ⛔ | 미구현 (로드맵) |

---

## 1. 제품 개요

| 항목 | 내용 |
| --- | --- |
| 제품 | Baryon AX — 온프레미스·모델 무관 AI 에이전트 및 근거 기반 채팅 플랫폼 |
| 구현 범위 | Basic Core v0.6 수직 슬라이스 |
| UI | React 19 + TypeScript + Vite |
| API | FastAPI (Python 3.11+) |
| 문서 저장 | 로컬 JSON 파일 (`DOCUMENT_STORE`) |
| 감사 저장 | Append-only JSONL (`AUDIT_LOG`) |
| 검색 | 어휘 기반 검색 + 조직/역할/소유자 ACL |
| 인증 | 개발용 신원 헤더 (프로덕션은 OIDC/JWT 필요) |
| 배포 | Docker Compose, Kubernetes 스타터 매니페스트 |
| 라이선스 | MIT |

---

## 2. 사용자 인터페이스

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| 한국어/영어 UI | ✅ | 한국어 우선, 상단 언어 선택으로 전환 |
| 브라우저 언어 자동 감지 | ✅ | 최초 접속 시 브라우저 언어 기준, 이후 localStorage에 저장 |
| 역할 전환 | ✅ | 헤더에서 `멤버` / `관리자` 전환, localStorage에 저장 |
| 관리자 시각 구분 | ✅ | 앰버 테마, `ADMIN` 배지, 관리자 전용 메뉴 표시 |
| 채팅 워크스페이스 | ✅ | 메시지 목록, 스트리밍 커서, 출처 표시 |
| Markdown 렌더링 | ✅ | GFM 표·목록·코드·링크·줄바꿈, raw HTML 비활성화 |
| 지식 라이브러리 | ✅ | 문서 목록, 청크 수, 허용 역할, 소유자 표시 |
| 거버넌스 뷰 | ✅ | 관리자 감사 로그, 멤버 접근 차단 안내 |
| 관리자 설정 뷰 | ✅ | 개요·런타임·보안 정책·저장소 카드 |
| 반응형 레이아웃 | ✅ | 700px 이하에서 사이드바 숨김, 설정 카드 1열 |
| 빈 상태 안내 | ✅ | 문서 없음, 감사 이벤트 없음, 권한 없음 상태 표시 |
| 업로드 오류 안내 | ✅ | 파싱 실패·미지원 형식·용량 초과를 채팅/응답에 안내 |
| 스트림 오류 안내 | ✅ | 부분 응답 보존 후 오류 문구 추가 |
| 전역 로딩 인디케이터 | 🔶 | 전송 버튼 비활성화·커서 표시만 있고 전역 스피너는 없음 |

---

## 3. 채팅 및 모델

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| SSE 토큰 스트리밍 | ✅ | `text/event-stream`으로 토큰 단위 전송 |
| 실제 업스트림 스트리밍 | ✅ | OpenAI 호환 `stream: true` 응답을 중계 |
| `demo-local` 폴백 | ✅ | `MODEL_BASE_URL` 미설정 시 키 없이 동작하는 데모 응답 |
| OpenAI 호환 모델 어댑터 | ✅ | Ollama `qwen2.5:7b`, B300 GPU 7 vLLM `Qwen3.5-27B` 검증 |
| 원격 vLLM 서빙 | ✅ | B300 GPU 7, SSH 터널, `MODEL_DISABLE_THINKING` |
| LangGraph 오케스트레이션 | ✅ | 정책 검사 + 검색 단계를 그래프로 실행 |
| Langfuse 트레이싱 | ✅ | trace/generation, 사용자·조직·모델 메타데이터 |
| Redis 레이트 리밋 | ✅ | 사용자·조직당 분당 요청 제한 (fixed window) |
| 모델별 시스템 프롬프트 | ✅ | `qwen35`/`qwen`/`generic` 프로필, 한국어 강제·근거 강제·환각 방지 |
| 모델 선택 UI | 🔶 | 현재 설정된 모델 1개만 표시 |
| RAG on/off | ✅ | 요청별 `use_retrieval` 토글 |
| 출처 표시 | ✅ | 문서 ID·제목·청크 번호만 표시 (원문 비노출) |
| `Shift+Enter` 전송 | ✅ | 입력창에서 즉시 전송 |
| `Enter` 줄바꿈 | ✅ | 멀티라인 입력 지원 |
| 전송 중 중복 방지 | ✅ | `busy` 상태에서 전송 버튼 비활성화 |
| 부분 응답 보존 | ✅ | 스트림 실패 시 이미 받은 토큰 유지 |
| 취소 감사 이벤트 | ✅ | 클라이언트 연결 종료 시 `chat.cancelled` 기록 |
| `trace_id` 연결 | ✅ | 응답 `X-Trace-Id`, 감사 이벤트, 출처 이벤트 연결 |
| 대화 스레드 영속화 | ⛔ | `thread_id`는 수용·감사만 하고 DB 저장은 없음 |
| 대화 이력 서버 저장 | ⛔ | 브라우저 메모리 상태만 유지 |
| 툴 실행/에이전트 루프 | ⛔ | 모델 응답만 처리, 툴 승인·실행 없음 |
| 응답 중단 버튼 | ⛔ | UI 취소 버튼 없음 (연결 종료 시 서버 감사만) |

---

## 4. 지식 · 검색 (RAG)

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| 문서 업로드 | ✅ | MIME 기반 라우팅. 기본 `.txt`/`.md`, sidecar 구성 시 HWP/HWPX/PDF/Office/이미지 |
| 다중 업로드 | ✅ | 여러 파일 선택/드래그, 동일 카테고리·태그 일괄 적용 |
| 카테고리·태그 | ✅ | 업로드 시 지정, 문서별 편집(업로더·관리자), 목록에 칩으로 표시 |
| 분류 필터 | ✅ | `GET /v1/documents?category=&tag=` 및 UI 필터 |
| 업로드 크기 제한 | ✅ | 기본 20,000,000 bytes (`MAX_DOCUMENT_BYTES`) |
| MIME 스니핑 | ✅ | magic bytes 우선, ZIP 내부(`word/`, `xl/`, `ppt/`, HWPX `mimetype`) 검사 |
| 빈 문서 거부 | ✅ | 공백만 있는 문서는 422 |
| 텍스트 인코딩 검증 | ✅ | UTF-8 디코딩 실패 시 415 |
| 한글 정규화 | ✅ | NFD(macOS) 문서·파일명을 NFKC로 정규화해 NFC 질의와 매칭 |
| 구조 인지 청킹 | ✅ | 헤딩 경로(`h1 > h2`) 보존, 표는 헤더 반복 + 행 묶음 |
| 청킹 폴백 | ✅ | LlamaIndex `SentenceSplitter` (256 토큰, 32 토큰 overlap) |
| 파서 폴백 체인 | ✅ | kordoc → docling → Tika → 415, 실패는 `document.parse_failed` 감사 |
| 비동기 인제스트 잡 | ✅ | `POST /v1/documents?async=true` → `GET /v1/jobs/{id}` |
| 버튼 E2E | ✅ | `npm run test:buttons` — 33개 내비게이션·업로드·암묵지·공백·설정 버튼 시나리오 |
| 파서 sidecar | ✅ | `KORDOC_URL`/`DOCLING_URL`(HTTP) 또는 `KORDOC_CMD`/`DOCLING_CMD`(CLI), `TIKA_URL` |
| 파이프라인 배압/재시도/DLQ | ✅ | `INGEST_*` 환경변수, 영구 오류는 재시도 없이 DLQ |
| 문서 영속화 | ✅ | 문서별 JSON 파일, 재시작 후 복원 |
| 조직 격리 | ✅ | 다른 조직 문서는 목록·검색·삭제·수정 불가 |
| 역할 ACL | ✅ | 문서별 `allowed_roles` |
| 소유자 예외 | ✅ | 업로더는 역할과 무관하게 조회 가능 |
| 관리자 예외 | ✅ | 관리자는 같은 조직의 모든 문서 조회·삭제·ACL 수정 |
| 목록/검색 ACL 일원화 | ✅ | `can_view` 단일 규칙 사용 |
| 빈 ACL 보호 | ✅ | `allowed_roles`가 비면 소유자·관리자만 접근 |
| 한국어 조사 대응 | ✅ | 질의 토큰의 한글 접두어 확장으로 `Qdrant는` ↔ `Qdrant와` 매칭 |
| 검색 결과 제한 | ✅ | 기본 4개 (`search(limit=4)`) |
| 검색 최소 점수 | ✅ | `RETRIEVAL_MIN_SCORE` 기본 0.3, 무관 문서 컷 |
| 문서 삭제 | ✅ | 업로더 또는 관리자 |
| 문서 ACL 편집 | ✅ | 관리자 전용, `PATCH /v1/documents/{id}` |
| 원문 클라이언트 비노출 | ✅ | 출처에는 ID·제목·청크만 반환 |
| 벡터 검색/Qdrant 연동 | ✅ | FastEmbed 다국어 임베딩 + Qdrant, 실패 시 어휘 검색 폴백 |
| 임베딩 생성 | ✅ | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384차원) |
| 벡터 재색인 | ✅ | 관리자 설정 버튼 + `POST /v1/admin/reindex` |
| PDF/Word/HTML 파싱 | ✅ | docling sidecar/CLI 구성 시. 미구성 시 415 |
| HWP/HWPX/HWPML 파싱 | ✅ | kordoc sidecar/CLI 구성 시. 표 구조·RAG 청크 지원 |
| 스캔 이미지 OCR | 🔶 | kordoc 내장 OCR(설정 시), 그 외 OCR 어댑터는 미구현 |
| 데이터베이스 → 지식 | 🔶 | `baryon.ingest.db` + `scripts/ingest_db.py` (SQLite/SQLAlchemy), API 소스 엔드포인트는 미구현 |
| 문서 본문 수정 | ⛔ | ACL만 수정 가능 |
| 문서/감사 페이지네이션 | ⛔ | 감사는 `limit`만 지원, 문서 목록은 전체 반환 |
| 객체 스토리지 연동 | ⛔ | 로컬 파일 시스템 사용 |

### UX · 접근성

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| 빈 상태 안내 | ✅ | 추천 질문 4개, 업로드/암묵지/공백별 다음 행동 안내 |
| 스트리밍 제어 | ✅ | 생성 중지, 스피너, `aria-live` 상태 |
| 응답 액션 | ✅ | 복사, 다시 생성, 👍/👎 피드백 |
| 근거 신뢰 표시 | ✅ | 인용 수·출처 칩, 근거 없음 경고 배너 |
| 대화 연속성 | ✅ | localStorage 복원, 대화 지우기 확인 |
| 모바일 | ✅ | 드로어 사이드바, 1열 카드, 하단 고정 컴포저 |
| 키보드/접근성 | ✅ | skip link, focus-visible, `Esc`, reduced motion |
| 피드백 저장 | ✅ | `POST /v1/feedback`, trace_id 연결, 관리자 조회·감사 |
| 오픈소스 인벤토리 | ✅ | 설정 → 오픈소스 탭: 라이선스·링크·상태·검색/필터, `GET /v1/open-source` |

### 암묵지

> 설계 배경: [research/knowledge-gap-loop.md](research/knowledge-gap-loop.md)

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| 질문 제안 | ✅ | 미해결 공백(높은 신호) + 판단 기준·온보딩·예외·용어·문제 해결 템플릿 |
| 답변 작성 | ✅ | 질문별 답변, 작성자·조직 스코프 |
| 첨부 | ✅ | 기존 인제스트 경로로 업로드(HWP/PDF/Office 포함), 답변에 문서 ID 연결 |
| 지식 게시 | ✅ | 답변을 Markdown 문서로 색인해 검색·인용 대상으로 편입 |
| 공백 연결 | ✅ | 공백에서 시작한 답변은 게시 시 해당 공백을 `resolved`로 전환 |
| 권한 | ✅ | 작성자 본인 또는 관리자만 수정·게시, 관리자는 전체 답변 조회 |
| 답변 편집 | ✅ | 게시 전까지 수정 가능, 게시 후 불변 |

### 지식 공백 · 온톨로지 제안

> 설계 배경·절차·미래 계획: [research/knowledge-gap-loop.md](research/knowledge-gap-loop.md)

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| 공백 감지 | ✅ | 출처 없음, 거절 표현, 모호 표현을 신호로 기록 |
| 중복 집계 | ✅ | 같은 질문은 `occurrences`를 증가시키고 최근 답변으로 갱신 |
| 관리자 목록 | ✅ | `GET /v1/admin/gaps` (조직 스코프, 상태 필터, 사유 요약) |
| 클러스터 제안 | ✅ | 용어 가중치(TF×IDF)로 묶어 온톨로지 후보 생성 |
| 온톨로지 꾸러미 | ✅ | 엔티티·관계·필요 소스·수용 질문을 포함한 JSON 초안 저장 |
| 승인 워크플로 | ✅ | 초안 → 검토됨 → 활성. 승인·재검증 모두 감사 이벤트 기록 |
| 자동 재검증 | ✅ | 소스 업로드/DB 동기화 시 `reviewed` 꾸러미 재검증 + 미해결 공백 재평가 |
| 커버리지 | ✅ | passed/total·비율·부족 용어를 꾸러미에 저장, 관리자 화면 표시 |
| 초안 편집 | ⛔ | 엔티티·관계 직접 수정 UI는 미구현 |
| 자동 데이터 요구 | 🔶 | 소스 힌트는 규칙 기반(ERP/HR/법규 등). 실제 스키마 매핑은 미구현 |

### Mem0 메모리

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| 메모리 저장 | ✅ | `POST /v1/memories`, Ollama로 사실 추출 |
| 메모리 목록 | ✅ | `GET /v1/memories`, 사용자·조직 범위 |
| 메모리 검색 | ✅ | `GET /v1/memories/search`, Qdrant 시맨틱 검색 |
| 메모리 삭제 | ✅ | `DELETE /v1/memories/{id}` |
| 메모리 UI | ✅ | 사이드바 `메모리` 탭에서 저장·검색·삭제 |
| 대화 자동 메모리 주입 | ⛔ | 현재 수동 저장/검색, 채팅 컨텍스트 자동 주입은 없음 |

---

## 5. 관리자 · 거버넌스

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| 관리자 전용 설정 메뉴 | ✅ | 관리자 역할에서만 사이드바에 표시 |
| 관리자 설정 조회 | ✅ | `GET /v1/admin/settings` |
| 개요 카드 | ✅ | 버전, 어댑터, 문서 수, 감사 이벤트 수 |
| 런타임 카드 | ✅ | 모델명, 엔드포인트 설정 여부, 타임아웃, 최대 업로드 |
| 보안 정책 카드 | ✅ | 차단 패턴 수, PII 마스킹, 검색 모드, 결과 수 |
| 저장소 카드 | ✅ | 문서/감사 저장 방식, 조직 ID |
| 통합 구성 카드 | ✅ | LangGraph·LlamaIndex·Qdrant·PostgreSQL·Redis·Langfuse·Mem0 상태 |
| 벡터 재색인 | ✅ | 관리자 설정 버튼 + `POST /v1/admin/reindex` |
| 감사 로그 조회 | ✅ | 관리자만, 자기 조직 이벤트만 (PostgreSQL 우선) |
| 감사 로그 제한 | ✅ | `limit` 1~500, 기본 100 |
| 문서 ACL 변경 | ✅ | 관리자 인라인 편집 UI + API |
| ACL 변경 감사 | ✅ | `document.acl_updated` 이벤트 |
| 관리자 전용 네비게이션 | ✅ | 멤버에게 설정·ACL 편집 UI 숨김 |
| 런타임 설정 변경 | ⛔ | 모델/정책/저장소 값은 읽기 전용 |
| 사용자·멤버 관리 | ⛔ | 사용자 DB, 초대, 역할 부여 기능 없음 |
| 정책 패턴 편집 | ⛔ | 차단 패턴은 코드/환경 기반 |
| 감사 보존/무결성 정책 | ⛔ | PostgreSQL + JSONL 이중 기록, 보존·서명·변조 방지 없음 |
| 감사 검색/필터 | 🔶 | 조직·limit만 지원, 이벤트 유형/기간 필터 없음 |

### 감사 이벤트 유형

| 이벤트 | 발생 시점 |
| --- | --- |
| `chat.started` | 채팅 요청 수락 |
| `chat.completed` | 스트리밍/비스트리밍 응답 완료 |
| `chat.failed` | 모델 호출 실패 |
| `chat.cancelled` | 스트리밍 중 클라이언트 연결 종료 |
| `document.ingested` | 문서 업로드·색인 |
| `document.deleted` | 문서 삭제 |
| `document.acl_updated` | 관리자 문서 ACL 변경 |
| `memory.added` | Mem0 메모리 저장 |
| `memory.deleted` | Mem0 메모리 삭제 |
| `admin.reindexed` | 관리자 벡터 재색인 |

---

## 6. API 엔드포인트

| Method | Endpoint | 접근 권한 | 설명 |
| --- | --- | --- | --- |
| `GET` | `/health` | 공개 | 상태, 버전, 모델·벡터·감사·캐시·트레이싱·메모리 백엔드 |
| `GET` | `/v1/models` | 공개 | 사용 가능한 모델 목록 |
| `POST` | `/v1/chat/completions` | 멤버·관리자 | OpenAI 스타일 채팅, SSE 스트리밍 |
| `POST` | `/v1/documents` | 멤버·관리자 | 문서 업로드 (multipart) |
| `GET` | `/v1/documents` | 멤버·관리자 | 권한 범위 내 문서 목록 |
| `GET` | `/v1/documents/search` | 멤버·관리자 | Qdrant 벡터 검색 (실패 시 어휘 폴백) |
| `PATCH` | `/v1/documents/{id}` | 관리자 | 문서 ACL 변경 |
| `DELETE` | `/v1/documents/{id}` | 업로더·관리자 | 문서 삭제 |
| `POST` | `/v1/memories` | 멤버·관리자 | Mem0 메모리 저장 |
| `GET` | `/v1/memories` | 멤버·관리자 | 사용자 메모리 목록 |
| `GET` | `/v1/memories/search` | 멤버·관리자 | 메모리 시맨틱 검색 |
| `DELETE` | `/v1/memories/{id}` | 멤버·관리자 | 메모리 삭제 |
| `GET` | `/v1/audit-events` | 관리자 | 조직 감사 이벤트 |
| `POST` | `/v1/admin/reindex` | 관리자 | Qdrant 벡터 재색인 |
| `GET` | `/v1/admin/settings` | 관리자 | 런타임·정책·저장소·통합·카운트 |
| `GET` | `/v1/connectors` | 멤버·관리자 | 큐레이션 SaaS 커넥터 카탈로그 |
| `GET` | `/v1/connectors/{id}` | 멤버·관리자 | 커넥터 상세와 도구 목록 |
| `GET` | `/v1/tools` | 멤버·관리자 | 도구 목록·필터 |
| `GET` | `/v1/tools/search` | 멤버·관리자 | 의도 기반 도구 검색 |
| `GET` | `/v1/mcp/status` | 멤버·관리자 | 게이트웨이 설정·카탈로그 상태 |

### 요청 옵션 (`POST /v1/chat/completions`)

| 필드 | 기본값 | 설명 |
| --- | --- | --- |
| `model` | `demo-local` | 모델 ID; `MODEL_BASE_URL` 설정 시 `MODEL_NAME`으로 해석 |
| `messages` | 필수 | `system` / `user` / `assistant` 메시지 배열 |
| `stream` | `true` | SSE 스트리밍 여부 |
| `use_retrieval` | `true` | RAG 사용 여부 |
| `thread_id` | `null` | 감사 로그에 기록되는 스레드 식별자 |
| `locale` | `ko` | `ko` / `en` 데모 응답 언어 |

### 개발용 신원 헤더

| 헤더 | 기본값 | 설명 |
| --- | --- | --- |
| `X-User-Id` | `local-user` | 사용자 식별자 |
| `X-Organization-Id` | `local-org` | 조직 식별자 |
| `X-Roles` | `member` | 쉼표 구분 역할 (`member`, `admin`, `legal` 등) |

---

## 7. 보안 · 정책

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| 입력 정책 차단 | ✅ | 금지 패턴 포함 시 422 |
| 전화번호 마스킹 | ✅ | 출력에서 `[REDACTED-PHONE]`로 치환 |
| 스트림 경계 PII 마스킹 | ✅ | 청크 사이에 걸친 번호도 안전하게 마스킹 |
| 문서 업로드 검증 | ✅ | 확장자·UTF-8·크기·빈 내용 검사 |
| 조직 단위 감사 격리 | ✅ | 관리자도 자기 조직 이벤트만 조회 |
| 감사 정보 최소화 | ✅ | 실패 원인은 예외 타입만 기록, 엔드포인트 상세 비노출 |
| 출처 데이터 최소화 | ✅ | 문서 원문을 클라이언트에 반환하지 않음 |
| CORS 제한 | ✅ | `ALLOWED_ORIGINS` 기반 |
| 비밀정보 저장 | 🔶 | `.env` 사용, `.gitignore` 처리. 시크릿 매니저 미연동 |
| OIDC/JWT 인증 | 🔶 | API Bearer 토큰 검증·RBAC 구현(`AUTH_MODE=oidc`). 웹 PKCE 로그인은 미구현 |
| Rate limiting | ✅ | Redis fixed-window, 사용자·조직당 분당 제한 (다중 노드 정교한 정책은 ⛔) |
| CSRF/보안 헤더 | ⛔ | 쿠키 인증·CSP·HSTS 등 미적용 |
| 완전한 PII 탐지 | 🔶 | 전화번호 패턴만 예시로 제공 |
| 감사 변조 방지/보존 | ⛔ | PostgreSQL + JSONL 이중 기록, 서명·WORM·보존 정책 없음 |
| Langfuse/Mem0 데이터 보존 | ⛔ | 프롬프트·메모리 보존/삭제 정책 미구현 |
| 비밀 마스킹 로그 | 🔶 | 감사 페이로드 최소화 수준 |

---

## 8. 데이터 · 저장소

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| 문서 JSON 저장 | ✅ | 문서별 `{id}.json`, 원자적 교체 |
| 감사 JSONL 저장 | ✅ | append-only, 재시작 시 복원 (PostgreSQL과 이중 기록) |
| 인메모리 문서 인덱스 | ✅ | 시작 시 JSON 로드 |
| 감사 메모리 버퍼 | ✅ | 최근 5,000건 유지 |
| PostgreSQL | ✅ | `deps` 프로필, 감사 이벤트 테이블 |
| Redis | ✅ | `deps` 프로필, 채팅 레이트 리밋 |
| Qdrant | ✅ | `deps` 프로필, 문서 벡터 + Mem0 벡터 |
| Mem0 | ✅ | Qdrant + FastEmbed + Ollama, 이력 SQLite |
| FastEmbed 모델 캐시 | ✅ | `models` 볼륨 (`HF_HOME`) |
| 오브젝트 스토리지 | ⛔ | S3/MinIO 등 미연동 |
| 데이터 백업 가이드 | ✅ | `docs/operations.md`에 문서·감사·벡터·메모리 백업 지침 |

---

## 9. 배포 · 운영

> 기본 `docker compose up`은 `api` + `web`만 실행합니다. `docker-compose.full.yml` + `--profile deps`는 PostgreSQL, Redis, Qdrant와 호스트 Ollama/Langfuse를 연결합니다.

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| Docker Compose Basic Core | ✅ | API + Web, `demo-local` |
| Full-stack override | ✅ | `docker-compose.full.yml` + `--profile deps` |
| B300 vLLM override | ✅ | `docker-compose.b300.yml`, GPU 7, SSH 터널 |
| B300 host full deployment | ✅ | `docker-compose.b300-host.yml`, Langfuse, `/data` bind mounts |
| Ollama 프록시 | ✅ | `scripts/ollama_proxy.py` (Docker Desktop loopback 우회) |
| Compose 데이터 서비스 | ✅ | PostgreSQL(5433), Redis(6379), Qdrant(6333/6334) |
| API 이미지 | ✅ | Python 3.12 slim, 비루트 사용자(uid 10001) |
| Web 이미지 | ✅ | Node 22 빌드 + Nginx 1.27 정적 서빙 |
| Nginx SPA 라우팅 | ✅ | `root`/`index`/`try_files` 설정 |
| Nginx API 프록시 | ✅ | `/api/` → `http://api:8000/` |
| Compose 헬스체크 | ✅ | API healthcheck, Web은 API healthy 후 기동 |
| Compose 볼륨 | ✅ | documents, audit, mem0, models, postgres, qdrant |
| Kubernetes 매니페스트 | ✅ | Namespace, Deployment, Service |
| K8s 보안 컨텍스트 | ✅ | 비루트, read-only root, capability drop, seccomp, fsGroup |
| K8s 프로브 | ✅ | readiness / liveness `/health` |
| K8s 데이터 볼륨 | 🔶 | `emptyDir` 스타터, 운영 시 PVC/관리형 저장소로 교체 필요 |
| CI | ✅ | API pytest + Web build |
| E2E 자동화 | ✅ | Playwright 15 checks, 전체 Compose 대상 검증 |
| 시스템 모니터링 | ✅ | 관리자 설정에서 저장공간·메모리·프로세스·컨테이너·백엔드 상태 표시 |
| 업로드 포맷 UX | ✅ | 지식 화면에 지원 형식 배지(PDF/HWP/HWPX/Office/이미지 등)와 드래그·드롭 영역 |
| 암묵지 생성 근거 | ✅ | 답변별 생성 경로·원본 공백·감지 사유·발생 횟수·핵심 용어·게시 문서 표시 |
| 유휴 시간 질문 큐 | ✅ | 지식 공백·반복 질문을 모아 전문가에게 먼저 물어볼 질문으로 준비 |
| 지식 저장소 영속화 | ✅ | 지식 공백·암묵지 저장소를 `/data` 볼륨에 보관해 재시작 후에도 유지 |
| 출처 원문 뷰어 | ✅ | 출처 클릭 시 인용 구절·문서 원문·원본 파일 열기/다운로드 |
| 원본 파일 보관 | ✅ | 업로드 원본을 문서 ACL과 함께 보관하고 권한 검사 후 제공 |
| 메모리 컨텍스트 주입 | ✅ | 채팅 시 Mem0 관련 기억을 검색해 시스템 프롬프트에 주입 |
| 파이프라인 추적 | ✅ | 속도제한·정책·메모리·문서검색·모델·감사 단계별 ms를 UI와 로그로 표시 |
| 통합 관리자 링크 | ✅ | Qdrant/Langfuse/모델 서버/pgAdmin/Redis Insight를 설정에서 원클릭 이동 |
| 모델 프로바이더 설정 | ✅ | 어댑터·모델·엔드포인트·Thinking·프롬프트 프로필 표시 |
| 전체 스택 스모크 | ✅ | `scripts/smoke_full_stack.py` |
| 에어갭 가이드 | ✅ | 이미지·모델·패키지 미러링 지침 |
| 관측성(Langfuse) | ✅ | 셀프호스팅 Langfuse v2 + SDK |
| 이미지 서명/SBOM | ⛔ | 공급망 보안 미적용 |

---

## 10. 테스트 · 품질

| 항목 | 상태 | 결과/설명 |
| --- | --- | --- |
| API 단위·통합 테스트 | ✅ | **115 passed** |
| Playwright E2E | ✅ | **15 checks passed** (`npm run test:e2e`, Vite/Compose) |
| Web 타입체크·빌드 | ✅ | `tsc -b && vite build` 성공 |
| 전체 스택 스모크 | ✅ | `scripts/smoke_full_stack.py` — health/upload/search/chat/audit/memory |
| Docker 이미지 빌드 | ✅ | API/Web 이미지 빌드 성공 |
| 의존성 감사 | ✅ | `npm audit --omit=dev`: 0 vulnerabilities |
| Python 의존성 점검 | ✅ | `pip check`: 깨진 요구사항 없음 |
| 정적 컴파일 | ✅ | `compileall` 통과 |
| Compose/K8s YAML 검증 | ✅ | `docker compose config`, YAML 파싱 통과 |
| 프런트엔드 단위 테스트 | ⛔ | E2E만 존재 |
| 부하/성능 테스트 | ⛔ | 미실시 |
| 보안 스캔 CI | ⛔ | Semgrep/Trivy 등 미연동 |

---

## 11. 환경변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `MODEL_NAME` | `demo-local` | 모델 ID |
| `MODEL_BASE_URL` | 빈 값 | OpenAI 호환 엔드포인트 (빈 값이면 데모) |
| `MODEL_API_KEY` | 빈 값 | 모델 서버 인증 키 |
| `MODEL_TIMEOUT_SECONDS` | `180` | 업스트림 타임아웃 |
| `MODEL_PROMPT_PROFILE` | `auto` | `qwen35`/`qwen`/`generic` 시스템 프롬프트 |
| `MODEL_SYSTEM_PROMPT` | 빈 값 | 시스템 프롬프트 전체 오버라이드 |
| `RETRIEVAL_MIN_SCORE` | `0.3` | Qdrant 검색 최소 유사도 |
| `MODEL_DISABLE_THINKING` | `false` | Qwen3.5 vLLM thinking 비활성화 |
| `MODEL_CHAT_TEMPLATE_KWARGS` | 빈 값 | vLLM `chat_template_kwargs` JSON |
| `ALLOWED_ORIGINS` | `http://localhost:3001,http://localhost:5173` | CORS 허용 오리진 |
| `DOCUMENT_STORE` | `/tmp/baryon-ax-documents` | 문서 JSON 저장 경로 |
| `AUDIT_LOG` | `/tmp/baryon-ax-audit/events.jsonl` | 감사 JSONL 경로 |
| `MAX_DOCUMENT_BYTES` | `2000000` | 문서 업로드 최대 크기 |
| `DATABASE_URL` | 빈 값 | PostgreSQL 감사 저장소 |
| `REDIS_URL` | 빈 값 | Redis 레이트 리밋 |
| `RATE_LIMIT_PER_MINUTE` | `60` | 분당 채팅 요청 수 |
| `QDRANT_URL` | 빈 값 | Qdrant 벡터 저장소 |
| `QDRANT_COLLECTION` | `baryon_documents` | 문서 벡터 컬렉션 |
| `QDRANT_REINDEX_ON_START` | `false` | 시작 시 전체 재색인 |
| `EMBEDDING_MODEL` | multilingual MiniLM | FastEmbed 모델 |
| `EMBEDDING_DIMENSION` | `384` | 임베딩 차원 |
| `LANGFUSE_ENABLED` | `false` | Langfuse 트레이싱 |
| `LANGFUSE_HOST` | `http://127.0.0.1:3000` | Langfuse 서버 |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 빈 값 | Langfuse 프로젝트 키 |
| `MEM0_ENABLED` | `false` | Mem0 메모리 |
| `MEM0_LLM_MODEL` | `qwen2.5:7b` | 메모리 추출 모델 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Mem0용 Ollama |
| `MEM0_HISTORY_DB` | `/tmp/baryon-ax-mem0/history.db` | Mem0 이력 SQLite |
| `MEM0_TELEMETRY` | `false` | Mem0 텔레메트리 비활성화 |
| `MEMORY_CONTEXT_LIMIT` | `5` | 채팅에 주입할 최대 기억 수 |
| `GAP_STORE` | `/data/gaps/gaps.jsonl` | 지식 공백 저장 경로 |
| `TACIT_STORE` | `/data/tacit/tacit.jsonl` | 암묵지 답변 저장 경로 |
| `LOG_LEVEL` | `INFO` | API 로그 레벨 (파이프라인 추적 로그 포함) |
| `SYSTEM_METRICS_ENABLED` | `true` | Docker 소켓 기반 컨테이너 지표 수집 |
| `DOCKER_PROJECT` | `baryon-ax` | 관리자 화면에 표시할 Compose 프로젝트 |
| `DOCKER_GID` | `0` | Docker 소켓 그룹 ID (B300은 `113`) |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | 마운트할 Docker 소켓 경로 |
| `HOST_PROC` | `/host/proc` | 호스트 프로세스 수 계산용 proc 마운트 |
| `QDRANT_DASHBOARD_URL` / `LANGFUSE_DASHBOARD_URL` | 빈 값 | 설정 화면의 대시보드 링크 |
| `MODEL_SERVER_URL` / `PGADMIN_URL` / `REDIS_INSIGHT_URL` | 빈 값 | 설정 화면의 관리자 링크 |
| `POSTGRES_DB` | `baryon` | Compose PostgreSQL DB |
| `POSTGRES_USER` | `baryon` | Compose PostgreSQL 사용자 |
| `POSTGRES_PASSWORD` | 예시 값 | Compose PostgreSQL 비밀번호 (변경 필수) |
| `WEB_PORT` | `3001` | Compose Web 포트 |
| `MODEL_HOST_URL` | 빈 값 | full-stack Compose 원격 모델 호스트 (예: B300 vLLM) |

---

## 12. 역할 · 권한 매트릭스

| 기능 | 멤버 | 관리자 |
| --- | --- | --- |
| 채팅 / 스트리밍 | ✅ | ✅ |
| 문서 업로드 | ✅ | ✅ |
| 문서 목록 | 역할·소유자 범위 | 같은 조직 전체 |
| 문서 검색 | 역할·소유자 범위 | 같은 조직 전체 |
| 문서 삭제 | 본인 문서만 | 같은 조직 전체 |
| 문서 ACL 편집 | ⛔ | ✅ |
| 메모리 저장/검색/삭제 | ✅ (본인) | ✅ (본인) |
| 감사 로그 | ⛔ | ✅ (자기 조직) |
| 관리자 설정 | ⛔ | ✅ |
| 관리자 메뉴/배지 | ⛔ | ✅ |
| 타 조직 접근 | ⛔ | ⛔ |

---

## 13. 미구현 · 로드맵

| 우선순위 | 항목 | 설명 |
| --- | --- | --- |
| 높음 | OIDC/JWT 인증 | 개발용 헤더를 실제 IdP 검증으로 교체 |
| 높음 | 대화 스레드 영속화 | PostgreSQL 기반 스레드·메시지 저장, `thread_id` 활용 |
| 높음 | Guardrails/NeMo | 현재 PolicyEngine을 조직별 정책·분류기 레일로 확장 |
| 중간 | 런타임 설정 편집 | 모델 엔드포인트·정책·검색 limit을 UI에서 변경 |
| 중간 | 사용자/멤버 관리 | 조직 사용자·역할 관리 화면 |
| 중간 | 감사 무결성/보존 | 서명·WORM·보존 기간, Langfuse/Mem0 삭제 정책 |
| 중간 | 문서 파싱 확장 | PDF/Word/HTML, 재색인 |
| 중간 | 채팅 메모리 자동 주입 | Mem0 검색 결과를 채팅 컨텍스트에 자동 포함 |
| 낮음 | 툴/에이전트 실행 | LangGraph 체크포인트 기반 승인·재시도·중단 |
| 낮음 | Milvus/SGLang/LiteLLM | 대안 스택 평가·프로필 추가 (vLLM은 B300 GPU 7에서 검증) |
| 낮음 | Kubernetes 런타임 | 매니페스트를 실제 클러스터에서 검증 |
| 낮음 | CI E2E/보안 스캔 | Playwright 잡, Trivy/Semgrep |

---

## 14. 검증 기준

```text
API tests           115 passed
Playwright E2E      15 checks passed (Vite 5173 / Compose 3001)
Full-stack smoke    health + upload + Qdrant retrieval + chat + audit + Mem0
Web build           tsc -b && vite build 성공
Docker build        API/Web 이미지 성공
npm audit           0 vulnerabilities
pip check           깨진 요구사항 없음
```

---

## 관련 문서

- [아키텍처](architecture.md)
- [전체 스택 실행 가이드](full-stack.md)
- [B300 호스트 배포](b300-host-deploy.md)
- [Strata/로컬 AI 조사](research/strata-and-local-ai-shortlist.md)
- [오픈소스 구성 요소](open-source.md)
- [운영 가이드](operations.md)
- [보안 베이스라인](security.md)
- [README](../README.md)
