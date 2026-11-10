# clawfit 등재 제안서 — Baryon AX

> 작성일: 2026-11-11
> 대상: [`hongsw/clawfit`](https://github.com/hongsw/clawfit) — `data/tools_registry.json`, `data/feature_matrix.json`, `docs/reference-levels.md`
> 상태: **제안 (등재 보류)** — 공개 저장소·star·결정론적 비용 데이터 확보 후 PR

---

## 0. 왜 지금 등재가 불가한가

| clawfit 요건 | 현재 상태 | 해결 방법 |
| --- | --- | --- |
| 공개 저장소 | 비공개(로컬 Git, remote 없음) | 저장소 공개 |
| ≥5,000★ | 0 | 공개 후 채택 또는 "reference implementation 예외" 제안 |
| 결정론적 `cost_per_call`/`latency` | 단일값 없음 (자체 호스팅) | GPU-hour 비용식 + sustained throughput 실측 |
| 카테고리 슬롯 | `governed_ai_platform` 없음 | 신설 제안 (LibreChat 사례와 함께) |

→ 이 문서는 **PR 본문 + research-watch 첨부 + 스키마 변경 제안**을 한 번에 처리하기 위한 초안이다.

---

## 1. `tools_registry.json` 등재 제안

```json
{
  "id": "baryon-ax",
  "name": "Baryon AX",
  "category": "governed_ai_platform",
  "level": "6a",
  "license": "MIT",
  "primary_language": "Python",
  "url": "",
  "source_type": "github",
  "comparison_priority": "supplemental",
  "notes": "On-premises, model-agnostic knowledge and grounded-chat platform. Retrieval with ACL enforced inside the query, metadata-only citations, policy checks with chunk-boundary-safe PII masking, PostgreSQL+JSONL audit events, Redis rate limiting, Langfuse tracing, Mem0 memory. Verified against Ollama qwen2.5:7b and vLLM Qwen3.5-27B on a single B300 GPU. Korean-first bilingual UI. Basic Core (Compose) plus full-stack override and Kubernetes starter manifests. No MCP or tool execution yet; identity is a development header adapter pending OIDC.",
  "org_fit": {
    "min_maturity": 4,
    "max_maturity": 10,
    "optimal_maturity": 7,
    "features": [
      "rag",
      "document-ingestion",
      "self-hosted",
      "offline",
      "governance",
      "audit-logs",
      "acl",
      "observability",
      "memory",
      "citations",
      "team-sharing"
    ],
    "pricing_tier": "free",
    "tasks": [
      "research",
      "qa",
      "summarization",
      "knowledge-management"
    ],
    "roles": [
      "exec",
      "analyst",
      "developer",
      "devops"
    ],
    "team_size": [
      "mid",
      "large"
    ],
    "network": "offline",
    "setup_complexity": "medium",
    "latency": "low"
  }
}
```

**입력값 근거**

| 필드 | 근거 |
| --- | --- |
| `level: "6a"` | Qdrant + FastEmbed + LlamaIndex chunking + 인용 — retrieval-native 지식 계층이 제품의 핵심 루프 |
| `license: MIT` | 저장소 `LICENSE` 확인 |
| `primary_language: Python` | FastAPI API + LangGraph. UI는 React/TypeScript |
| `comparison_priority: supplemental` | 직접 대체재(AnythingLLM)가 있으므로 `core` 아님 |
| `optimal_maturity: 7` | 감사·ACL·레이트리밋을 요구하는 조직(중~대규모)이 최적 |
| `network: offline` | 기본 프로파일이 온프레미스 vLLM/Ollama. 외부 API 선택 시 `hybrid` |
| `latency: low` | 실측 0.50~0.58초(짧은 응답). **sustained 측정 전까지 잠정값** |
| `setup_complexity: medium` | Basic Core는 `docker compose up`. full-stack은 `high` |

---

## 2. `feature_matrix.json` 추가 제안

clawfit의 현재 feature 목록은 코딩 에이전트 중심이라 거버넌스 지식 플랫폼을 표현할 필드가 없다. 아래 8개를 추가 제안한다.

| `feature` | `value` | 정의 |
| --- | --- | --- |
| `retrieval_path_acl` | `true` | ACL을 검색 쿼리 단계에서 강제하고 검색 후 재검증 |
| `citation_metadata_only` | `true` | 원문이 아닌 문서 ID·제목·청크 번호만 클라이언트에 반환 |
| `audit_events` | `true` | 감사 이벤트를 PG + JSONL로 이중 기록, 관리자 조회 API 제공 |
| `pii_masking` | `true` | 스트리밍 청크 경계에서도 안전한 마스킹 |
| `org_isolation` | `true` | 조직 단위 문서·메모리·레이트리밋 격리 |
| `model_prompt_profiles` | `true` | 모델별 시스템 프롬프트 프로필 + thinking 토글 |
| `observability_tracing` | `true` | Langfuse trace/generation에 user·org·model 메타 연결 |
| `mcp_server` | `false` | **미구현** — 로드맵 1순위 |

예시 행:

```json
[
  {
    "tool_id": "baryon-ax",
    "feature": "retrieval_path_acl",
    "value": true,
    "support_level": "official",
    "verification_status": "verified",
    "evidence_url": "",
    "evidence_note": "Retrieval applies organization/role/owner filters in the query and rechecks after retrieval; documented in docs/security.md.",
    "verified_at": "2026-11-11",
    "tags": ["governance", "retrieval"]
  },
  {
    "tool_id": "baryon-ax",
    "feature": "mcp_server",
    "value": false,
    "support_level": "not_supported",
    "verification_status": "verified",
    "evidence_url": "",
    "evidence_note": "No MCP server or tool execution loop in the Basic Core v0.6 reference implementation.",
    "verified_at": "2026-11-11",
    "tags": ["capability-gap"]
  }
]
```

---

## 3. 스키마 변경 제안

### 3-1. 신규 카테고리: `governed_ai_platform`

현재 `category` 값은 도구 유형(에이전트, 메모리, 지식관리, 관측성…)이지만, **테넌시·감사·정책을 1급으로 갖는 배포 단위**를 표현할 슬롯이 없다. `governed_ai_platform`은 다음을 만족하는 도구를 담는다.

- 조직/테넌트 격리가 제품 기본값
- 감사 이벤트 또는 정책 엔진이 요청 경로에 존재
- 자체 호스팅 배포 가이드(Compose/K8s) 제공
- 모델 백엔드를 교체 가능

후보: Baryon AX, LibreChat(`interface` 강도), Apache Maka(`orchestration` 강도), AnythingLLM(`advisory` 강도).

### 3-2. 신규 축: `governance_enforcement`

`features`의 `governance` 불리언은 "정책 문서를 제공한다"와 "정책을 요청 경로에서 강제한다"를 구분하지 못한다.

| 값 | 의미 | 예시 |
| --- | --- | --- |
| `advisory` | 문서·가이드로만 제공 | AnythingLLM |
| `interface` | UI/인증 계층에서 강제 | LibreChat (RBAC/LDAP) |
| `retrieval-path` | 검색·모델 컨텍스트 진입 전에 강제 | **Baryon AX** |
| `orchestration` | 실행 오케스트레이션 계층에서 강제 | Apache Maka, DureClaw |

### 3-3. 신규 비용 모델: `cost_model: self_hosted_gpu`

`cost_per_call`이 없는 자체 호스팅 도구를 등재하려면 대체 필드가 필요하다. 다만 **선결 문제**: `clawfit/registry/hardware.json`의 온프레미스 항목이 모두 `cost_per_hour: 0.0`이므로, 어떤 비용식을 넣어도 0으로 붕괴한다. 도구 스키마만 고쳐서는 해결되지 않는다.

**함께 제안하는 하드웨어 스키마 변경**

```json
{
  "id": "on-prem-b300-1gpu",
  "name": "NVIDIA B300 (single GPU slice)",
  "type": "on-prem",
  "gpu": true,
  "ram_gb": 275,
  "cost_per_hour": 0.0,
  "amortized_cost_per_gpu_hour": null,
  "amortization_note": "capex/3y + power + cooling; null = not yet modeled",
  "latency": "low"
}
```

**도구 스키마**

```json
{
  "cost_model": "self_hosted_gpu",
  "hardware_ref": "on-prem-b300-1gpu",
  "tokens_per_second_sustained": null,
  "cost_formula": "amortized_cost_per_gpu_hour / (3.6 * tokens_per_second_sustained)",
  "hardware_reference": "B300 GPU 7, TP1, max-model-len 32768, gpu-memory-utilization 0.90",
  "measured": {
    "model": "Qwen3.5-27B",
    "vram_used_mib": 246030,
    "vram_total_mib": 275040,
    "non_streaming_latency_s": 0.50,
    "streaming_latency_s": 0.58,
    "streaming_tokens": 39,
    "trace_latency_s": 0.53,
    "note": "short-response verification run, not a sustained benchmark"
  }
}
```

---

## 4. clawfit 데이터 수정 요청 (검증 완료)

| 대상 | 현재 값 | 수정값 | 근거 |
| --- | --- | --- | --- |
| `tencent-weknora.license` | `Apache-2.0` | `MIT` | upstream `LICENSE`: "WeKnora is licensed under the MIT except for the third-party components listed below." GitHub API는 `NOASSERTION`(복합 라이선스) |
| `langfuse.license` | `MIT` | `MIT (core) + EE` | 저장소에 별도 라이선스 디렉터리 존재. GitHub API `NOASSERTION` |
| LibreChat URL | (문서 내 구 URL) | `https://github.com/LibreChat-AI/LibreChat` | 저장소 이전 확인 (46,943★, MIT) |

---

## 5. PR 본문 초안

```markdown
## Add research-watch: Baryon AX (governed on-premises knowledge platform)

Adds `docs/research-watch/2026-11-11-baryon-ax-governed-onprem-knowledge-platform.md`
and proposes a registry entry + two schema extensions.

### Why this matters
- No current registry entry closes retrieval, policy, audit, tenancy, observability,
  and memory inside one self-hosted deployment.
- LibreChat was excluded for lacking a deterministic cost point. Baryon AX offers a
  GPU-hour cost formula plus measured VRAM/latency, giving self-hosted platforms a
  path into the registry without weakening the deterministic-cost rule.
- Pairs with the tracked Chaltteok entry (L6b) from the same org: L6b on the device,
  L6a on the server. Documents a two-tier knowledge architecture the taxonomy does
  not currently express.

### Proposed changes
1. `docs/research-watch/2026-11-11-baryon-ax-governed-onprem-knowledge-platform.md` (new)
2. `data/tools_registry.json` — new entry `baryon-ax` (pending public repo + benchmark)
3. `data/feature_matrix.json` — 8 governance/knowledge feature fields
4. Schema proposal: `category: governed_ai_platform`, `governance_enforcement` axis,
   `cost_model: self_hosted_gpu`

### Data fixes
- `tencent-weknora.license`: Apache-2.0 → MIT (upstream LICENSE verified)

### Not included
- Registry promotion: repository is not public and has no sustained throughput
  benchmark yet. This PR is an evidence entry, not a scored entry.
```

---

## 6. 실행 순서

| # | 작업 | 산출물 | 담당 |
| --- | --- | --- | --- |
| 1 | sustained throughput 벤치마크 (고정 프롬프트 3회, median) | `scripts/measure_throughput.py --json docs/research/clawfit/throughput.json` | 인프라 |
| 2 | 저장소 공개 + README에 research-watch 링크 | GitHub repo | 메인테이너 |
| 3 | MCP 서버 노출 (검색·메모리·감사 조회) | `apps/api` 확장 | 개발 |
| 4 | OIDC/JWT 어댑터 | 인증 계층 | 개발 |
| 5 | clawfit PR (research-watch + 데이터 수정) | PR | 메인테이너 |
| 6 | `governed_ai_platform` 카테고리 제안 (LibreChat과 공동) | clawfit issue | 메인테이너 |

> 1·2가 끝나면 5는 즉시 가능하다. 3·4는 등재가 아니라 경쟁 요건이므로 별도 트랙으로 진행한다.
