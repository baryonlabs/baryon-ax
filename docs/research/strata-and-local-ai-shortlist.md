# Strata와 로컬 AI 오픈소스 후보 조사

> 조사일: 2026-11-11  
> 조사 범위: `hongsw` GitHub Stars 2,265개 + Strata 및 주요 대안 저장소  
> 기준: GitHub API/README/LICENSE, 2026-11-11 확인. Star 수는 조사 시점 값입니다.

## TL;DR

1. **Strata는 "Baryon AX의 경쟁 제품"이 아니라 "소비자용/엣지 추론 엔진 + 데스크톱 앱"** 입니다. MIT, C++, OpenAI/Anthropic 호환 API를 제공하므로 **Baryon AX Edge(오프라인 데스크톱)** 의 모델 런타임으로 붙일 수 있습니다.
2. **B300 서버에는 Strata가 맞지 않습니다.** Strata는 RTX 20~50, Radeon, Intel Arc, Apple Silicon 등 소비자 하드웨어를 대상으로 하며, B300 데이터센터 GPU는 검증 대상이 아닙니다. **B300는 vLLM 유지**가 맞습니다.
3. **당장 가져다 쓸 만한 것**은 모델 런타임이 아니라 **문서 인제스트**입니다.
   - `microsoft/markitdown` (MIT, 194k): PDF/Office/HTML → Markdown
   - `docling-project/docling` (MIT, 70k): PDF 레이아웃·표·OCR
   - `Niko1221/Strata` (MIT, 42k): 엣지 런타임 PoC
4. **UI/플랫폼은 통째로 갈아타지 말고 참고**하는 것이 좋습니다.
   - `Mintplex-Labs/anything-llm` (MIT, 68k): 로컬 RAG 워크스페이스의 UX 기준
   - `LibreChat-AI/LibreChat` (MIT, 47k): 채팅·에이전트 UI 기준
   - `Tencent/WeKnora` (MIT, 37k), `infiniflow/ragflow` (Apache-2.0, 94k): RAG 플랫폼 구조 참고
5. **라이선스 주의**: Dify(수정 Apache, 멀티테넌트 제한), Open WebUI(브랜딩 조항), n8n(Sustainable Use), textgen/Khoj/Firecrawl(AGPL), MinerU(상용 임계치), Onyx/Flowise/LiteLLM(EE 디렉터리 별도 라이선스)은 **재배포/상용화 전 법무 검토**가 필요합니다.

---

## 1. Strata 분석

### 무엇인가

- **저장소**: `Niko1221/Strata`
- **설명**: Qwen3.8-Flash-Next 등 대형 MoE 모델을 소비자 하드웨어에서 실행하는 추론 엔진 + 데스크톱 앱 + 터미널 + 로컬 API 서버
- **라이선스**: MIT (코드), 모델 파일은 별도 라이선스(Qwen Community License 등)
- **규모**: 42.8k stars, 4.7k forks, C++, 6,247 commits, v0.2.13
- **하드웨어**: NVIDIA RTX 20/30/40/50, RTX A/Quadro, AMD RX 6000/7000/9000, Ryzen AI Max, Intel Arc(Linux), Apple Silicon
- **배포**: Windows/Linux 설치 파일, macOS dmg, `strata launch` 터미널, Docker(ghcr.io/niko1221/strata), MCP 서버

### 핵심 구조

Strata는 모델의 **24,576개 expert** 를 GPU VRAM, RAM, SSD에 나눠 배치합니다.

- GPU: attention/DeltaNet, router, shared expert, KV cache, **자주 쓰는 expert cache**
- RAM: 전체 expert를 pinned memory에 보관, GPU에 없는 expert는 CPU가 계산
- SSD: n-gram lookup table(약 28.8GB)을 토큰당 몇 행만 읽음
- MTP/DFlash2/prompt lookup으로 speculative decoding
- 긴 문서를 8,192 토큰 단위로 스트리밍

즉, **"GPU 한 장에 다 안 들어가는 큰 MoE 모델을 PC에서 돌리는 것"** 이 핵심입니다. B300처럼 VRAM이 275GB인 서버에서는 vLLM의 연속 배치/처리량이 더 적합합니다.

### API/통합

- OpenAI 호환 API + Anthropic 호환 API
- 로컬 바인딩 `127.0.0.1:8080` (Docker 예시), `--access lan|cloudflare|tailscale`
- API key 옵션
- Claude Code, Codex CLI, OpenCode, oh-my-pi, pi.dev, Hermes Agent, OpenClaw 등 연결 가이드
- MCP 서버 제공

**Baryon AX 통합은 코드 변경 없이 가능**합니다.

```bash
MODEL_BASE_URL=http://127.0.0.1:8080/v1
MODEL_NAME=<strata-served-model>
MODEL_API_KEY=<key>
```

### 장점

- MIT, 활발한 개발, 거대 MoE를 소비자 GPU에서 실행
- OpenAI/Anthropic API + MCP + 데스크톱/터미널/Docker
- 원클릭 설치, Windows/Linux/macOS
- Baryon AX의 `MODEL_BASE_URL` 어댑터와 바로 호환

### 한계/리스크

- **B300 데이터센터 GPU는 공식 지원 목록에 없음** → 서버용으로 부적합
- v0.2.13, 아직 젊은 프로젝트. C++/CUDA/HIP/SYCL 엔진 유지보수 부담
- 엔터프라이즈 SSO/RBAC/감사/멀티테넌시 없음 (데스크톱/개인용 중심)
- 모델 다운로드 수십 GB, SSD/RAM 요구량 큼
- 모델별 라이선스가 코드 라이선스와 별개
- Docker는 Mac Metal 미지원 (Mac은 네이티브 앱/터미널)

### 결론

| 용도 | Strata 적합도 |
| --- | --- |
| B300 서버 추론 | ★☆☆☆☆ (vLLM 사용) |
| Mac/Windows 데스크톱 오프라인 | ★★★★★ |
| 12~24GB GPU에서 큰 MoE 실행 | ★★★★★ |
| 엔터프라이즈 멀티테넌트 플랫폼 | ★☆☆☆☆ |
| Baryon AX Edge 모델 런타임 | ★★★★☆ (PoC 권장) |

---

## 2. Strata 유사 로컬 런타임 비교

| 프로젝트 | 유형 | 라이선스 | Stars | API | 강점 | Baryon AX 적합성 |
| --- | --- | --- | ---: | --- | --- | --- |
| **Strata** | MoE 추론 엔진 + 데스크톱 | MIT | 42.8k | OpenAI/Anthropic | 거대 MoE를 GPU+RAM+SSD로 | 엣지 PoC ★★★★ |
| **Ollama** | 로컬 모델 서버 | MIT | 186.6k | OpenAI | 가장 쉬움, 생태계, 모델 관리 | 개발/엣지 기본 ★★★★★ |
| **llama.cpp** | 추론 엔진/서버 | MIT | 133.9k | OpenAI server | 최대 제어, 광범위 하드웨어 | 임베드/엣지 ★★★★ |
| **LocalAI** | OpenAI 호환 서버 | MIT | 50.3k | OpenAI | 텍스트·이미지·음성·임베딩, Docker | 셀프호스트 API ★★★★ |
| **Jan** | 데스크톱 ChatGPT 대안 | Apache-2.0 | 45.7k | OpenAI (:1337) | 완전 오픈소스 GUI, 로컬 API | 데스크톱 UX ★★★★★ |
| **GPT4All** | 데스크톱 로컬 LLM | MIT | 77.4k | Local API | 오프라인 RAG, 단순 UI | 데스크톱 참고 ★★★ |
| **textgen** | 데스크톱/웹 UI | AGPL-3.0 | 47.7k | OpenAI/Anthropic | 확장성, 다중 백엔드 | 라이선스 리스크 ★★ |
| **llamafile** | 단일 파일 실행 | Apache-2.0 | 26.6k | OpenAI | 배포 단순 | 엣지 배포 참고 ★★★ |
| **vLLM** | 서버 추론 | Apache-2.0 | 96.7k | OpenAI | 처리량, B300 GPU | 서버 ★★★★★ |
| **SGLang** | 서버 추론 | Apache-2.0 | 38.7k | OpenAI | 고속 서빙, 멀티모달 | 서버 대안 ★★★★ |

> Strata는 LocalAI의 **실험적 backend**로도 통합되어 있습니다. 즉, LocalAI를 API 게이트웨이로 두고 Strata 엔진을 붙이는 조합도 가능합니다.

---

## 3. `hongsw` Stars에서 뽑은 인접 후보

### 3.1 로컬 RAG/워크스페이스

| 프로젝트 | 라이선스 | Stars | 특징 | 판단 |
| --- | --- | ---: | --- | --- |
| **AnythingLLM** | MIT | 68.1k | 데스크톱/Docker, 워크스페이스, 문서 RAG, 에이전트, 임베디드 위젯 | UX/기능 참고, 데스크톱 에디션 후보 |
| **Open WebUI** | Custom BSD-like (NOASSERTION) | 158.3k | Ollama/OpenAI, RAG, 도구, 멀티유저 | UI 참고, 브랜딩 조항 확인 |
| **LibreChat** | MIT | 46.9k | 채팅, 에이전트, MCP, 다중 제공자 | UI/에이전트 참고 |
| **WeKnora** | MIT (제3자 예외) | 37.1k | Tencent, 문서→RAG, 지식 플랫폼, Go | RAG 구조 참고 |
| **RAGFlow** | Apache-2.0 | 94.0k | 문서 파싱·청킹·검색 특화 RAG 엔진 | 인제스트/검색 백엔드 후보 |
| **Dify** | 수정 Apache-2.0 | 160.9k | 워크플로·RAG·에이전트 올인원 | 기능 참고, 멀티테넌트 제한 |
| **Flowise** | Apache-2.0 + EE | 55.6k | 비주얼 에이전트 빌더 | 기능 참고, EE 라이선스 |
| **Cognita** | Apache-2.0 | 4.4k | 모듈형 RAG 프레임워크 | 구조 참고 |

### 3.2 문서 인제스트

| 프로젝트 | 라이선스 | Stars | 특징 | 판단 |
| --- | --- | ---: | --- | --- |
| **MarkItDown** | MIT | 194.8k | PDF/Office/HTML → Markdown | **즉시 도입 후보** |
| **Docling** | MIT | 70.4k | PDF 레이아웃·표·OCR | **즉시 도입 후보** |
| **MinerU** | Apache-2.0 + 상용 임계치 | 83.5k | PDF/Office → Markdown/JSON | 품질 우수, 라이선스 검토 |
| **Crawl4AI** | Apache-2.0 | 87.2k | 웹 크롤링/추출 | 웹 인제스트 후보 |
| **Firecrawl** | AGPL-3.0 | 200.4k | 웹 데이터 API | AGPL 주의 |

### 3.3 메모리/지식 그래프

| 프로젝트 | 라이선스 | Stars | 특징 | 판단 |
| --- | --- | ---: | --- | --- |
| **Mem0** | Apache-2.0 | 68.8k | 에이전트 메모리, Qdrant 등 | **이미 적용** |
| **Letta** | Apache-2.0 | 25.4k | 상태 기반 에이전트, 장기 메모리 | 메모리 구조 비교 |
| **Cognee** | Apache-2.0 | 33.2k | AI 메모리 플랫폼, 그래프 | 메모리 구조 비교 |
| **Graphiti** | Apache-2.0 | 32.7k | 실시간 지식 그래프 | Graph RAG 후보 |
| **LightRAG** | MIT | 40.6k | 경량 Graph RAG | Graph RAG 후보 |
| **GraphRAG** | MIT | 36.6k | Microsoft 그래프 RAG | Graph RAG 참고 |

### 3.4 관측/평가/보안

| 프로젝트 | 라이선스 | Stars | 특징 | 판단 |
| --- | --- | ---: | --- | --- |
| **Langfuse** | MIT core + EE | 37.3k | 트레이스·평가·비용 | **이미 적용** |
| **Opik** | Apache-2.0 | 23.3k | LLM/RAG/에이전트 관측·평가 | Langfuse 대안/보완 |
| **Phoenix** | Apache-2.0 | 12.2k | AI 관측·평가 | 경량 대안 |
| **DeepEval** | Apache-2.0 | 19.4k | LLM 평가 프레임워크 | CI 평가 후보 |
| **Garak** | Apache-2.0 | 10.0k | LLM 취약점 스캐너 | 보안 테스트 후보 |
| **LiteLLM** | MIT + EE | 63.1k | 100+ 모델 게이트웨이 | 모델 게이트웨이 후보 |
| **n8n** | Sustainable Use | 210.0k | 워크플로 자동화 | 라이선스 주의 |

### 3.5 에이전트 하네스 (참고)

| 프로젝트 | 라이선스 | Stars | 특징 |
| --- | --- | ---: | --- |
| **OpenClaw** | MIT | 394.3k | "실제로 일하는" 에이전트, 모든 OS |
| **Orca** | MIT | 121.3k | 코딩 에이전트 플릿 관리 |
| **Paperclip** | MIT | 112.9k | 업무용 에이전트 관리 |
| **OpenHands** | MIT | 93.1k | AI 개발 에이전트 |
| **DeerFlow** | MIT | 85.2k | 장기 리서치/코딩 에이전트 |

> 이들은 Baryon AX의 "에이전트 실행/관리" 계층을 참고할 수 있지만, Strata의 대체재는 아닙니다.

---

## 4. 라이선스 요약

| 프로젝트 | 라이선스 | 재배포/상용 주의 |
| --- | --- | --- |
| Strata | MIT | 코드 OK, 모델 파일 별도 |
| Ollama | MIT | OK (GUI 일부 closed) |
| llama.cpp | MIT | OK |
| LocalAI | MIT | OK |
| Jan | Apache-2.0 | OK |
| GPT4All | MIT | OK |
| textgen | AGPL-3.0 | 소스 공개 의무, 주의 |
| AnythingLLM | MIT | OK (데스크톱 텔레메트리 별도) |
| Open WebUI | Custom BSD-like | 브랜딩 조항 확인 |
| LibreChat | MIT | OK |
| WeKnora | MIT (제3자 예외) | 제3자 라이선스 확인 |
| RAGFlow | Apache-2.0 | OK |
| Dify | 수정 Apache-2.0 | 멀티테넌트 SaaS 제한 |
| Flowise | Apache-2.0 + EE | enterprise 디렉터리 별도 |
| MarkItDown | MIT | OK |
| Docling | MIT | OK |
| MinerU | Apache-2.0 + 임계치 | 일정 규모 이상 상용 라이선스 |
| Graphiti/LightRAG/GraphRAG | Apache/MIT | OK |
| Mem0/Letta/Cognee | Apache-2.0 | OK |
| Langfuse | MIT + EE | EE 기능 별도 |
| Opik/Phoenix/DeepEval/Garak | Apache-2.0 | OK |
| LiteLLM | MIT + EE | enterprise 디렉터리 별도 |
| n8n | Sustainable Use | OSI 아님, SaaS 제한 |
| Firecrawl | AGPL-3.0 | 소스 공개 의무 |
| vLLM/SGLang | Apache-2.0 | OK |

---

## 5. Baryon AX에 어떻게 적용할까

### 5.1 서버 (B300)

- **vLLM 유지**. Strata는 B300 데이터센터 GPU 검증 대상이 아니고, vLLM이 연속 배치/처리량/OpenAI API에 적합합니다.
- 모델: Qwen3.5-27B(현재) → 필요 시 Qwen3.5-122B/397B로 확장.
- GPU 7 단독 서빙은 이미 검증됨 (0.5초 응답, E2E 11 checks).

### 5.2 엣지/데스크톱

- **Ollama**: 기본 개발/엣지 런타임. 이미 Baryon AX full stack에 연결.
- **Strata**: 12~24GB GPU에서 큰 MoE를 돌리는 **Baryon AX Edge** 후보. OpenAI API라 `MODEL_BASE_URL`만 바꿔 붙일 수 있음.
- **Jan**: 데스크톱 UI/로컬 API가 필요할 때 대안. Apache-2.0, 완전 오픈소스.
- **AnythingLLM**: "로컬 RAG 데스크톱" UX 기준. MIT. 다만 Baryon AX UI를 대체하기보다는 기능 벤치마크로 사용.

### 5.3 지식/인제스트

- **MarkItDown**: PDF/DOCX/PPTX/XLSX/HTML → Markdown. MIT, 즉시 도입 가능.
- **Docling**: PDF 레이아웃/표/OCR. MIT, 품질 우수.
- **MinerU**: 품질 좋지만 상용 임계치 조항 확인.
- **RAGFlow/WeKnora**: 문서 파싱·청킹·검색 파이프라인 참고 또는 백엔드 후보.

### 5.4 메모리/그래프

- **Mem0**: 현재 적용. 사용자 메모리 저장/검색.
- **Letta/Cognee**: 상태 기반 에이전트 메모리 구조 비교.
- **Graphiti/LightRAG/GraphRAG**: 조직 지식 그래프가 필요할 때 옵션.

### 5.5 관측/보안/평가

- **Langfuse**: 현재 적용.
- **Opik/Phoenix**: Langfuse 대안/보완.
- **DeepEval**: RAG/에이전트 품질 CI.
- **Garak**: LLM 취약점 스캔.
- **LiteLLM**: 다중 모델 게이트웨이. 라이선스 검토 후 도입.

---

## 6. 권장 도입 순서

### Phase 0 — 1주: 저위험 PoC

1. **Strata Edge PoC**
   - 24GB NVIDIA GPU 또는 Apple Silicon에서 Strata 설치/실행
   - `MODEL_BASE_URL=http://127.0.0.1:8080/v1`로 Baryon AX 연결
   - `scripts/smoke_full_stack.py`로 문서 업로드→검색→채팅 검증
   - 측정: 첫 토큰, 토큰/초, 메모리, 안정성
2. **MarkItDown 인제스트**
   - `DocumentService`에 `DOCUMENT_PARSER=markitdown` 옵션 추가
   - PDF/DOCX/XLSX/HTML 업로드 테스트
   - 실패 시 기존 UTF-8 텍스트 파서로 폴백

### Phase 1 — 2주: 지식 품질

3. **Docling vs MinerU vs RAGFlow** 파싱 비교
   - 동일 PDF 20개, 표/이미지/레이아웃 품질
   - 라이선스/처리 시간/메모리
4. **WeKnora/RAGFlow** 구조 분석
   - 문서→청킹→인덱싱→검색 파이프라인
   - Baryon AX에 가져올 컴포넌트 선별

### Phase 2 — 3~4주: 제품화

5. **AnythingLLM/LibreChat/Jan UX 비교**
   - 워크스페이스, 문서 격리, 인용, 에이전트, MCP
   - Baryon AX UI에 흡수할 패턴 선정
6. **Graphiti/LightRAG** Graph RAG PoC
7. **Garak + DeepEval** CI/보안 파이프라인

### Phase 3 — 이후

8. LiteLLM 게이트웨이 라이선스 검토 및 도입
9. Baryon AX Edge 데스크톱 패키징 (Strata/Jan 기반)
10. B300 멀티 GPU/모델 라우팅

---

## 7. 의사결정 게이트

| 질문 | 통과 조건 |
| --- | --- |
| 라이선스 | 재배포/상용/SaaS 시나리오에서 법무 검토 통과 |
| 하드웨어 | 목표 고객의 GPU/RAM/SSD에서 실제 동작 |
| 보안 | SSO/RBAC/감사/텔레메트리 통제 |
| 통합 | OpenAI 호환 API 또는 라이브러리로 붙일 수 있음 |
| 운영 | 업그레이드/모니터링/장애 대응 가능 |
| 품질 | RAG 정확도/인용/지연이 기존 대비 우수 |
| 비용 | 모델 다운로드/스토리지/GPU 비용이 수용 가능 |

---

## 8. 결론

- **Strata는 "Baryon AX Edge"에 적합한 엣지 추론 엔진**입니다. 서버/B300 코어로 쓰기에는 맞지 않습니다.
- **가장 먼저 가져올 것은 MarkItDown/Docling**입니다. 코드 변경이 작고, 라이선스가 명확하며, RAG 품질에 직접 기여합니다.
- **AnythingLLM/LibreChat/Jan**은 Baryon AX UI를 대체하기보다 **UX/기능 벤치마크**로 활용하는 것이 안전합니다.
- **Dify/Open WebUI/n8n/AGPL 프로젝트**는 라이선스 검토 없이 제품에 넣지 않는 것이 좋습니다.
- **B300는 vLLM 유지**, **엣지는 Strata/Ollama/Jan**, **지식은 MarkItDown/Docling + Qdrant**, **운영은 Langfuse/Garak/DeepEval** 조합을 권장합니다.

---

## 9. 참고 링크

- Strata: https://github.com/Niko1221/Strata
- Strata Docker: https://github.com/Niko1221/Strata/blob/main/docs/DOCKER.md
- Strata 작동 원리: https://github.com/Niko1221/Strata/blob/main/docs/HOW_IT_WORKS.md
- LocalAI Strata backend: https://localai.io/docs/features/strata/
- Ollama: https://github.com/ollama/ollama
- llama.cpp: https://github.com/ggml-org/llama.cpp
- LocalAI: https://github.com/mudler/LocalAI
- Jan: https://github.com/janhq/jan
- AnythingLLM: https://github.com/Mintplex-Labs/anything-llm
- Open WebUI: https://github.com/open-webui/open-webui
- LibreChat: https://github.com/LibreChat-AI/LibreChat
- WeKnora: https://github.com/Tencent/WeKnora
- RAGFlow: https://github.com/infiniflow/ragflow
- MarkItDown: https://github.com/microsoft/markitdown
- Docling: https://github.com/docling-project/docling
- MinerU: https://github.com/opendatalab/MinerU
- Graphiti: https://github.com/getzep/graphiti
- LightRAG: https://github.com/HKUDS/LightRAG
- Mem0: https://github.com/mem0ai/mem0
- Langfuse: https://github.com/langfuse/langfuse
- Garak: https://github.com/NVIDIA/garak
- DeepEval: https://github.com/confident-ai/deepeval
- LiteLLM: https://github.com/BerriAI/litellm
- `hongsw` Stars: https://github.com/hongsw?tab=stars
