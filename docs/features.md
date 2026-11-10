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
| 지식 라이브러리 | ✅ | 문서 목록, 청크 수, 허용 역할, 소유자 표시 |
| 거버넌스 뷰 | ✅ | 관리자 감사 로그, 멤버 접근 차단 안내 |
| 관리자 설정 뷰 | ✅ | 개요·런타임·보안 정책·저장소 카드 |
| 반응형 레이아웃 | ✅ | 700px 이하에서 사이드바 숨김, 설정 카드 1열 |
| 빈 상태 안내 | ✅ | 문서 없음, 감사 이벤트 없음, 권한 없음 상태 표시 |
| 업로드 오류 안내 | ✅ | UTF-8/확장자 오류 시 채팅에 안내 메시지 |
| 스트림 오류 안내 | ✅ | 부분 응답 보존 후 오류 문구 추가 |
| 전역 로딩 인디케이터 | 🔶 | 전송 버튼 비활성화·커서 표시만 있고 전역 스피너는 없음 |

---

## 3. 채팅 및 모델

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| SSE 토큰 스트리밍 | ✅ | `text/event-stream`으로 토큰 단위 전송 |
| 실제 업스트림 스트리밍 | ✅ | OpenAI 호환 `stream: true` 응답을 중계 |
| `demo-local` 폴백 | ✅ | `MODEL_BASE_URL` 미설정 시 키 없이 동작하는 데모 응답 |
| OpenAI 호환 모델 어댑터 | ✅ | vLLM, SGLang, LiteLLM 등 `/chat/completions` 호환 서버 |
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
| 문서 업로드 | ✅ | UTF-8 `.txt` / `.md` |
| 업로드 크기 제한 | ✅ | 기본 2,000,000 bytes (`MAX_DOCUMENT_BYTES`) |
| 빈 문서 거부 | ✅ | 공백만 있는 문서는 422 |
| UTF-8 검증 | ✅ | 디코딩 실패 시 415 |
| 청킹 | ✅ | 900자 단위 고정 분할 |
| 문서 영속화 | ✅ | 문서별 JSON 파일, 재시작 후 복원 |
| 조직 격리 | ✅ | 다른 조직 문서는 목록·검색·삭제·수정 불가 |
| 역할 ACL | ✅ | 문서별 `allowed_roles` |
| 소유자 예외 | ✅ | 업로더는 역할과 무관하게 조회 가능 |
| 관리자 예외 | ✅ | 관리자는 같은 조직의 모든 문서 조회·삭제·ACL 수정 |
| 목록/검색 ACL 일원화 | ✅ | `can_view` 단일 규칙 사용 |
| 빈 ACL 보호 | ✅ | `allowed_roles`가 비면 소유자·관리자만 접근 |
| 한국어 조사 대응 | ✅ | 질의 토큰의 한글 접두어 확장으로 `Qdrant는` ↔ `Qdrant와` 매칭 |
| 검색 결과 제한 | ✅ | 기본 4개 (`search(limit=4)`) |
| 문서 삭제 | ✅ | 업로더 또는 관리자 |
| 문서 ACL 편집 | ✅ | 관리자 전용, `PATCH /v1/documents/{id}` |
| 원문 클라이언트 비노출 | ✅ | 출처에는 ID·제목·청크만 반환 |
| 벡터 검색/Qdrant 연동 | ⛔ | Compose에 Qdrant는 있으나 앱 로직은 미연결 |
| 임베딩 생성 | ⛔ | 임베딩 모델·인덱스 없음 |
| PDF/Word/HTML 파싱 | ⛔ | 텍스트/Markdown만 지원 |
| 문서 재색인/본문 수정 | ⛔ | ACL만 수정 가능 |
| 문서/감사 페이지네이션 | ⛔ | 감사는 `limit`만 지원, 문서 목록은 전체 반환 |
| 객체 스토리지 연동 | ⛔ | 로컬 파일 시스템 사용 |

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
| 감사 로그 조회 | ✅ | 관리자만, 자기 조직 이벤트만 |
| 감사 로그 제한 | ✅ | `limit` 1~500, 기본 100 |
| 문서 ACL 변경 | ✅ | 관리자 인라인 편집 UI + API |
| ACL 변경 감사 | ✅ | `document.acl_updated` 이벤트 |
| 관리자 전용 네비게이션 | ✅ | 멤버에게 설정·ACL 편집 UI 숨김 |
| 런타임 설정 변경 | ⛔ | 모델/정책/저장소 값은 읽기 전용 |
| 사용자·멤버 관리 | ⛔ | 사용자 DB, 초대, 역할 부여 기능 없음 |
| 정책 패턴 편집 | ⛔ | 차단 패턴은 코드/환경 기반 |
| 감사 보존/무결성 정책 | ⛔ | JSONL append-only, 보존·서명·변조 방지 없음 |
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

---

## 6. API 엔드포인트

| Method | Endpoint | 접근 권한 | 설명 |
| --- | --- | --- | --- |
| `GET` | `/health` | 공개 | 상태, 버전, 어댑터, 서버 시각 |
| `GET` | `/v1/models` | 공개 | 사용 가능한 모델 목록 |
| `POST` | `/v1/chat/completions` | 멤버·관리자 | OpenAI 스타일 채팅, SSE 스트리밍 |
| `POST` | `/v1/documents` | 멤버·관리자 | 문서 업로드 (multipart) |
| `GET` | `/v1/documents` | 멤버·관리자 | 권한 범위 내 문서 목록 |
| `GET` | `/v1/documents/search` | 멤버·관리자 | 권한 기반 어휘 검색 |
| `PATCH` | `/v1/documents/{id}` | 관리자 | 문서 ACL 변경 |
| `DELETE` | `/v1/documents/{id}` | 업로더·관리자 | 문서 삭제 |
| `GET` | `/v1/audit-events` | 관리자 | 조직 감사 이벤트 |
| `GET` | `/v1/admin/settings` | 관리자 | 런타임·정책·저장소·카운트 |

### 요청 옵션 (`POST /v1/chat/completions`)

| 필드 | 기본값 | 설명 |
| --- | --- | --- |
| `model` | `demo-local` | 모델 ID |
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
| OIDC/JWT 인증 | ⛔ | 헤더 신원은 개발용, 프로덕션 부적합 |
| Rate limiting | ⛔ | 요청 속도 제한 없음 |
| CSRF/보안 헤더 | ⛔ | 쿠키 인증·CSP·HSTS 등 미적용 |
| 완전한 PII 탐지 | 🔶 | 전화번호 패턴만 예시로 제공 |
| 감사 변조 방지/보존 | ⛔ | 서명·WORM·보존 정책 없음 |
| 비밀 마스킹 로그 | 🔶 | 감사 페이로드 최소화 수준 |

---

## 8. 데이터 · 저장소

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| 문서 JSON 저장 | ✅ | 문서별 `{id}.json`, 원자적 교체 |
| 감사 JSONL 저장 | ✅ | append-only, 재시작 시 복원 |
| 인메모리 문서 인덱스 | ✅ | 시작 시 JSON 로드 |
| 감사 메모리 버퍼 | ✅ | 최근 5,000건 유지 |
| PostgreSQL | 🔶 | Compose에 포함, 앱 로직 미사용 |
| Redis | 🔶 | Compose에 포함, 앱 로직 미사용 |
| Qdrant | 🔶 | Compose에 포함, 벡터 검색 미연동 |
| 오브젝트 스토리지 | ⛔ | S3/MinIO 등 미연동 |
| 데이터 백업 가이드 | ✅ | `docs/operations.md`에 문서·감사·벡터 백업 지침 |

---

## 9. 배포 · 운영

| 기능 | 상태 | 설명 |
| --- | --- | --- |
| Docker Compose | ✅ | API, Web, PostgreSQL, Redis, Qdrant |
| API 이미지 | ✅ | Python 3.12 slim, 비루트 사용자(uid 10001) |
| Web 이미지 | ✅ | Node 22 빌드 + Nginx 1.27 정적 서빙 |
| Nginx SPA 라우팅 | ✅ | `root`/`index`/`try_files` 설정 |
| Nginx API 프록시 | ✅ | `/api/` → `http://api:8000/` |
| Compose 헬스체크 | ✅ | API healthcheck, Web은 API healthy 후 기동 |
| Compose 볼륨 | ✅ | documents, audit, postgres, qdrant |
| Kubernetes 매니페스트 | ✅ | Namespace, Deployment, Service |
| K8s 보안 컨텍스트 | ✅ | 비루트, read-only root, capability drop, seccomp, fsGroup |
| K8s 프로브 | ✅ | readiness / liveness `/health` |
| K8s 데이터 볼륨 | 🔶 | `emptyDir` 스타터, 운영 시 PVC/관리형 저장소로 교체 필요 |
| CI | ✅ | API pytest + Web build |
| E2E 자동화 | 🔶 | Playwright 스크립트 제공, CI 잡에는 미포함 |
| 에어갭 가이드 | ✅ | 이미지·모델·패키지 미러링 지침 |
| 관측성(Langfuse/OTel) | ⛔ | 트레이스 익스포트 미구현 |
| 이미지 서명/SBOM | ⛔ | 공급망 보안 미적용 |

---

## 10. 테스트 · 품질

| 항목 | 상태 | 결과/설명 |
| --- | --- | --- |
| API 단위·통합 테스트 | ✅ | **19 passed** |
| Playwright E2E | ✅ | **10 checks passed** (`npm run test:e2e`) |
| Web 타입체크·빌드 | ✅ | `tsc -b && vite build` 성공 |
| Compose 통합 스모크 | ✅ | 30개 체크 수동 스크립트로 통과 |
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
| `MODEL_TIMEOUT_SECONDS` | `90` | 업스트림 타임아웃 |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | CORS 허용 오리진 (쉼표 구분) |
| `DOCUMENT_STORE` | `/tmp/baryon-ax-documents` | 문서 JSON 저장 경로 |
| `AUDIT_LOG` | `/tmp/baryon-ax-audit/events.jsonl` | 감사 JSONL 경로 |
| `MAX_DOCUMENT_BYTES` | `2000000` | 문서 업로드 최대 크기 |
| `POSTGRES_DB` | `baryon` | Compose PostgreSQL DB |
| `POSTGRES_USER` | `baryon` | Compose PostgreSQL 사용자 |
| `POSTGRES_PASSWORD` | 예시 값 | Compose PostgreSQL 비밀번호 (변경 필수) |

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
| 높음 | Qdrant/임베딩 검색 | 어휘 검색을 벡터 검색으로 교체, 문서 ACL 메타데이터 유지 |
| 중간 | 런타임 설정 편집 | 모델 엔드포인트·정책·검색 limit을 UI에서 변경 |
| 중간 | 사용자/멤버 관리 | 조직 사용자·역할 관리 화면 |
| 중간 | Rate limiting·보안 헤더 | API 미들웨어와 테스트 |
| 중간 | 감사 무결성/보존 | append-only 저장소, 보존 기간, 변조 방지 |
| 중간 | 문서 파싱 확장 | PDF/Word/HTML, 재색인 |
| 낮음 | 툴/에이전트 실행 | LangGraph 기반 승인·재시도·중단 |
| 낮음 | 관측성 | OpenTelemetry/Langfuse 연동 |
| 낮음 | CI E2E/보안 스캔 | Playwright 잡, Trivy/Semgrep |

---

## 14. 검증 기준

```text
API tests           19 passed
Playwright E2E      10 checks passed
Web build           tsc -b && vite build 성공
Compose smoke       30 checks passed (수동 통합 스크립트)
npm audit           0 vulnerabilities
pip check           깨진 요구사항 없음
```

---

## 관련 문서

- [아키텍처](architecture.md)
- [운영 가이드](operations.md)
- [보안 베이스라인](security.md)
- [README](../README.md)
