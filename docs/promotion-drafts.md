# 홍보 문안 초안 (Baryon AX)

> 대상: https://baryonlabs.github.io/baryon-ax/ · `/en/`
> UTM 규칙과 채널 계획은 [docs/promotion-playbook.md](promotion-playbook.md)를 따릅니다.
> 수치는 저장소에서 확인 가능한 사실만 사용했습니다. 새 수치를 넣기 전에 `docs/`에서 근거를 확인하세요.

---

## 1. LinkedIn

### 한국어

> 사내 문서로 답하되, 근거 없이는 답하지 않게.
>
> **Baryon AX**를 공개했습니다. 온프레미스에서 도는 AI 에이전트 플랫폼입니다.
>
> - 문서·암묵지·메모리를 검색해 **출처와 청크를 인용**하는 RAG 채팅
> - HWP·HWPX·PDF·Office·이미지 파싱과 구조 인식 청킹
> - 조직·역할 ACL과 감사 로그, 모델 비종속(OpenAI 호환)
> - Docker Compose로 설치, 에어갭 환경 지원, MIT 라이선스
>
> Basic Core는 2 vCPU·4GB에서 데모 모델로 바로 실행됩니다. Ollama `qwen2.5:7b`와 B300 GPU의 vLLM `Qwen3.5-27B` 구성으로 검증했습니다.
>
> 저장소: https://github.com/baryonlabs/baryon-ax
> 소개: https://baryonlabs.github.io/baryon-ax/?utm_source=linkedin&utm_medium=social&utm_campaign=launch_2026_11&utm_content=post_ko
>
> #OnPremise #RAG #LLM #AI에이전트 #오픈소스

### English

> Enterprise answers should come with evidence, not confidence.
>
> **Baryon AX** is an on-premises, model-agnostic AI agent platform. It retrieves from your own documents, tacit knowledge, and memory, then answers with source citations.
>
> - Grounded RAG with document and chunk citations
> - HWP/HWPX, PDF, Office, images, and text ingestion
> - Organization and role ACLs, audit events, and a pipeline trace
> - Docker Compose, air-gapped capable, MIT licensed
>
> The Basic Core runs on 2 vCPU / 4GB with a demo model. Validated with Ollama `qwen2.5:7b` and vLLM `Qwen3.5-27B` on B300.
>
> Code: https://github.com/baryonlabs/baryon-ax
> Site: https://baryonlabs.github.io/baryon-ax/en/?utm_source=linkedin&utm_medium=social&utm_campaign=launch_2026_11&utm_content=post_en
>
> #OnPremises #RAG #LLM #AIAgents #OpenSource

---

## 2. Naver 블로그

**제목 후보**

- HWP 문서까지 근거로 답하는 사내 AI, Baryon AX를 공개했습니다
- 폐쇄망에서도 도는 RAG: 문서 인용과 권한 통제를 한 번에
- 온프레미스 AI 에이전트를 Docker로 10분 만에 띄우는 방법

**본문**

> 사내 문서를 LLM에 넣는 것과, **근거를 대며 답하게 하는 것**은 다릅니다.
>
> Baryon AX는 두 번째를 목표로 만든 온프레미스 AI 에이전트 플랫폼입니다. 답변마다 어떤 문서의 어떤 청크를 근거로 삼았는지 표시하고, 근거를 찾지 못하면 모른다고 말합니다.
>
> **1. 문서를 그냥 넣지 않습니다**
> HWP·HWPX는 kordoc, PDF·Office·이미지는 docling, 레거시 형식은 Tika로 라우팅합니다. 표 헤더와 제목 경로를 인식해 청킹하므로, 표 안의 숫자도 문맥을 잃지 않습니다.
>
> **2. 권한이 먼저입니다**
> 검색과 인용은 사용자에게 부여된 문서 ACL 범위 안에서만 이뤄집니다. 조직·역할 설정과 감사 로그, 최근 이벤트를 한 화면에서 확인할 수 있습니다.
>
> **3. 모델은 골라 씁니다**
> OpenAI 호환 `/chat/completions`만 맞으면 됩니다. Ollama `qwen2.5:7b`로 시작하고, 운영에서는 vLLM으로 GPU에 올릴 수 있습니다. B300 GPU에서 `Qwen3.5-27B` 구성을 검증했습니다.
>
> **4. 설치**
> ```bash
> git clone https://github.com/baryonlabs/baryon-ax.git
> cd baryon-ax
> cp .env.example .env
> docker compose up --build
> ```
> 데모 모델은 API 키 없이 동작합니다. Basic Core 기준 2 vCPU·4GB면 충분합니다.
>
> MIT 라이선스이고, 모든 PR에 보안 스캔(P0/P1 머지 차단)이 붙습니다.
>
> - 소개: https://baryonlabs.github.io/baryon-ax/?utm_source=naver_blog&utm_medium=owned&utm_campaign=hwp_rag_guide&utm_content=post
> - 저장소: https://github.com/baryonlabs/baryon-ax
> - 보안 CI: https://github.com/baryonlabs/baryon-ax/blob/main/docs/security-ci.md
>
> #온프레미스 #RAG #HWP #LLM #오픈소스

**이미지**: `site/assets/og.jpg` (1200×630) + `docs/images/gallery/01-chat.png`

---

## 3. GitHub Release

**Tag**: `v0.6.0` · **Title**: `Baryon AX v0.6.0 — on-premises grounded AI agents`

```markdown
Baryon AX v0.6.0 is the first public release: an MIT-licensed, on-premises
AI agent platform that answers with source citations.

## Highlights

- **Grounded chat**: streaming answers with document and chunk citations,
  stop/regenerate/copy, 👍/👎 feedback, and an ungrounded-answer warning.
- **Ingestion**: HWP/HWPX via kordoc, PDF/Office/images via docling, legacy
  formats via Tika, MIME routing, structure-aware chunking.
- **Governance**: organization and role ACLs, document permission audit,
  recent events, and an admin status view.
- **Knowledge loop**: tacit-knowledge capture, knowledge-gap clustering,
  ontology pack drafts, and verification.
- **Models**: OpenAI-compatible routing; validated with Ollama `qwen2.5:7b`
  and vLLM `Qwen3.5-27B` on B300.
- **Security CI**: every PR runs `security-sweep`; P0/P1 findings block merge.
  Weekly image, SBOM, and secret scans are included.

## Run it

```bash
git clone https://github.com/baryonlabs/baryon-ax.git
cd baryon-ax
cp .env.example .env
docker compose up --build
```

Open http://localhost:3001. The demo model needs no API key.

## Docs

- Site: https://baryonlabs.github.io/baryon-ax/?utm_source=github&utm_medium=referral&utm_campaign=release_v0_6_0&utm_content=release_notes
- Architecture: docs/architecture.md
- Security CI: docs/security-ci.md
- Open-source inventory: docs/open-source.md
- Hardware: docs/hardware-requirements.md
```

---

## 4. 짧은 문안 (X / Threads / Mastodon)

### 한국어

> 사내 문서로 답하되, 출처 없이는 답하지 않습니다.
> Baryon AX — 온프레미스 AI 에이전트, MIT, Docker, HWP/HWPX/PDF 파싱, ACL·감사 포함.
> 2 vCPU·4GB로 바로 실행됩니다.
> https://baryonlabs.github.io/baryon-ax/?utm_source=x&utm_medium=social&utm_campaign=launch_2026_11&utm_content=thread

### English

> Answers from your own documents, with citations — not guesses.
> Baryon AX: on-premises AI agents, MIT, Docker, HWP/HWPX/PDF parsing, ACLs and audit.
> Runs on 2 vCPU / 4GB.
> https://baryonlabs.github.io/baryon-ax/en/?utm_source=x&utm_medium=social&utm_campaign=launch_2026_11&utm_content=thread

---

## 5. 외부 커뮤니티용 (Hacker News / Reddit)

**Title**: `Show HN: Baryon AX – on-premises RAG agents with citations and ACLs`

**Body**

> We built Baryon AX because most "chat with your docs" demos stop at retrieval quality and ignore the two things enterprises ask about: can I see the evidence, and can I control who sees it.
>
> It runs with Docker Compose, uses Qdrant + FastEmbed for retrieval, and keeps model access behind an OpenAI-compatible interface (Ollama, vLLM, or an internal gateway). Ingestion handles HWP/HWPX (Korean formats), PDF, Office, and images through MIME-routed parsers. Answers carry document and chunk citations plus a pipeline trace; if retrieval finds nothing, the UI says so instead of inventing an answer.
>
> Basic Core runs on 2 vCPU / 4GB with a demo model. The full-stack profile adds PostgreSQL, Redis, Langfuse, LangGraph, LlamaIndex, and Mem0.
>
> MIT licensed. Feedback on the ingestion and grounding design is especially welcome.
>
> Code: https://github.com/baryonlabs/baryon-ax
> Site: https://baryonlabs.github.io/baryon-ax/en/

---

## 6. 게시 전 체크

- [ ] 수치가 `docs/`와 일치하는가 (버전, 하드웨어, 모델명)
- [ ] 링크가 라이브 200인가 (`check_site_live.py`)
- [ ] UTM이 채널별로 다른가
- [ ] OG 미리보기가 의도대로 보이는가 (1200×630)
- [ ] 고객사·수상·벤치마크를 사실 이상으로 주장하지 않았는가
- [ ] 보안/인증 표현이 `docs/security.md`의 한계와 모순되지 않는가
