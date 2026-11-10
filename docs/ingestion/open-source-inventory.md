# 문서·데이터베이스 지식화 오픈소스 총정리

> 조사일: 2026-11-11
> 목적: Baryon AX가 UTF-8 text/Markdown만 받는 현재 인제스트를 **다양한 문서 + 데이터베이스**로 확장하기 위한 후보 조사
> 기준: GitHub API 2026-11-11 확인(★, 라이선스, 최근 push). 라이선스는 저장소 표기 기준이며, 배포 전 원문 확인 필요
> 관련 설계: [`architecture.md`](architecture.md)

---

## 0. 결론 먼저 — 추천 조합

| 계층 | 1순위 | 대안 | 이유 |
| --- | --- | --- | --- |
| 한국 문서 (HWP/HWPX/공문서) | **kordoc** | rhwp, hwp.js | HWP 3.x/5.x·HWPX·HWPML + 표 구조·RAG 청크·오프라인·MCP까지 한 번에. MIT |
| 범용 문서 → Markdown | **docling** | markitdown, MinerU, marker | PDF 레이아웃·표·OCR·Office. MIT, Python 네이티브, 청크 친화 구조 출력 |
| 스캔/이미지 OCR | **PaddleOCR** (또는 kordoc 내장) | surya, olmocr, tesseract | 한국어 포함 100+ 언어, Apache-2.0. kordoc이 이미 로컬 OCR 제공 |
| 레거시 포맷 최후 fallback | **Apache Tika** | gotenberg(Office→PDF) | 1,000+ 포맷 탐지·추출. JVM sidecar |
| 청킹 | **chonkie** | LlamaIndex SentenceSplitter(현행), semchunk | 표·헤딩 인지 청킹, 경량 |
| 임베딩 | **FastEmbed** (현행 유지) | BGE-M3 계열 | 이미 도입. 다국어 MiniLM으로 시작 |
| 벡터 저장 | **Qdrant** (현행) | pgvector | ACL payload 필터·재색인 용이 |
| 원본 보관 | **로컬 FS → MinIO** | S3 호환 | content-addressed 저장, 재처리 가능 |
| DB 적재(배치) | **dlt** | Airbyte, Meltano | Python 네이티브, 스키마 진화·증분, 가볍다 |
| DB 변경 스트림(CDC) | **Debezium** | dlt 증분, 폴링 | 실시간 반영이 필요할 때만 |
| DB 의미 계층 | **Cube** 또는 경량 YAML 시맨틱 모델 | dbt, DB-GPT | "숫자가 대시보드와 일치" 요구를 만족 |
| 파이프라인 오케스트레이션 | **Broadway 패턴의 Python 이식** (자체) | Temporal, Dagster | 단일 프로세스 규모에선 라이브러리 도입보다 패턴 이식이 저렴 |
| 장기 실행/재시도 보장 | **Temporal** (P1 이후) | Argo Workflows, Dagster | 잡이 길어지고 재시작·보상이 필요해질 때 |
| PII | **Presidio** | 자체 PolicyEngine(현행) | 탐지기 확장, 한국어는 커스텀 필요 |

> **핵심 판단**: 파서는 "하나로 통일"하지 않는다. **MIME 라우팅 + 어댑터 레지스트리**로 두고, 한국 문서는 kordoc, 일반 문서는 docling, 미지 포맷은 Tika로 흘린다. 모든 파서는 동일한 **Document IR**로 수렴시킨다.

---

## 1. 한국 문서 특화

| 프로젝트 | ★ | 라이선스 | 역할 | 채택 |
| --- | --- | --- | --- | --- |
| **[kordoc](https://github.com/chrisryugj/kordoc)** | 2,958 | MIT | HWP 3.x/5.x·HWPX·HWPML·PDF·XLS/XLSX·DOCX·PPTX·이미지 → Markdown/IR/RAG 청크. 로컬 OCR(손글씨), 병합 셀 표, `--format chunks`, MCP 22개 도구, `KORDOC_OFFLINE=1`, gVisor 샌드박스 | **1순위.** Node 20+ |
| [rhwp](https://github.com/edwardkim/rhwp) | 4,095 | 확인 필요 | Rust/WASM HWP 뷰어·편집기 | 뷰어가 필요할 때 |
| [hwp.js](https://github.com/hahnlee/hwp.js) | 1,307 | 확인 필요 | 웹 기반 HWP 파서 | 브라우저 미리보기 |
| [pyhwp](https://github.com/mete0r/pyhwp) | 303 | NOASSERTION | Python HWP 5.x 파서 | 라이선스 불명 — 보류 |
| [hwplib](https://github.com/neolord0/hwplib) | 587 | 확인 필요 | Java HWP 라이브러리 | JVM 스택일 때 |

**kordoc을 1순위로 두는 이유**
- 한국 공문서 표 구조 벤치마크 10,342/10,342(100%), PDF 200문서 종합 0.965 — 우리가 필요로 하는 표·별표·병합 셀 케이스를 직접 검증한 수치를 공개한다.
- `--format chunks`가 **제목 경로가 붙은 구조 청크 + 독립 표 청크**를 출력한다. 즉 파서 단계에서 이미 청킹 단위를 제공하므로 우리 청커와 정합을 맞추기 쉽다.
- `KORDOC_OFFLINE=1` + 로컬 OCR → **온프레미스 요건과 충돌하지 않는다.**
- MCP 서버를 이미 제공 → 향후 "에이전트가 직접 문서를 읽는" 경로를 추가 비용 없이 확보.
- **제약**: Node.js 런타임. Python API 프로세스에 직접 embed하지 말고 **sidecar/서브프로세스 워커**로 붙인다(공식 Python SDK가 parse-worker 프로토콜을 제공).

---

## 2. 범용 문서 파싱

| 프로젝트 | ★ | 라이선스 | 강점 | 약점 | 위치 |
| --- | --- | --- | --- | --- | --- |
| **[docling](https://github.com/docling-project/docling)** | 70,393 | MIT | PDF 레이아웃·표·읽기순서·OCR, Office/HTML, 구조화 JSON, 청킹 친화 | 대형 PDF에서 느림 | **기본 파서** |
| **[markitdown](https://github.com/microsoft/markitdown)** | 194,790 | MIT | Office/PDF/HTML/오디오 → Markdown, 매우 가볍고 빠름 | 표 구조·레이아웃 정밀도 낮음 | 빠른 경로/미리보기 |
| **[MinerU](https://github.com/opendatalab/MinerU)** | 83,452 | NOASSERTION | 복잡 PDF·수식·표 → Markdown/JSON, 에이전트 워크플로 지향 | 라이선스 확인 필요, GPU 선호 | 고품질 PDF 배치 |
| **[marker](https://github.com/datalab-to/marker)** | 40,845 | Apache-2.0 | PDF→Markdown, 표·수식·이미지, 빠른 변환 | 모델 다운로드·GPU | 고품질 PDF 대안 |
| **[unstructured](https://github.com/Unstructured-IO/unstructured)** | 15,713 | Apache-2.0 | 파티셔닝·청킹·메타데이터, ETL 지향 | 무거운 의존성, 일부 기능 유료 | 구조 추출 대안 |
| **[Tika](https://github.com/apache/tika)** | 4,140 | Apache-2.0 | 1,000+ 포맷 탐지·텍스트/메타 추출 | 표·레이아웃 품질 낮음, JVM | **최후 fallback** |
| **[gotenberg](https://github.com/gotenberg/gotenberg)** | 13,555 | MIT | Office/HTML → PDF 변환 API (Docker) | 변환만 담당 | Office→PDF 전처리 |
| [mammoth](https://github.com/mwilliamson/mammoth) | — | BSD-2 | DOCX → 시맨틱 HTML/Markdown | DOCX 전용 | DOCX 경량 경로 |
| [pdfplumber](https://github.com/jsvine/pdfplumber) | 10,890 | MIT | 문자·좌표·표 정밀 추출 | 느림, 스캔 미지원 | 표 검증/디버깅 |
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | 11,103 | **AGPL-3.0** | 초고속 PDF 텍스트/이미지 | **AGPL — 재배포 시 주의** | 내부 도구 한정 |
| [Camelot](https://github.com/camelot-dev/camelot) | 3,855 | MIT | PDF 표 추출 특화 | PDF 전용 | 표 품질 보강 |

---

## 3. OCR·레이아웃

| 프로젝트 | ★ | 라이선스 | 비고 |
| --- | --- | --- | --- |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 92,535 | Apache-2.0 | 100+ 언어, 한국어 포함. CPU/GPU. **기본 OCR** |
| [surya](https://github.com/datalab-to/surya) | 21,589 | Apache-2.0 | OCR·레이아웃·읽기순서·표, 90+ 언어 |
| [olmOCR](https://github.com/allenai/olmocr) | 19,822 | Apache-2.0 | PDF 선형화, LLM 데이터셋 지향 |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) | 77,449 | Apache-2.0 | 가장 오래된 fallback. 한국어 품질은 PaddleOCR보다 낮음 |
| kordoc 내장 OCR | — | MIT | 손글씨·표·기울기 보정, 오프라인 CPU. **한국 문서는 이걸 우선** |

---

## 4. 웹·메일 (지식 소스 확장)

| 프로젝트 | ★ | 라이선스 | 역할 |
| --- | --- | --- | --- |
| [crawl4ai](https://github.com/unclecode/crawl4ai) | 87,190 | Apache-2.0 | LLM 친화 크롤러, Markdown 변환 |
| [trafilatura](https://github.com/adbar/trafilatura) | 7,118 | Apache-2.0 | 본문 추출(보일러플레이트 제거), 경량 |

> 웹 소스는 P2. 온프레미스 고객사는 대부분 내부 문서·DB가 우선순위다.

---

## 5. 청킹·임베딩·색인

| 프로젝트 | ★ | 라이선스 | 역할 | 채택 |
| --- | --- | --- | --- | --- |
| [chonkie](https://github.com/feyninc/chonkie) | 4,828 | MIT | 토큰/문장/시맨틱/표 인지 청킹 | **P0 도입 검토** |
| [LlamaIndex](https://github.com/run-llama/llama_index) | 52,868 | MIT | SentenceSplitter(현행), 노드 파서 | 현행 유지 |
| [semchunk](https://github.com/isaacus-dev/semchunk) | 670 | MIT | 빠른 의미 경계 분할 | 대안 |
| [FastEmbed](https://github.com/qdrant/fastembed) | 3,291 | Apache-2.0 | ONNX 다국어 임베딩(현행) | 유지 |
| [Qdrant](https://github.com/qdrant/qdrant) | 35,502 | Apache-2.0 | 벡터 DB, payload 필터(현행) | 유지 |
| [pgvector](https://github.com/pgvector/pgvector) | 23,644 | NOASSERTION | Postgres 벡터 확장 | 단일 DB 선호 고객용 대안 |

**청킹 원칙**: 헤딩 경로(`h1 > h2 > h3`)와 표를 청크 메타데이터로 보존한다. 표는 **행 묶음 단위**로 자르고 헤더를 반복 삽입한다(검색 정확도에 직접 영향).

---

## 6. 데이터베이스 → 지식

### 6-1. 커넥터·적재

| 프로젝트 | ★ | 라이선스 | 역할 | 채택 |
| --- | --- | --- | --- | --- |
| [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) | 12,273 | MIT | DB 추상화·스키마 introspection | **P1 필수** |
| [dlt](https://github.com/dlt-hub/dlt) | 6,073 | Apache-2.0 | Python 네이티브 추출/적재, 증분·스키마 진화 | **P1 1순위** |
| [Airbyte](https://github.com/airbytehq/airbyte) | 22,352 | NOASSERTION | 300+ 커넥터 플랫폼 | 커넥터 많을 때 |
| [Meltano](https://github.com/meltano/meltano) | 2,679 | MIT | Singer 기반 ELT | 대안 |
| [Debezium](https://github.com/debezium/debezium) | 13,342 | Apache-2.0 | CDC(WAL/binlog) | **P2 실시간** |
| [dbt](https://github.com/dbt-labs/dbt) | 14,142 | Apache-2.0 | 모델·문서화·계보 | 의미 계층과 함께 |

### 6-2. 스키마·의미 계층

| 프로젝트 | ★ | 라이선스 | 역할 |
| --- | --- | --- | --- |
| [sqlglot](https://github.com/tobymao/sqlglot) | 9,778 | MIT | SQL 파싱·방언 변환. 컬럼 계보 추적 |
| [Cube](https://github.com/cube-js/cube) | 21,208 | NOASSERTION | 시맨틱 레이어. 에이전트가 "공식 지표"를 조회 |
| [DB-GPT](https://github.com/eosphoros-ai/DB-GPT) | 20,281 | MIT | Text2SQL·DB 에이전트 참고 구현 |

### 6-3. 에이전트/외부 연결

| 프로젝트 | ★ | 라이선스 | 역할 |
| --- | --- | --- | --- |
| [MCP Toolbox](https://github.com/googleapis/mcp-toolbox) | 16,835 | Apache-2.0 | DB를 MCP 툴로 노출(선언적 설정) |
| [DBHub](https://github.com/bytebase/dbhub) | 3,822 | MIT | 범용 DB MCP 서버 |
| [postgres-mcp](https://github.com/crystaldba/postgres-mcp) | 3,467 | MIT | Postgres 특화 MCP(인덱스·헬스 분석) |
| [airweave](https://github.com/airweave-ai/airweave) | 6,545 | MIT | SaaS/DB → 에이전트 검색 계층 |

> **우리 방향**: MCP 노출(P1 로드맵 1순위)은 `MCP Toolbox` 또는 자체 MCP 서버로 처리하되, **지식화(스냅샷→IR→색인)** 는 별도 경로로 둔다. 둘을 섞으면 ACL·감사가 우회된다.

---

## 7. 오케스트레이션·전송

| 프로젝트 | ★ | 라이선스 | 적합 시점 |
| --- | --- | --- | --- |
| [Broadway](https://github.com/elixir-broadway/broadway) | 2,692 | Apache-2.0 | **패턴 차용**(GenStage demand 기반 배압·배칭·ack) |
| [Temporal](https://github.com/temporalio/temporal) | 24,307 | MIT | 잡이 분 단위를 넘고 보상·재시작이 필요할 때 |
| [Dagster](https://github.com/dagster-io/dagster) | 16,386 | Apache-2.0 | 자산(asset) 중심 배치·계보 |
| [Prefect](https://github.com/PrefectHQ/prefect) | 24,147 | Apache-2.0 | Python 워크플로 |
| [Airflow](https://github.com/apache/airflow) | 47,440 | Apache-2.0 | 스케줄 중심, 무거움 |
| [Argo Workflows](https://github.com/argoproj/argo-workflows) | 17,086 | Apache-2.0 | Kubernetes 잡 |
| [NATS](https://github.com/nats-io/nats-server) | 21,029 | Apache-2.0 | 경량 메시지 버스(다중 워커) |

**판단**: P0~P1은 단일 API 프로세스 + Postgres 잡 큐로 충분하다. Broadway의 **개념**(producer → processor → batcher → consumer, demand 기반 배압, at-least-once, DLQ)만 Python으로 이식한다. 워커를 수평 확장해야 할 때 NATS/Temporal을 붙인다.

---

## 8. 보안·거버넌스 부품

| 프로젝트 | ★ | 라이선스 | 역할 |
| --- | --- | --- | --- |
| [Presidio](https://github.com/data-privacy-stack/presidio) | 11,822 | MIT | PII 탐지·익명화. 한국어는 recognizer 추가 필요 |
| [MinIO](https://github.com/minio/minio) | 61,263 | **AGPL-3.0** | S3 호환 오브젝트 스토리지. **재배포 주의** |
| kordoc gVisor | — | MIT | 파서 격리(`KORDOC_SANDBOX=gvisor`) |

---

## 9. 라이선스 리스크 요약

| 등급 | 프로젝트 | 조치 |
| --- | --- | --- |
| **AGPL-3.0** | PyMuPDF, MinIO | 내부 사용은 가능. **고객사에 바이너리/서비스로 재배포하면 고지·소스 제공 의무 검토**. PyMuPDF는 pdfplumber/docling으로 대체 가능 |
| **NOASSERTION(복합/불명)** | MinerU, Airbyte, Cube, pgvector, pyhwp | 배포 전 LICENSE 원문 확인. 특히 pyhwp는 사용 보류 |
| MIT/Apache-2.0 | kordoc, docling, markitdown, marker, Tika, dlt, Debezium, Broadway, chonkie, FastEmbed, Qdrant, SQLAlchemy, Presidio | 기본 채택 가능 |

> 이 표는 법률 자문이 아니다. `docs/open-source.md`의 기존 원칙(배포 전 원문 확인)을 그대로 따른다.

---

## 10. 조합 시나리오

### A. 최소 (P0, 2~3주)
```
업로드 → MIME 라우팅 → kordoc | docling | Tika → IR → chonkie → FastEmbed → Qdrant
```
- 대상: PDF/HWP/HWPX/DOCX/XLSX/PPTX/이미지
- 추가 인프라: kordoc 워커(Node), Tika는 선택
- 기대: 현재 "txt/md만" 대비 커버리지 대폭 확대

### B. 표준 (P1, +4~6주)
```
A + DB 스냅샷(SQLAlchemy/dlt) → 스키마 프로파일 → 행 직렬화 → 동일 IR/색인
  + 잡 큐/재시도/DLQ + 지식 카탈로그(Postgres) + 재색인/삭제 전파
```
- 대상: Postgres/MySQL/MSSQL/Oracle 스냅샷, 테이블 단위 증분
- 기대: "문서 + DB" 통합 검색, 출처에 `db://schema.table.row` 표기

### C. 대규모 (P2)
```
B + Debezium CDC + Cube 시맨틱 레이어 + Temporal/NATS 워커
```
- 대상: 실시간 반영, 다중 워커, 수십만 문서
- 기대: 변경 즉시 색인, 지표 질의는 시맨틱 레이어로 분리

---

## 11. 결정이 필요한 사항

| # | 질문 | 제안 |
| --- | --- | --- |
| 1 | kordoc을 어떤 형태로 붙이나 | **HTTP/JSON 워커 sidecar** (Node 런타임 격리, gVisor 옵션) |
| 2 | 표를 어떻게 청킹하나 | 헤더 반복 + 행 묶음(≤1,200자), 표 전체 요약 청크 1개 추가 |
| 3 | DB 행을 어떻게 지식화하나 | "테이블 설명 + 컬럼 의미 + 행 값" 템플릿. 행 단위 1청크, 상한 초과 시 컬럼 그룹 분할 |
| 4 | ACL을 어떻게 상속하나 | 소스 단위 기본 ACL → 문서/테이블 단위 override. 색인 payload에 최종 ACL만 기록 |
| 5 | 삭제는 어떻게 전파하나 | `kb_document.tombstoned_at` + Qdrant `delete_by_filter(document_id)` |
| 6 | 재색인 비용 | `content_hash` 동일 시 파싱·임베딩 생략(파서/모델 버전을 해시에 포함) |
| 7 | 파서 실패 시 | Tika fallback → 실패 시 DLQ + 감사 이벤트(`document.parse_failed`) |
