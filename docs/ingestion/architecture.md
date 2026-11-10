# 인제스트 아키텍처 — 문서·DB → 지식

> 작성일: 2026-11-11
> 상태: **설계 제안 v0.1** (Basic Core v0.6 기준)
> 후보 조사: [`open-source-inventory.md`](open-source-inventory.md)
> 참고 모델: [Elixir Broadway](https://github.com/elixir-broadway/broadway) — GenStage demand 기반 배압, 배칭, at-least-once, DLQ
> 프로토타입: `apps/api/src/baryon/ingest/`

---

## 1. 목표와 비목표

**목표**
1. UTF-8 text/Markdown만 받는 현재 업로드를 **HWP/HWPX/PDF/Office/이미지 + DB 테이블**로 확장한다.
2. 파서가 늘어나도 **색인·검색·인용·ACL·감사 계약은 변하지 않는다** — 모든 소스가 하나의 `Document IR`로 수렴.
3. 대용량 배치에서도 **메모리 폭주 없이** 처리한다 — Broadway식 배압.
4. 실패가 격리된다 — 파서 하나가 죽어도 파이프라인 전체가 멈추지 않고 DLQ로 떨어진다.

**비목표 (이번 단계)**
- 실시간 CDC(Debezium) — P2. P1은 스냅샷 + 증분 쿼리.
- 멀티 워커 수평 확장 — P1까지는 단일 API 프로세스 + Postgres 잡 큐.
- 에이전트 자율 수집 — 소스 등록은 관리자 행위.

---

## 2. 현재 상태와 한계

| 항목 | 현재 (`apps/api/src/baryon/main.py`) | 문제 |
| --- | --- | --- |
| 입력 | `.txt`, `.md`만, UTF-8 strict | PDF/HWP/Office/이미지 불가 |
| 처리 | 요청 스레드에서 파싱→청킹→임베딩→업서트 동기 수행 | 대용량에서 요청 타임아웃, 재시도 없음 |
| 청킹 | LlamaIndex `SentenceSplitter(256/32)` | 표·헤딩 구조 상실 |
| 배치 | 문서 1건 = 임베딩 1회, 업서트 1회 | 배치 크기 제어·배압 없음 |
| 중복 | 없음 | 같은 문서 재업로드 시 중복 색인 |
| 실패 | 500 반환 | 부분 실패 복구·DLQ 없음 |
| DB | 없음 | — |
| 삭제 | Qdrant 필터 삭제 | 재색인·버전 관리 없음 |

---

## 3. Broadway에서 가져올 것

Broadway는 Elixir 생태계의 데이터 처리 라이브러리다. 핵심은 **GenStage 기반 demand(수요) 전파**다. 소비자가 처리 가능한 만큼만 상류에 요청하므로, 생산자가 아무리 빨라도 큐가 무한정 커지지 않는다.

| Broadway 개념 | 의미 | Python 구현 (우리 방식) |
| --- | --- | --- |
| `producer` | 소스에서 이벤트를 가져옴 | 소스 커넥터 코루틴 (업로드 큐, DB 커서, 디렉터리 스캔) |
| `processor` | 이벤트 단위 변환 | 파서 어댑터 (kordoc/docling/Tika) |
| `batcher` | 크기 또는 시간 기준 묶음 | `BatchAssembler(batch_size, batch_timeout)` |
| `consumer` | 배치 처리(부수효과) | 임베딩 + Qdrant 업서트 + 카탈로그 기록 |
| demand / backpressure | 소비 능력만큼만 당김 | `asyncio.Queue(maxsize)` + `await put` (가득 차면 생산자 대기) |
| `max_demand` / `min_demand` | 상류 요청 단위 | `max_in_flight` 세마포어 |
| `handle_failed` | 재시도/DLQ | 지수 백오프 재시도 → DLQ 테이블 |
| at-least-once | ack 후에만 오프셋 전진 | 성공 후 `ack` 기록. 중복은 `content_hash`로 멱등 처리 |
| graceful shutdown | 진행 중 배치 완료 후 종료 | `SIGTERM` → 생산 중단 → in-flight drain |
| partitioning | 키 단위 순서 보장 | `partition_key = document_id`(문서 내 청크 순서 보존) |

> Broadway를 Elixir로 도입하는 것이 아니라, **개념과 계약을 Python으로 이식**한다. 근거: 단일 프로세스·Postgres 규모에서 별도 런타임(BEAM)과 메시지 브로커는 운영 부담만 늘린다. 워커 수평 확장이 필요해지는 시점(P2)에 NATS/Temporal로 대체한다.

---

## 4. 전체 데이터 흐름

```text
[소스]                    [원본 보관]              [정규화]                 [지식화]                [서빙]
파일 업로드 ─┐
디렉터리 감시 ─┼─▶ Raw Vault ──▶ Router ──▶ Parser ──▶ Document IR ──▶ Chunker ──▶ Embedder ──▶ Qdrant
DB 스냅샷 ────┤   (content-addressed)  (MIME/확장자)   (어댑터)      (blocks)     (헤딩/표 인지)  (batch)     (+payload ACL)
웹/메일(P2) ──┘         │                  │              │              │              │              │
                        │                  │              │              │              │              │
                        └──────────────▶ Knowledge Catalog (Postgres) ◀──┴──────────────┴──────────────┘
                                          kb_source / kb_document / kb_chunk / kb_job / kb_dead_letter
                                                        │
                                                        └──▶ 감사 이벤트 · Langfuse span · 인용 조회
```

### 4.1 단계별 계약

| # | 단계 | 입력 | 출력 | 실패 시 |
| --- | --- | --- | --- | --- |
| 0 | **Source** | 업로드/스캔/DB 커서 | `SourceItem(uri, mime_hint, acl, meta)` | DLQ |
| 1 | **Raw Vault** | 바이트 | `sha256` 키로 저장된 원본 + `raw_uri` | DLQ(용량/무결성) |
| 2 | **Router** | `mime_hint`, 파일명, magic bytes | `parser_name` | Tika fallback → DLQ |
| 3 | **Parser** | 원본 경로/바이트 | `Document`(IR) | 다음 파서 후보 → DLQ |
| 4 | **Normalizer** | IR blocks | Markdown + 구조 JSON | DLQ |
| 5 | **Chunker** | IR blocks | `Chunk[]`(text, heading_path, page, table_ref) | 폴백: 고정 길이 |
| 6 | **Embedder** | `Chunk[]` (batch) | `vector[]` | 재시도 3회 → DLQ |
| 7 | **Indexer** | vectors + ACL payload | Qdrant points | 재시도 → DLQ |
| 8 | **Catalog** | 문서/청크 메타 | Postgres 행 | 색인 롤백(문서 단위) |

**원칙**
- 3~7단계는 **문서 단위 트랜잭션**이다. 8단계가 실패하면 Qdrant 포인트를 `document_id` 필터로 제거한다(고아 벡터 방지).
- 6단계는 **배치**로만 수행한다. 1건씩 임베딩하면 ONNX 세션 오버헤드가 지배한다.
- 7단계 payload에는 **최종 ACL만** 기록한다. 원본 ACL 규칙을 검색 시점에 재평가하지 않는다(현행 방식 유지: 검색 후 재검증).

---

## 5. Document IR

파서 교체 가능성의 핵심은 IR이다. 모든 파서는 아래 형태로만 출력한다.

```json
{
  "document_id": "doc_01H...",
  "source_type": "file",
  "source_uri": "s3://kb/raw/ab12...pdf",
  "title": "2026년 예산 편성 지침",
  "mime": "application/pdf",
  "content_hash": "sha256:ab12...",
  "parser": {"name": "kordoc", "version": "4.24.61"},
  "acl": {"organization_id": "org_acme", "owner_id": "u_7", "allowed_roles": ["member", "admin"]},
  "metadata": {"page_count": 12, "language": "ko"},
  "blocks": [
    {"kind": "heading", "level": 1, "text": "Ⅰ. 총칙", "page": 1},
    {"kind": "paragraph", "text": "이 지침은 ...", "page": 1},
    {"kind": "table", "page": 2, "table": {"header_rows": 1, "rows": [["구분", "2025", "2026"], ["인건비", "100", "110"]]}},
    {"kind": "table_caption", "text": "표 1. 세출 예산", "page": 2}
  ]
}
```

| 필드 | 이유 |
| --- | --- |
| `blocks[].kind` | 청킹 전략을 블록 종류별로 분기 |
| `page`, `bbox` | 인용 시 "p.2 표 1"까지 표시 가능 |
| `table.rows` | 표를 행 묶음으로 청킹, 헤더 반복 삽입 |
| `heading_path` (청킹 시 계산) | `Ⅰ. 총칙 > 2. 편성 기준` — 검색 정확도와 인용 품질의 핵심 |
| `content_hash` | 멱등성·중복 제거·재색인 생략 |
| `parser.version` | 파서 업그레이드 시 재처리 판단 |

DB 소스는 동일 IR로 매핑한다.

```json
{
  "source_type": "database",
  "source_uri": "postgres://erp/public.vendor_invoice#id=88421",
  "title": "vendor_invoice (id=88421)",
  "metadata": {"schema": "public", "table": "vendor_invoice", "pk": {"id": 88421}, "columns": {"amount": "numeric(14,2)", "currency": "char(3)"}},
  "blocks": [
    {"kind": "paragraph", "text": "테이블 vendor_invoice — 공급업체 청구서. 컬럼: id(청구서 번호), vendor_id(공급업체), amount(금액), currency(통화), issued_at(발행일)."},
    {"kind": "table", "table": {"header_rows": 1, "rows": [["id", "vendor_id", "amount", "currency", "issued_at"], ["88421", "V-102", "1250000", "KRW", "2026-10-30"]]}}
  ]
}
```

> **행 1개 = 문서 1개**로 두면 ACL·삭제·버전 관리가 단순해진다. 행이 수백만 건이면 테이블 단위로 묶고 `metadata.pk_range`를 기록하는 모드(P2)를 추가한다.

---

## 6. 데이터베이스 경로

```text
[Postgres/MySQL/MSSQL/Oracle]
        │  ① 스키마 introspection (SQLAlchemy Inspector)
        ▼
[Schema Profile]  테이블 설명·컬럼 타입·PK/FK·행 수·샘플
        │  ② 증분 커서 (updated_at > last_cursor 또는 PK 범위)
        ▼
[Row Serializer]  템플릿: 테이블 설명 + 컬럼 의미 + 행 값(JSON)
        │  ③ Document IR (source_type=database)
        ▼
[동일 파이프라인]  Chunker → Embedder → Qdrant
```

### 6.1 증분 동기화

| 방식 | 언제 | 구현 |
| --- | --- | --- |
| 풀 스캔 | 최초 1회, 소규모 테이블 | `SELECT *` 스트리밍 |
| 커서 증분 | `updated_at`/`id`가 단조 증가 | `WHERE updated_at > :cursor ORDER BY updated_at` |
| CDC | 실시간·삭제 감지 필요 | Debezium → 이벤트 → 동일 IR (P2) |

### 6.2 행 직렬화 규칙

1. **테이블 헤더 블록**을 항상 첫 블록으로 둔다(테이블명·설명·컬럼 의미). 이 블록이 없으면 행 청크가 맥락을 잃는다.
2. 값은 `컬럼명: 값` 형태로 직렬화한다(JSON dump 금지 — 임베딩 품질 저하).
3. 숫자·통화·단위는 컬럼 타입에서 유도해 붙인다(`amount: 1,250,000 KRW`).
4. 민감 컬럼(주민번호·계좌·연락처)은 **색인 전에** Presidio/PolicyEngine으로 마스킹하고 `metadata.redacted=true`를 남긴다.
5. 행 텍스트가 임베딩 상한을 넘으면 컬럼 그룹으로 분할하고 각 청크에 PK를 반복한다.

### 6.3 DB에 대한 질의와 지식화의 분리

| 경로 | 용도 | 도구 |
| --- | --- | --- |
| **지식화(색인)** | "계약서상 지급 조건이 뭐야?" 같은 문서형 질문 | 이 파이프라인 |
| **구조화 질의** | "이번 달 미지급 건수는?" 같은 집계 질문 | 시맨틱 레이어(Cube/자체 YAML) + SQL 실행 |

둘을 섞으면 **숫자가 대시보드와 달라지는 문제**가 생긴다. 집계는 시맨틱 레이어가 소유하고, 색인은 원문 근거만 담당한다.

---

## 7. Knowledge Catalog (Postgres)

```sql
CREATE TABLE kb_source (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,              -- file | directory | database | web
  name          TEXT NOT NULL,
  config        JSONB NOT NULL,             -- 경로, 접속 정보 참조(비밀값 아님), 스키마/테이블
  default_acl   JSONB NOT NULL,
  enabled       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE kb_document (
  id             TEXT PRIMARY KEY,
  source_id      TEXT REFERENCES kb_source(id),
  source_uri     TEXT NOT NULL,
  title          TEXT NOT NULL,
  mime           TEXT,
  content_hash   TEXT NOT NULL,
  parser         TEXT,
  parser_version TEXT,
  organization_id TEXT NOT NULL,
  owner_id       TEXT NOT NULL,
  allowed_roles  TEXT[] NOT NULL,
  version        INTEGER NOT NULL DEFAULT 1,
  status         TEXT NOT NULL,             -- pending | indexed | failed | tombstoned
  tombstoned_at  TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_uri, organization_id, version)
);

CREATE TABLE kb_chunk (
  id            TEXT PRIMARY KEY,
  document_id   TEXT REFERENCES kb_document(id) ON DELETE CASCADE,
  ordinal       INTEGER NOT NULL,
  heading_path  TEXT,
  page          INTEGER,
  text          TEXT NOT NULL,
  token_count   INTEGER,
  UNIQUE (document_id, ordinal)
);

CREATE TABLE kb_job (
  id            TEXT PRIMARY KEY,
  source_id     TEXT REFERENCES kb_source(id),
  state         TEXT NOT NULL,              -- queued | running | done | failed
  cursor        TEXT,                        -- 증분 커서
  stats         JSONB NOT NULL DEFAULT '{}',
  error         TEXT,
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ
);

CREATE TABLE kb_dead_letter (
  id           TEXT PRIMARY KEY,
  source_uri   TEXT NOT NULL,
  stage        TEXT NOT NULL,               -- parse | chunk | embed | index
  parser       TEXT,
  error        TEXT NOT NULL,
  attempts     INTEGER NOT NULL,
  payload_ref  TEXT,                         -- Raw Vault 키
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`kb_document.status` 전이: `pending → indexed` / `pending → failed` / `indexed → tombstoned`.

---

## 8. Qdrant payload 계약

```json
{
  "document_id": "doc_01H...",
  "source_type": "database",
  "source_uri": "postgres://erp/public.vendor_invoice#id=88421",
  "title": "vendor_invoice (id=88421)",
  "chunk": 3,
  "heading_path": "vendor_invoice > 지급 조건",
  "page": null,
  "content_hash": "sha256:ab12...",
  "parser": "dlt-sqlalchemy",
  "version": 1,
  "organization_id": "org_acme",
  "owner_id": "u_7",
  "allowed_roles": ["member", "admin"]
}
```

- 기존 인덱스 필드(`organization_id`, `owner_id`, `document_id`, `allowed_roles`)는 유지한다.
- 추가 필드는 **필터에 쓰는 것만** payload index를 만든다(`source_type`, `parser`, `version`).
- 삭제: `document_id` 필터 삭제(현행) + `kb_chunk` cascade.

---

## 9. 멱등성·버전·삭제

| 문제 | 해결 |
| --- | --- |
| 같은 파일 재업로드 | `content_hash` 동일 + `parser_version` 동일 → **색인 생략**, 카탈로그만 touch |
| 파서 업그레이드 | `content_hash`에 `parser.name@version` 포함 → 자동 재처리 |
| 문서 수정 | 새 `version` 행 생성, 이전 버전 포인트를 먼저 삭제 후 업서트(순서 고정) |
| 삭제 | `tombstoned_at` 기록 → Qdrant 필터 삭제 → 감사 이벤트. 원본은 보존 정책에 따름 |
| 부분 실패 | 문서 단위 롤백: Qdrant 포인트 제거 + `kb_document.status=failed` |
| 중복 배치 실행 | `(source_uri, organization_id, version)` 유니크 + 업서트 멱등 |

멱등 키: `sha256(source_uri | content_hash | parser@version | embedding_model@version)`

---

## 10. 배압·동시성 파라미터

| 파라미터 | 기본값 | 근거 |
| --- | --- | --- |
| `max_in_flight_documents` | 4 | 파서는 CPU/메모리 집약. 4 이상은 OOM 위험 |
| `batch_size` (embed) | 32 청크 | FastEmbed ONNX 배치 효율 |
| `batch_timeout_s` | 0.5 | 소량 문서의 지연 방지 |
| `queue_maxsize` | 32 | 생산자가 소비자보다 앞서지 못하게 제한 |
| `max_retries` | 3 | 지수 백오프 1s/2s/4s |
| `parser_timeout_s` | 120 | 대형 PDF 상한. 초과 시 DLQ |
| `document_max_bytes` | 2,000,000 → **20,000,000** | PDF/HWP 대응 상향 |
| `ocr_enabled` | false (기본) → 문서별 opt-in | OCR은 비용·시간이 크다 |

**관측 지표**: `ingest_queue_depth`, `ingest_batch_size`, `ingest_stage_seconds{stage}`, `ingest_failures_total{stage}`, `ingest_dlq_total`, `ingest_documents_total{source_type}`. 각 문서는 Langfuse span 1개, 각 단계는 child span.

---

## 11. API 변경안

| 메서드 | 경로 | 변화 |
| --- | --- | --- |
| `POST` | `/v1/documents` | MIME 스니핑, 다중 포맷, **비동기 옵션** (`?async=true` → `job_id`) |
| `GET` | `/v1/jobs/{job_id}` | 진행률·단계·실패 건수 |
| `POST` | `/v1/sources` | 소스 등록(디렉터리/DB). 관리자 전용, `config`에 비밀값 저장 금지 |
| `POST` | `/v1/sources/{id}/sync` | 수동 동기화 시작 |
| `GET` | `/v1/sources/{id}` | 마지막 커서·성공/실패 통계 |
| `POST` | `/v1/admin/reindex` | `parser_version`/`embedding_model` 변경 시 전체 재색인 |
| `GET` | `/v1/documents/{id}/chunks` | 관리자 감사용(원문 노출 정책 별도) |

기존 계약(`/v1/documents/search`, 인용 메타만 반환)은 **변경하지 않는다**.

---

## 12. 보안

| 위협 | 통제 |
| --- | --- |
| 악성 문서(매크로·zip bomb·XXE) | 파서 격리(kordoc gVisor, Tika 별도 컨테이너), 압축 해제 상한, DTD 비활성 |
| 파서 RCE | 파서는 API 프로세스에서 실행하지 않음(sidecar). `KORDOC_SANDBOX=gvisor` |
| PII 유출 | 색인 전 Presidio/PolicyEngine 마스킹, 인용은 메타만(현행 유지) |
| ACL 우회 | 색인 payload에 최종 ACL 고정 + 검색 후 재검증(현행) |
| DB 자격 증명 | Secret manager 참조. `kb_source.config`에는 참조 키만 |
| 원본 보관 | Raw Vault 접근은 API만. `raw_uri`를 클라이언트에 반환하지 않음 |
| 감사 | `document.ingested`, `document.parse_failed`, `document.tombstoned`, `source.synced` 이벤트 |

---

## 13. 단계별 로드맵

### P0 — 다중 포맷 업로드 (2~3주)
- [x] `ingest/` 모듈: IR, 파서 레지스트리, Broadway식 배처/파이프라인 (테스트 19개 통과)
- [ ] `TextParser`, `MarkdownParser` → `kordoc` 워커 → `docling` → `Tika` fallback
- [ ] MIME 스니핑(`python-magic` 또는 확장자+magic bytes), 크기 상한 20MB
- [ ] 표·헤딩 인지 청킹, Qdrant payload 확장
- [ ] `document.parse_failed` 감사 + DLQ 테이블
- **수용 기준**: HWP/HWPX/PDF/DOCX/XLSX/PPTX/PNG 각 1건 업로드 → 검색에서 인용과 함께 반환. 실패 1건이 다른 업로드를 막지 않음.

### P1 — DB 지식화 (4~6주)
- [ ] SQLAlchemy introspection + 스키마 프로파일
- [ ] dlt 또는 자체 커서 기반 증분
- [ ] 행 직렬화 템플릿, ACL 매핑, PII 마스킹
- [ ] `kb_source`/`kb_job`/`kb_dead_letter`, 잡 API
- [ ] 재색인·버전·tombstone
- **수용 기준**: Postgres 테이블 1개(10만 행) 동기화 → 자연어 검색으로 특정 행 근거 인용. 동기화 중 API 응답 지연 p95 2배 이내.

### P2 — 실시간·확장
- [ ] Debezium CDC → 동일 IR
- [ ] Cube 시맨틱 레이어 연동(집계 질의 분리)
- [ ] 워커 수평 확장(NATS) / 장기 잡(Temporal)
- [ ] MinIO Raw Vault, 보존·삭제 정책
- **수용 기준**: DB 변경이 60초 내 검색에 반영. 워커 2개로 처리량 선형 증가.

---

## 14. 테스트 전략

| 층 | 방법 |
| --- | --- |
| IR/직렬화 | 골든 파일(파서 출력 JSON 고정) |
| 파서 어댑터 | 포맷별 샘플 3종 + 실패 샘플(손상 파일) |
| 파이프라인 | 배압(큐 상한), 배칭(크기/시간), 재시도→DLQ, graceful shutdown |
| 멱등성 | 동일 문서 2회 → Qdrant 포인트 수 불변 |
| ACL | 역할별 검색 결과 차집합 검증(기존 테스트 확장) |
| DB | Docker Postgres + 시드 데이터, 커서 증분 재실행 |
| E2E | Playwright: 업로드 → 검색 → 인용 표시 |

프로토타입 테스트: `apps/api/tests/test_ingest_pipeline.py` (19개, 파서·IR·배압·배칭·재시도·DLQ·graceful stop).

---

## 15. 배선 예시

`main.post /v1/documents`를 교체할 때의 최소 형태다. 파서·청커·인덱서는 그대로 두고 **호출 방식만** 파이프라인으로 바꾼다.

```python
import asyncio

from baryon.ingest import Acl, Pipeline, PipelineConfig, REGISTRY, chunk_document

config = PipelineConfig(batch_size=32, batch_timeout_s=0.5, max_in_flight=4)


async def index_batch(batch: list[SourceItem]) -> None:
    documents = []
    for item in batch:
        document = await asyncio.to_thread(
            REGISTRY.parse,
            item.payload,
            item.filename,
            Acl(item.organization_id, item.owner_id, item.allowed_roles),
            item.uri,
            item.mime,
        )
        documents.append(document)

    chunks = [(document, chunk) for document in documents for chunk in chunk_document(document)]
    vectors = await asyncio.to_thread(vector._embed, [chunk.text for _, chunk in chunks])
    await asyncio.to_thread(upsert_points, documents, chunks, vectors)  # 문서 단위 롤백 포함
    for document in documents:
        audit.emit("document.ingested", principal, trace_id, document_id=document.id)


pipeline = Pipeline(index_batch, config)

# 업로드 엔드포인트: 큐에 넣고 즉시 job_id 반환
stats = await pipeline.run(items)   # 또는 백그라운드 태스크로 실행하고 /v1/jobs/{id}로 폴링
```

**주의할 점**
1. 파싱은 CPU 바운드 → 반드시 `asyncio.to_thread` 또는 프로세스 워커.
2. `vector._embed`는 내부 락으로 직렬화되어 있다. 배치 크기를 키우면 락 대기 시간이 늘어나므로 `max_in_flight`와 함께 조정한다.
3. `index_batch`는 **문서 단위 롤백**을 책임진다. 예외를 그대로 올리면 파이프라인이 배치를 재시도하고, 최종 실패 시 DLQ로 보낸다.
4. `stop()`은 생산만 멈춘다. 진행 중 배치는 완료된다(graceful shutdown 계약).

---

## 16. 열린 결정

1. **kordoc 호출 방식**: CLI subprocess vs HTTP 워커 vs 공식 Python SDK. → HTTP 워커 권장(타임아웃·격리·재사용).
2. **docling vs MinerU/marker**: GPU가 있으면 MinerU가 품질 우위, 없으면 docling. → P0은 docling, 품질 미달 문서만 MinerU 배치.
3. **청커**: chonkie 도입 시 기존 `chunk_text`와 토큰 단위 정합 필요. → 표/헤딩은 chonkie, 폴백은 현행 유지.
4. **DB 행 단위 문서화**: 100만 행 이상 테이블은 테이블 단위 문서 + 행 청크 혼합 필요.
5. **오브젝트 스토리지**: MinIO는 AGPL. 고객사 재배포 형태에 따라 S3/파일시스템 유지 검토.
