# clawfit 지형에서의 Baryon AX 포지셔닝

> 조사일: 2026-11-11
> 조사 대상: [`hongsw/clawfit`](https://github.com/hongsw/clawfit) — `data/tools_registry.json` 107개 도구, `data/feature_matrix.json` 22개 도구, `docs/research-watch/` 1,053개 문서, `docs/reference-levels.md` 정본 분류
> 목적: clawfit에 등록된 리포 중 Baryon AX와 유사한 것을 식별하고, 이 저장소의 의의·차이·등재 전략을 정리한다.
> 수치 기준: GitHub API 2026-11-11 확인. clawfit `tools_registry.json`의 값은 등재 시점 값이라 현재 star 수와 다를 수 있다.

---

## TL;DR

1. **Baryon AX는 clawfit에 등재되어 있지 않다.** `baryon`으로 전체 검색하면 잡히는 것은 **찰떡AI(L6b, Baryon Labs)** 와 **DureClaw(L2)** 뿐이다. 즉 clawfit이 아는 "우리 것"은 데스크톱 제품과 오케스트레이터이고, 이 서버 플랫폼은 지도에 없다.
2. **가장 가까운 비교군은 AnythingLLM(68.2k★), LibreChat(46.9k★), Tencent WeKnora(37.1k★), Odysseus** 이다. 넷 다 "자체 호스팅 + 문서 근거 + 모델 무관"을 공유하지만, **정책·감사·조직 ACL·관측성을 한 배포본에서 닫은 사례는 없다.**
3. **이 리포의 의의는 "부품의 집합을 운영 모델로 바꾼 레퍼런스"** 다. clawfit 등재물은 대부분 한 레이어만 담당한다(Qdrant·Langfuse·Mem0·Qwen…). Baryon AX는 L6a(검색/인용) + L3(정책/감사) + L7(채팅 UI) + L1(모델 라우팅) + L4a(메모리) + L5(관측)를 하나의 수직 슬라이스로 묶는다.
4. **등재의 실제 장벽은 스키마가 아니라 공개 상태다.** clawfit은 공개 저장소 + 5k★ 임계값을 요구한다. 현재 이 저장소는 로컬 저장소이며 star가 0이다. **선행 조건: 공개 + research-watch 문서 + "reference implementation" 예외.**
5. **라이선스 정합성 이슈 발견**: clawfit은 WeKnora를 `Apache-2.0`으로 적었지만 실제 LICENSE는 **MIT(제3자 컴포넌트 별도)** 다. 등재 문서 작성 전 수정 요청이 필요하다.

---

## 1. clawfit 지도 현황

| 항목 | 값 | 의미 |
| --- | --- | --- |
| `tools_registry.json` | 107개 | 스코어링 대상 도구. `org_fit`(maturity/tasks/roles/network/features) 포함 |
| `feature_matrix.json` | 24행 / 22도구 | 기능 근거 매트릭스. **커버리지가 코딩 에이전트에 치우쳐 있음** |
| `docs/research-watch/` | 1,053개 | 개별 딥다이브. 등재 전 단계의 증거 저장소 |
| 정본 레벨 | L1~L7 | L6=지식/증거 인프라(L6a retrieval, L6b LLM-native), L7=휴먼 인터페이스 |
| 등재 실질 기준 | 공개 repo + ≥5,000★ + 결정론적 cost/latency | **자체 호스팅 멀티 프로바이더 플랫폼은 구조적으로 진입 불가** |

> LibreChat은 46.9k★, MIT임에도 등재되지 않았다. clawfit이 기록한 사유: "self-hosted multi-provider 플랫폼은 `cost_per_call`/`latency` 단일 데이터 포인트가 없고, `provider router` 카테고리가 스키마에 없다." → **Baryon AX도 동일 사유로 막힌다.**

---

## 2. clawfit이 보는 "우리 것"

| 제품 | clawfit 등재 | 레벨 | clawfit의 평가 |
| --- | --- | --- | --- |
| **찰떡AI (Chaltteok)** | `reference-levels.md` L6b, research-watch 문서 1건 | **L6b primary** (LLM이 KB 유지) / L4c secondary (host-protective MCP 라우팅) | "도메인 특화 + 로컬 전용 + 규제 대응" L6b 최초 신호. 메인 앱 비공개(0★)로 등재 보류 |
| **DureClaw** | `tools_registry.json` (`comparison_priority: core`) | L2 `distributed_orchestration` | 크로스머신 멀티에이전트, 이기종 백엔드, MIT |
| **Baryon AX (이 저장소)** | **없음** | — | — |

**해석**: clawfit 지도에서 우리 회사는 "데스크톱 지식 도구(L6b)"와 "분산 오케스트레이터(L2)"로 표현된다. **서버·조직 단위의 지식/거버넌스 계층이 비어 있고**, 그 자리가 이 저장소의 자리다.

---

## 3. 유사 리포 — 3개 군

### 1군. 직접 비교군 (같은 문제: 자체 호스팅 문서 Q&A + 멀티유저 권한)

| 리포 | clawfit 레벨 | 라이선스 | ★ | 우리와의 관계 |
| --- | --- | --- | --- | --- |
| **AnythingLLM** (`Mintplex-Labs/anything-llm`) | L1 `ai_platform` | MIT | 68.2k | **최대 대체재.** 문서챗 RAG, 40+ provider, 노코드 에이전트, MCP, 멀티유저 권한. 거버넌스(감사/정책/PII/레이트리밋)는 얇음 |
| **LibreChat** (`LibreChat-AI/LibreChat`) | **미등재** (research-watch만) | MIT | 46.9k | **가장 강한 경쟁자.** MCP 3-transport + Skills + Subagents + RBAC/LDAP + 멀티 프로바이더. L6 인터페이스 계층 |
| **Tencent WeKnora** (`Tencent/WeKnora`) | L5 `knowledge_management` | **MIT** (clawfit 표기 Apache-2.0은 오류) | 37.1k | 온프레미스 RAG **백엔드**. 문서 인제스트 + 인증, no egress. UI·정책·감사 없음 |
| **Odysseus** (`pewdiepie-archdaemon/odysseus`) | L1 primary / L6 secondary | MIT | — | 프라이버시 우선 셀프호스팅 워크스페이스. 하드웨어 인식 모델 추천(270+), 메일/캘린더. 개인·소규모 |

### 2군. 레이어가 겹치는 이웃 (부품 또는 인접 계층)

| 리포 | 레벨 | 겹치는 지점 | 차이 |
| --- | --- | --- | --- |
| **Langfuse** | L5 | 우리 스택에 **내장** | 경쟁이 아니라 구성요소 |
| **cognee** | L4a | 지식그래프 메모리 | 우리는 Mem0+Qdrant(벡터+프로필). 그래프 추론 없음 |
| **OpenMemory / Supermemory / GBrain** | L4a | 메모리 계층 | Supermemory는 클라우드 지향, GBrain은 개인용 Markdown |
| **Apache Maka** | L3 | `local-first` + `audit-logs` + `on-prem` | **거버넌스 어휘가 가장 겹치는 이웃.** 단 태스크 오케스트레이션이지 지식/RAG 아님. ASF 거버넌스 신뢰도 우위 |
| **DureClaw** | L2 | 온프레미스 + MCP + 이기종 백엔드 | 대상이 개발자 플릿/코드 실행. 지식·인용·ACL 없음 |
| **QM (YC)** | L2 | 팀 하네스, 3단 보안 포스처, 관리자 스킬 승격 | 거버넌스 **UX** 벤치마크. 지식 계층 없음 |
| **Rowboat** | L1 | 메모리 네이티브 coworker | RAG/감사/ACL 없음 |

### 3군. 같은 제품군, 다른 버티컬 (우리 것)

| | 찰떡AI (L6b) | Baryon AX (L6a + L3) |
| --- | --- | --- |
| 형태 | Windows 데스크톱 (Tauri) | 서버 / Docker Compose / K8s 스타터 |
| 지식 유지 주체 | **LLM이 지식그래프를 갱신** | **retrieval 파이프라인 + 사람이 문서 ACL 관리** |
| 사용자 | 단일 사용자, 한국 SMB | 조직(테넌트), 역할 기반 |
| 데이터 위치 | 온디바이스 전용 | 온프레미스 서버, no egress 가능 |
| 거버넌스 | 로컬·규제 준수 논리 | 감사 이벤트, 조직 격리, 역할 ACL, PII, 레이트리밋 |

→ **"개인 지식은 LLM이 유지(L6b), 조직 지식은 retrieval이 유지(L6a)"** 라는 2-tier 포트폴리오. clawfit 정본에 없는 관점이다.

---

## 4. 기능 비교

| 요구 | AnythingLLM | WeKnora | LibreChat | Maka | **Baryon AX** |
| --- | --- | --- | --- | --- | --- |
| 문서 RAG + **출처 인용** | ○ | ○ | △ | ✕ | **○ (문서 ID·제목·청크만 노출, 원문 미반환)** |
| 조직/역할 **문서 ACL** (검색 쿼리 단계 강제) | △ | △ | △ (RBAC) | ✕ | **○ (검색 후 재검증까지)** |
| **정책 엔진** (입출력 검사, PII 마스킹) | ✕ | ✕ | ✕ | ✕ | **○ (스트리밍 청크 경계 안전)** |
| **감사 이벤트** (PG + JSONL 미러, 관리자 조회) | ✕ | ✕ | ✕ | ○ | **○** |
| 레이트 리밋 (사용자·조직 단위) | ✕ | ✕ | △ | ✕ | **○ (Redis fixed window)** |
| 관측성 (Langfuse trace 연동) | ✕ | ✕ | ✕ | ✕ | **○** |
| 메모리 (Mem0) — 검색 인덱스와 분리 | △ | ✕ | ✕ | ✕ | **○** |
| 모델 무관 라우팅 (OpenAI 호환) | ○ (40+) | ○ | ○ (15+) | △ | **○ (vLLM/Ollama 실측 + 모델별 프롬프트 프로필)** |
| **MCP / 툴 실행** | **○** | ✕ | **○** | ✕ | **✕ (최대 약점)** |
| 문서 파서 | PDF/DOCX 등 | PDF/DOCX/OCR | 파일 첨부 | ✕ | **UTF-8 text/Markdown만** |
| 대화 영속화 | ○ | ○ | ○ | ○ | **⛔ (브라우저 메모리만)** |
| 인증 | ○ | ○ | OAuth2/LDAP | △ | **🔶 개발용 헤더 (OIDC 미구현)** |

---

## 5. 이 리포의 의의

1. **"부품의 집합"을 "운영 모델"로 바꾼 레퍼런스.**
   clawfit 등재물은 한 레이어만 담당하는 부품이 대부분이다. 이 저장소는 Qdrant·FastEmbed·LangGraph·LlamaIndex·Langfuse·PostgreSQL·Redis·Mem0를 **하나의 수직 슬라이스**로 묶고 `docker compose up`으로 재현한다. clawfit이 추천만 하고 검증하지 못하는 "조합의 실증"에 해당한다.

2. **거버넌스가 기능이 아니라 설계 제약.**
   ACL을 검색 쿼리 단계에서 강제하고, 인용은 메타데이터만 반환하고, ACL 변경·취소·차단을 전부 감사 이벤트로 남긴다. AnythingLLM/LibreChat의 권한은 "옵션"이지만 여기선 "전제"다.

3. **온프레미스 GPU 실측 자산.**
   B300 GPU 7 vLLM(Qwen3.5-27B), Ollama qwen2.5:7b, SSH 터널, 모델별 시스템 프롬프트(`qwen35`/`qwen`/`generic`), `enable_thinking` 토글, 데모 폴백까지. clawfit의 LLM×하드웨어 축에 넣을 수 있는 **실측 근거**를 우리만 갖고 있다.

4. **한국어·규제 맥락이 기본값.**
   한국어 우선 이중언어 UI, 한국어 근거 강제 프롬프트, PII 마스킹. 글로벌 도구가 설정으로 미루는 것을 기본 동작으로 둔다. 찰떡AI의 시장 논리를 조직 단위로 확장한 것.

---

## 6. 격차와 리스크

| 축 | 우리 | 상대 |
| --- | --- | --- |
| 채택 신호 | 0★, 비공개 로컬 저장소 | 37k~68k★, GitHub Trending |
| MCP/툴 루프 | ⛔ (README도 "목표 아키텍처"로 표기) | AnythingLLM·LibreChat 기본 |
| 문서 파서 | UTF-8 text/Markdown | PDF/DOCX/OCR |
| 검색 품질 | Qdrant dense + lexical fallback | hybrid + rerank, 그래프(cognee), vectorless(PageIndex) |
| 인증 | 개발용 헤더 | OIDC/LDAP |
| 감사 무결성 | PG + JSONL, **WORM/서명 없음** | Maka는 audit-logs를 제품 축으로 |
| 대화 영속화 | 없음 | 전부 서버 저장 |
| clawfit 등재 | **불가** (비공개 + star + cost 스키마) | LibreChat도 동일 사유로 미등재 |

**핵심 모순**: 이름은 "AI 에이전트 플랫폼"인데 에이전트 루프·툴 승인·MCP가 없다. 현재 실체는 **"거버넌스되는 근거 기반 채팅 + 지식 인프라"** 다. clawfit 기준 정직한 분류는 **L6a primary + L3 secondary + L7 secondary**다(저장소 내부 문서의 L1~L4 표기는 clawfit 레벨과 의미가 다르므로 혼용 금지).

---

## 7. clawfit 등재 전략 (순서대로)

### 7-1. 분류 제안

| 항목 | 제안 |
| --- | --- |
| primary | **L6a — Retrieval-native knowledge infrastructure** |
| secondary | **L3 — Governance/policy** (PolicyEngine, audit, ACL, rate limit) |
| tertiary | **L7 — Human interface** (한국어 우선 채팅 워크스페이스, 관리자 콘솔) |
| 신규 카테고리 후보 | `governed_ai_platform` — 테넌트·감사·정책을 1급으로 갖는 온프레미스 배포 단위 |
| `network` | `offline` (vLLM/Ollama 온프레미스) / `hybrid` (외부 API 선택 시) |
| `features` | `rag`, `document-ingestion`, `self-hosted`, `governance`, `audit-logs`, `acl`, `observability`, `memory`, `citations` |
| `tasks` | `research`, `qa`, `summarization`, `knowledge-management` |
| `roles` | `exec`, `analyst`, `developer`, `devops` |
| `team_size` | `mid`, `large` |
| `pricing_tier` | `free` (MIT, 자체 호스팅) |
| `setup_complexity` | `medium` (Basic Core) ~ `high` (full-stack) |

### 7-2. 스키마 갭과 비용 모델

clawfit이 요구하는 `cost_per_call`은 자체 호스팅에서 정의되지 않는다. 게다가 **clawfit 자신의 `clawfit/registry/hardware.json`은 온프레미스 장비의 `cost_per_hour`를 `0.0`으로 둔다** (`on-prem-workstation`, `local-rtx-spark`, `local-amd-halo`). 즉 온프레미스는 "한계비용 0"으로 모델링되어 있어, 어떤 비용식을 넣어도 0으로 붕괴한다. 이는 도구의 문제가 아니라 **clawfit 비용 스키마의 내부 불일치**다.

대안은 두 가지다.

| 안 | 내용 | 장단 |
| --- | --- | --- |
| A. `amortized_cost_per_gpu_hour` 필드 추가 | capex/3년 + 전력 + 냉각을 시간당으로 환산해 hardware 엔트리에 기록 | 하드웨어 축과 도구 축이 같은 단위로 연결됨. 조직별 회계 차이는 입력 변수로 흡수 |
| B. `cost_model: self_hosted_gpu` + `cost_per_hour: null` | 온프레미스는 비용 미모델링임을 명시하고 스코어링에서 cost 축을 중립화 | 스키마 변경 최소. 대신 "결정론적 비용" 요건은 포기 |

제안은 **A + B 병행**이다. 하드웨어에 상각 단가를 넣고, 도구 레벨에서는 다음 식으로 참조한다.

```
cost_per_1k_tokens($) = amortized_cost_per_gpu_hour / (3.6 × T_tokens_per_sec)
```

- `amortized_cost_per_gpu_hour`: B300 1-GPU 상각 단가(조직 회계 입력, 하드웨어 엔트리에서 상속)
- `T_tokens_per_sec`: 고정 프롬프트 3회 반복 측정의 median sustained throughput

측정 도구: `scripts/measure_throughput.py` (모델 서버 직접 측정 + 게이트웨이 지연 측정, `--json`으로 증거 저장).

측정된 지연(2026-11-11 기준, `docs/b300-vllm.md`):

| 항목 | 측정값 |
| --- | --- |
| Qwen3.5-27B 비스트리밍 | 약 0.50초 |
| 스트리밍 (39 토큰) | 약 0.58초 |
| Qwen3.5-122B-A10B | 0.14~3.47초 (쿼리·캐시 의존) |
| GPU 7 메모리 (27B) | 246,030 MiB / 275,040 MiB |
| Langfuse trace latency | 약 0.53초 |

> ⚠️ 위 값은 **짧은 응답의 지연**이지 sustained throughput이 아니다. `cost_per_1k_tokens` 계산에는 별도 벤치마크가 필요하다(아래 체크리스트).

민감도 예시(가정값, 실측 전 참고용):

| T (tok/s) | $1/GPU-h | $3/GPU-h | $6/GPU-h |
| --- | --- | --- | --- |
| 20 | $0.0139 | $0.0417 | $0.0833 |
| 40 | $0.0069 | $0.0208 | $0.0417 |
| 80 | $0.0035 | $0.0104 | $0.0208 |

### 7-3. 찰떡AI와의 페어링 서술

clawfit 문서에는 두 제품을 **한 아키텍처의 두 층**으로 제출한다.

> "Baryon AX (server, L6a+L3) ↔ 찰떡AI (device, L6b). 조직 지식은 retrieval로, 개인 지식은 LLM-maintained graph로 유지한다. 두 제품 모두 데이터가 조직/기기 밖으로 나가지 않는다."

이 서술은 clawfit 정본에 없는 조합이며, `data_sensitivity: confidential` + `governance_need: hard` 프로파일에 대한 실증 사례가 된다.

### 7-4. 선행 조건 (공개)

clawfit 등재의 실제 병목은 스키마가 아니라 **비공개**다. 순서:

1. 저장소 공개 (README의 "reference implementation, not a security certification" 문구 유지)
2. `docs/research-watch/` 형식의 딥다이브 1건 제출 → `registry-entry-proposal.md` 첨부
3. 5k★ 미달 시: "reference implementation 예외" 또는 "evidence entry" 트랙 제안
4. 등재 후보로 `governed_ai_platform` 카테고리 신설 요청 (LibreChat 사례와 묶어서)

---

## 8. 기능 로드맵 우선순위 (시장 요건)

clawfit 등재 요건이 아니라 경쟁 요건이다. 위에서 확인한 격차 순서대로:

| 순위 | 항목 | 이유 |
| --- | --- | --- |
| 1 | **MCP 서버 노출** (문서 검색·메모리·감사 조회를 MCP 툴로) | MCP 유무가 경쟁 비교표의 1차 분기점. AnythingLLM·LibreChat 모두 보유 |
| 2 | **OIDC/JWT 인증** | 개발용 헤더로는 "엔터프라이즈" 주장 불가 |
| 3 | **문서 파서 확장** (PDF/DOCX → Markdown) | WeKnora·AnythingLLM 대비 가장 큰 기능 격차 |
| 4 | **하이브리드 검색 + rerank** | dense + lexical fallback은 2026년 기준 하한 |
| 5 | **대화 스레드 영속화** | 감사·인용·메모리 주장의 전제 |
| 6 | 툴 실행 루프 (승인 게이트 포함) | "에이전트 플랫폼" 명칭 정합성 |
| 7 | 감사 WORM/서명 | `governance_need: hard` 프로파일 대응 |

---

## 9. 라이선스·데이터 정합성 (검증 결과)

| 항목 | clawfit 표기 | 실제 (2026-11-11 확인) | 조치 |
| --- | --- | --- | --- |
| Tencent WeKnora | `Apache-2.0` | **MIT** (LICENSE 원문: "licensed under the MIT except for the third-party components") | clawfit 수정 요청 |
| Langfuse | `MIT` | MIT core + EE 디렉터리 별도 (GitHub API `NOASSERTION`) | 표기 보강 권장 |
| AnythingLLM | `MIT` | MIT | 일치 |
| Apache Maka | `Apache-2.0` | Apache-2.0 | 일치 |
| LibreChat | (미등재) | MIT, `LibreChat-AI/LibreChat`로 이동 | 등재 제안 시 최신 URL 사용 |

---

## 10. 다음 액션 체크리스트

- [x] 유사 리포 식별 및 3군 분류 (문서 3장)
- [x] 기능 비교 매트릭스 작성 (문서 4장)
- [x] 분류·스키마·비용 모델 제안 (문서 7장)
- [x] 라이선스 검증 및 clawfit 오류 목록화 (문서 9장)
- [x] clawfit research-watch 초안 작성 (`docs/research/clawfit/`)
- [x] `tools_registry.json` 등재 제안 JSON 작성 (`docs/research/clawfit/registry-entry-proposal.md`)
- [ ] **sustained throughput 측정** → `cost_per_1k_tokens` 확정
- [ ] 저장소 공개 및 README에 research-watch 링크 추가
- [ ] clawfit에 PR: research-watch 문서 + WeKnora 라이선스 수정 + 카테고리 신설 제안

---

## 부록. clawfit 레벨 요약 (혼동 방지)

| 레벨 | clawfit 정의 | 이 저장소의 대응 |
| --- | --- | --- |
| L1 | 기본 런타임 / 추론 기반 | vLLM · Ollama 어댑터 |
| L2 | 하네스·오케스트레이션 래퍼 | LangGraph (정책·검색 그래프) |
| L3 | 거버넌스·정책 | PolicyEngine, ACL, 감사, 레이트리밋 |
| L4a | 메모리 | Mem0 + Qdrant |
| L4c | MCP·툴 | **없음 (로드맵 1순위)** |
| L5 | 평가·관측 | Langfuse, pytest/Playwright 스모크 |
| L6a | retrieval-native 지식 | **주 포지션** — Qdrant + FastEmbed + 인용 |
| L6b | LLM-native 지식 | 찰떡AI (별도 제품) |
| L7 | 휴먼 인터페이스 | 한국어 우선 채팅 UI, 관리자 콘솔 |
