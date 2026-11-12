# 오픈소스 구성 요소 목록

> 기준 버전: **0.6.0** · 문서 작성일: 2026-11-10  
> 이 문서는 라이선스 법률 자문이 아닙니다. 배포·재배포 전에는 각 프로젝트의 최신 라이선스와 고지를 직접 확인하세요.

## 요약

아키텍처 이미지의 목표 스택 중 상당 부분을 실제로 연결했습니다. Basic Core는 파일 기반으로 동작하고, **full-stack 프로필**은 상태 저장 서비스와 라이브러리를 사용합니다.

| 구분 | 내용 |
| --- | --- |
| **실제 사용 중** | React, Vite, TypeScript, FastAPI, Uvicorn, Pydantic, HTTPX, Nginx, Python, Node.js, Docker, Playwright, pytest |
| **Full-stack 적용** | Ollama, Qdrant + FastEmbed, LlamaIndex, LangGraph, Langfuse, PostgreSQL, Redis, Mem0 |
| **내장 대체** | Guardrails(현재 `PolicyEngine`), Milvus(Qdrant 대안), vLLM/SGLang(Ollama 대안), LiteLLM(선택) |
| **인프라** | Docker Compose Basic Core / full-stack override, Kubernetes 스타터 매니페스트 |

이미지의 L2/L3 구성 요소 중 **LangGraph, LlamaIndex, Guardrails, Mem0는 라이브러리**이고, **Langfuse, Qdrant, Milvus, PostgreSQL, Redis는 서비스**입니다. 라이브러리는 API 프로세스에 내장되므로 Compose 서비스 수와 1:1로 일치하지 않습니다.

---

## 1. 현재 실제 사용 중 (Runtime)

| 구성 요소 | 역할 | 라이선스 | 사용 위치 |
| --- | --- | --- | --- |
| Python 3.11+ | API 런타임 | PSF License | `apps/api` |
| FastAPI | HTTP API 프레임워크 | MIT | `apps/api/src/baryon/main.py` |
| Uvicorn | ASGI 서버 | BSD-3-Clause | API 컨테이너 |
| Pydantic | 요청/응답 검증 | MIT | API 모델 |
| HTTPX | 모델 서버 HTTP 클라이언트 | BSD-3-Clause | `ModelAdapter` |
| React / React DOM | UI | MIT | `apps/web` |
| Vite | 프런트엔드 빌드·개발 서버 | MIT | `apps/web` |
| TypeScript | 타입 시스템 | Apache-2.0 | `apps/web` |
| lucide-react | UI 아이콘 | ISC | `apps/web` |
| react-markdown / remark-gfm / remark-breaks | Markdown 렌더링 | MIT | `apps/web` |
| Nginx | 정적 파일 서빙·API 프록시 | BSD-2-Clause | Web 컨테이너 |
| Node.js | 프런트엔드 빌드 런타임 | MIT | Web 빌드 스테이지 |
| Docker Engine | 컨테이너 빌드·실행 | Apache-2.0 | Compose |
| Kubernetes | 컨테이너 오케스트레이션 | Apache-2.0 | `deploy/kubernetes` (스타터) |
| Playwright | 브라우저 E2E 테스트 | Apache-2.0 | `apps/web/e2e` |
| pytest | Python 테스트 | MIT | `apps/api/tests` |
| Qdrant client / server | 벡터 저장소 | Apache-2.0 | `VectorIndex`, `qdrant` 서비스 |
| FastEmbed | 다국어 임베딩 | Apache-2.0 | 문서/Mem0 임베딩 |
| ONNX Runtime | FastEmbed 추론 | MIT | API 컨테이너 |
| LlamaIndex Core | 청킹 | MIT | `chunk_text` |
| LangGraph | 정책·검색 오케스트레이션 | MIT | `ChatPipeline` |
| Langfuse SDK | 트레이싱 | MIT | `Tracer` |
| Mem0 | 사용자 메모리 | Apache-2.0 | `MemoryService` |
| psycopg | PostgreSQL 드라이버 | LGPL-3.0 | `AuditSink` |
| redis-py | Redis 클라이언트 | MIT | `RateLimiter` |
| Ollama Python client | 메모리 LLM 호출 | MIT | `MemoryService` |

> Docker Desktop은 별도 상용 약관이 적용될 수 있습니다. 서버 배포에는 Docker Engine을 사용하는 구성이 일반적입니다.

---

## 2. 이미지의 목표 스택 (Target Architecture)

| 구성 요소 | 레이어 | 역할 | 대표 라이선스 | 현재 상태 | 비고 |
| --- | --- | --- | --- | --- | --- |
| LangGraph | L3 실행 | 에이전트 워크플로·상태·체크포인트 | MIT | ✅ 적용 | 정책·검색 준비 그래프 (체크포인트는 미적용) |
| Guardrails | L3 실행 | 입출력 정책·안전성 검사 | Apache-2.0 (NeMo Guardrails / Guardrails AI) | 🔶 내장 대체 | `PolicyEngine` + PII 마스킹, NeMo는 선택 |
| Langfuse | L3 관측 | LLM 트레이스·평가·비용 관측 | MIT (core), EE 기능 상용 | ✅ 적용 | 셀프호스팅 v2 + SDK |
| Qdrant | L2 지식 | 벡터 검색 | Apache-2.0 | ✅ 적용 | `deps` 프로필 + FastEmbed |
| Milvus | L2 지식 | 벡터 검색 대안 | Apache-2.0 | ⛔ 미적용 | Qdrant 대체 후보 |
| LlamaIndex | L2 지식 | 문서 수집·파싱·RAG 파이프라인 | MIT | ✅ 적용 | `SentenceSplitter` 청킹 |
| Redis | L2 세션 | 캐시·세션·메모리 | 버전별 상이 (아래 참고) | ✅ 적용 | `deps` 프로필, 채팅 레이트 리밋 |
| Mem0 | L2 세션 | 에이전트 장기 메모리 | Apache-2.0 | ✅ 적용 | Qdrant + FastEmbed + Ollama |
| PostgreSQL | L1 인프라 | 메타데이터·스레드·감사 저장 | PostgreSQL License | ✅ 적용 | `deps` 프로필, 감사 이벤트 |
| vLLM | L1 모델 | GPU LLM 서빙 | Apache-2.0 | ✅ 적용 | B300 GPU 7, Qwen3.5-27B |
| SGLang | L1 모델 | 고속 LLM 서빙 | Apache-2.0 | ⛔ 미적용 | Linux/GPU 대안 |
| Ollama | L1 모델 | 로컬 모델 실행 | MIT | ✅ 적용 | `qwen2.5:7b` |
| LiteLLM | L1 모델 | 모델 게이트웨이 | MIT | ⛔ 미적용 | OpenAI 호환 게이트웨이 후보 |
| Chat Web UI | L4 UI | 대화·지식·메모리·거버넌스 화면 | MIT (이 저장소) | ✅ 적용 | React + Vite |

---

## 3. 라이선스 주의사항

### Redis

Redis 라이선스는 **버전에 따라 다릅니다.**

| 버전 | 라이선스 | OSI 승인 |
| --- | --- | --- |
| Redis 7.2 이하 | BSD-3-Clause | ✅ |
| Redis 7.4 ~ 7.8 | RSALv2 또는 SSPLv1 | ❌ (source-available) |
| Redis 8 이상 | RSALv2, SSPLv1, AGPLv3 중 선택 | ✅ (AGPLv3 선택 시) |

- Compose의 `redis:7-alpine`은 7.x 라인의 최신 태그를 따르므로, 실제 배포 버전의 라이선스를 확인해야 합니다.
- BSD 조건을 유지하려면 **Valkey**(BSD-3-Clause)를 대안으로 검토할 수 있습니다.
- Redis를 외부에 관리형 서비스로 제공하거나 Redis Inc.와 경쟁하는 형태로 임베드하는 경우 별도 상용 라이선스가 필요할 수 있습니다.

### Langfuse

- 핵심 제품은 MIT, `/ee` 디렉터리의 일부 엔터프라이즈 기능(SCIM, 확장 감사 로깅, 보존 정책, 프로젝트 단위 RBAC 등)은 상용 라이선스입니다.
- 셀프호스팅 시 EE 기능을 사용하지 않으면 MIT 범위로 운영할 수 있습니다.

### Guardrails

- 이미지의 "Guardrails"는 특정 프로젝트명이 아니라 일반화된 표현입니다.
- **NVIDIA NeMo Guardrails**와 **Guardrails AI** 모두 Apache-2.0이지만, 기능·구성 방식이 다르므로 도입 시 하나를 명확히 선택해야 합니다.

### 모델 가중치

- vLLM/SGLang/Ollama는 서빙 소프트웨어일 뿐이며, 실제 LLM 가중치는 별도 라이선스(예: Llama Community License, Apache-2.0, MIT)를 따릅니다.
- 상용 배포 전 모델 가중치 라이선스를 별도로 검토하세요.

### Docker

- Docker Engine/Moby는 Apache-2.0입니다.
- Docker Desktop은 상용 약관이 적용될 수 있으므로 기업 배포 환경에서는 별도 확인이 필요합니다.

---

## 4. 현재 구현과 남은 작업

| 목표 오픈소스 | 현재 적용 | 남은 작업 |
| --- | --- | --- |
| LangGraph | 정책 검사 + 검색 준비 그래프 | 체크포인트, 툴 루프, 승인·재시도 |
| Guardrails | `PolicyEngine` 금지 패턴 + PII 마스킹 | NeMo/Guardrails AI, 조직별 정책 룰셋 |
| Langfuse | 셀프호스팅 v2 + trace/generation | 평가·비용 대시보드, 보존 정책 |
| Qdrant | FastEmbed + 벡터 검색 + ACL 필터 | Milvus/hybrid 검색 비교, 대규모 튜닝 |
| LlamaIndex | `SentenceSplitter` 청킹 | PDF/Word/HTML 파싱, 정교한 노드 구성 |
| PostgreSQL | 감사 이벤트 테이블 | 스레드·메시지·사용자·조직 영속화 |
| Redis | 채팅 레이트 리밋 | 세션 캐시, 분산 rate limit |
| Mem0 | 사용자 메모리 저장/검색 | 채팅 컨텍스트 자동 주입, 보존 정책 |
| Ollama | `qwen2.5:7b` | vLLM/SGLang/LiteLLM 비교·프로필 |

---

## 5. 다음 도입 우선순위

| 순서 | 항목 | 이유 |
| --- | --- | --- |
| 1 | OIDC/JWT | 개발용 헤더를 실제 IdP 검증으로 교체 |
| 2 | PostgreSQL 스레드/메시지 | 대화 이력·재개·감사 연결의 기반 |
| 3 | Guardrails (NeMo/Guardrails AI) | 정교한 정책·안전성 검사 |
| 4 | 런타임 설정·사용자 관리 | 모델/정책/검색 limit, 조직 사용자 역할 |
| 5 | 감사 무결성/보존 | 서명·WORM·삭제 정책 |
| 6 | Milvus/SGLang/LiteLLM | 대안 스택 평가와 프로필 추가 (vLLM은 B300에서 검증) |

---

## 6. UI에서 보기

설정 → **오픈소스** 탭에서 구성 요소·라이선스·링크를 확인할 수 있습니다. API는 `GET /v1/open-source`이며, 데이터는 `apps/api/src/baryon/open_source.py`와 이 문서를 함께 갱신합니다.

## 7. 관련 문서

- [기능 목록](features.md)
- [전체 스택 실행 가이드](full-stack.md)
- [B300 호스트 배포](b300-host-deploy.md)
- [Strata/로컬 AI 조사](research/strata-and-local-ai-shortlist.md)
- [아키텍처](architecture.md)
- [운영 가이드](operations.md)
- [보안 베이스라인](security.md)
- [README](../README.md)
