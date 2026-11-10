# 오픈소스 구성 요소 목록

> 기준 버전: **0.6.0** · 문서 작성일: 2026-11-10  
> 이 문서는 라이선스 법률 자문이 아닙니다. 배포·재배포 전에는 각 프로젝트의 최신 라이선스와 고지를 직접 확인하세요.

## 요약

아키텍처 이미지에는 **목표(target) 오픈소스 스택**이 많이 표시되어 있지만, Basic Core v0.6이 실제로 실행·사용하는 구성 요소는 다음 두 가지로 나뉩니다.

| 구분 | 내용 |
| --- | --- |
| **실제 사용 중** | React, Vite, TypeScript, FastAPI, Uvicorn, Pydantic, HTTPX, Nginx, Python, Node.js, Docker |
| **내장 구현으로 대체** | 정책(Guardrails 대체), 검색(LlamaIndex·Qdrant 대체), 감사(Langfuse 대체), 세션(Redis/Mem0 대체), 저장(PostgreSQL 대체) |
| **선택적 placeholder** | PostgreSQL, Redis, Qdrant (`--profile deps`) — 컨테이너는 있으나 앱 미연결 |
| **개발/테스트** | Playwright, pytest |

이미지의 L2/L3 구성 요소 중 **LangGraph, LlamaIndex, Guardrails, Mem0는 라이브러리**이고, **Langfuse, Qdrant, Milvus, PostgreSQL, Redis는 서비스**입니다. 라이브러리는 API 프로세스에 내장될 수 있으므로 Compose 서비스 수와 1:1로 일치하지 않습니다.

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
| Nginx | 정적 파일 서빙·API 프록시 | BSD-2-Clause | Web 컨테이너 |
| Node.js | 프런트엔드 빌드 런타임 | MIT | Web 빌드 스테이지 |
| Docker Engine | 컨테이너 빌드·실행 | Apache-2.0 | Compose |
| Kubernetes | 컨테이너 오케스트레이션 | Apache-2.0 | `deploy/kubernetes` (스타터) |
| Playwright | 브라우저 E2E 테스트 | Apache-2.0 | `apps/web/e2e` |
| pytest | Python 테스트 | MIT | `apps/api/tests` |

> Docker Desktop은 별도 상용 약관이 적용될 수 있습니다. 서버 배포에는 Docker Engine을 사용하는 구성이 일반적입니다.

---

## 2. 이미지의 목표 스택 (Target Architecture)

| 구성 요소 | 레이어 | 역할 | 대표 라이선스 | 현재 상태 | 비고 |
| --- | --- | --- | --- | --- | --- |
| LangGraph | L3 실행 | 에이전트 워크플로·상태·체크포인트 | MIT | ⛔ 미사용 | Python 라이브러리, FastAPI 핸들러로 대체 |
| Guardrails | L3 실행 | 입출력 정책·안전성 검사 | Apache-2.0 (NeMo Guardrails / Guardrails AI) | 🔶 내장 대체 | `PolicyEngine`이 단순 패턴 검사 수행 |
| Langfuse | L3 관측 | LLM 트레이스·평가·비용 관측 | MIT (core), EE 기능 상용 | ⛔ 미사용 | JSONL 감사 로그로 대체 |
| Qdrant | L2 지식 | 벡터 검색 | Apache-2.0 | ⛔ placeholder | `deps` 프로필, 어휘 검색으로 대체 |
| Milvus | L2 지식 | 벡터 검색 대안 | Apache-2.0 | ⛔ 미사용 | Qdrant 대체 후보 |
| LlamaIndex | L2 지식 | 문서 수집·파싱·RAG 파이프라인 | MIT | 🔶 내장 대체 | 900자 단순 청킹 구현 |
| Redis | L2 세션 | 캐시·세션·메모리 | 버전별 상이 (아래 참고) | ⛔ placeholder | `deps` 프로필, 브라우저 상태로 대체 |
| Mem0 | L2 세션 | 에이전트 장기 메모리 | Apache-2.0 | ⛔ 미사용 | 라이브러리/서비스 |
| PostgreSQL | L1 인프라 | 메타데이터·스레드·감사 저장 | PostgreSQL License | ⛔ placeholder | `deps` 프로필, JSON 파일로 대체 |
| vLLM | L1 모델 | GPU LLM 서빙 | Apache-2.0 | 🔶 외부 | `MODEL_BASE_URL`로 연결 |
| SGLang | L1 모델 | 고속 LLM 서빙 | Apache-2.0 | 🔶 외부 | `MODEL_BASE_URL`로 연결 |
| Ollama | L1 모델 | 로컬 모델 실행 | MIT | 🔶 외부 | `MODEL_BASE_URL`로 연결 |
| LiteLLM | L1 모델 | 모델 게이트웨이 | MIT | ⛔ 미사용 | OpenAI 호환 게이트웨이 후보 |
| Chat Web UI | L4 UI | 대화·지식·거버넌스 화면 | MIT (이 저장소) | ✅ 사용 | React + Vite |

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

## 4. 현재 구현과 오픈소스 대체 지점

| 목표 오픈소스 | 현재 Basic Core 구현 | 교체 시점 |
| --- | --- | --- |
| LangGraph | FastAPI 핸들러의 단일 요청 처리 | 툴 루프·승인·재시도·체크포인트가 필요할 때 |
| Guardrails | `PolicyEngine`의 금지 패턴 + PII 마스킹 | 정책 룰셋·분류기·조직별 정책이 필요할 때 |
| Langfuse | JSONL 감사 로그 + `/v1/audit-events` | 트레이스·평가·비용 관측이 필요할 때 |
| Qdrant / Milvus | 어휘 검색 + 조직/역할 ACL | 임베딩 기반 의미 검색이 필요할 때 |
| LlamaIndex | 900자 청킹 + 텍스트/Markdown 파서 | PDF/Word/HTML 수집·정교한 청킹이 필요할 때 |
| PostgreSQL | 문서 JSON + 감사 JSONL | 스레드·사용자·조직·감사 영속화가 필요할 때 |
| Redis / Mem0 | 브라우저 메시지 상태 | 서버 세션·장기 메모리·캐시가 필요할 때 |

---

## 5. 도입 우선순위 제안

| 순서 | 구성 요소 | 이유 |
| --- | --- | --- |
| 1 | PostgreSQL | 스레드·사용자·감사 영속화의 기반, 가장 먼저 필요한 상태 저장소 |
| 2 | Qdrant + 임베딩 | 검색 품질 개선, 현재 어휘 검색의 한계 보완 |
| 3 | Langfuse | 트레이스·비용·품질 관측 확보 |
| 4 | LangGraph | 툴 실행·승인·재시도·중단 가능한 워크플로 |
| 5 | Guardrails (NeMo/Guardrails AI) | 정교한 정책·안전성 검사 |
| 6 | Redis / Mem0 | 세션 캐시·장기 메모리 |

---

## 6. 관련 문서

- [기능 목록](features.md)
- [아키텍처](architecture.md)
- [운영 가이드](operations.md)
- [보안 베이스라인](security.md)
- [README](../README.md)
