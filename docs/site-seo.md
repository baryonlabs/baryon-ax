# 정적 사이트 SEO · AEO · GEO 운영 가이드

> 대상: https://baryon-ax.baryon.ai/ (한국어) · `/en/` (English)
> 검증: `python3 scripts/check_site_seo.py` (로컬) · `python3 scripts/check_site_live.py` (배포)
> 메타 동기화: `python3 scripts/apply_site_meta.py all`
> 등록·홍보·측정: [docs/promotion-playbook.md](promotion-playbook.md)

---

## 1. 검색·AI 노출 전략

| 영역 | 목표 | 구현 |
| --- | --- | --- |
| **SEO** | 검색 결과에서 제목·설명·구조가 정확히 노출 | 고유 title/description, canonical, hreflang, sitemap, semantic heading, image alt |
| **AEO** | AI 답변 엔진이 질문에 바로 답하도록 | 첫 문단 answer-first 정의, FAQ(질문·답변), FAQPage/HowTo 구조화 데이터 |
| **GEO** | 생성형 엔진이 인용하기 좋은 출처 | `llms.txt` / `llms-full.txt`, 인용 URL·표기 가이드, canonical facts, `robots.txt` AI 크롤러 허용 |
| **공유** | Slack·X·LinkedIn 미리보기 품질 | 1200×630 OG 이미지(ko/en), `og:*`, `twitter:card=summary_large_image` |

---

## 2. 페이지별 메타

| | 한국어 (`/`) | English (`/en/`) |
| --- | --- | --- |
| title | `Baryon AX — 온프레미스 AI 에이전트 \| RAG 근거 인용, HWP·PDF·Office 검색` | `Baryon AX — On-premises AI Agents with Grounded RAG` |
| description | 145자: 문서·암묵지·메모리, HWP·PDF·Office, ACL·감사, MIT/Docker | 156자: grounded RAG citations, parsing, ACL/audit, MIT/Docker |
| OG image | `/assets/og.jpg` (1200×630, ~61KB) | `/assets/og-en.jpg` (1200×630, ~79KB) |
| canonical | `https://baryon-ax.baryon.ai/` | `https://baryon-ax.baryon.ai/en/` |
| hreflang | ko · en · x-default | ko · en · x-default |

OG/Twitter 제목·설명은 `<title>`/description과 **자동 동기화**됩니다(`apply_site_meta.py`).

---

## 3. 구조화 데이터 (JSON-LD)

`@graph`에 다음 노드를 포함합니다.

| 타입 | 역할 |
| --- | --- |
| `Organization` | Baryon Labs 엔터티, GitHub `sameAs` |
| `SoftwareApplication` | 제품명, 버전, 라이선스, 무료 offer, 저장소, 스크린샷 |
| `SoftwareSourceCode` | 코드 저장소, MIT, Python/TypeScript, Docker/Kubernetes |
| `WebSite` | 사이트 엔터티, 언어 |
| `WebPage` | 페이지 제목·설명·대표 이미지·언어 |
| `HowTo` | Docker 빠른 시작 4단계 |
| `BreadcrumbList` | Home (→ English) |
| `FAQPage` | 화면에 보이는 FAQ 상위 10개와 1:1 대응 |

> FAQPage는 **화면에 실제로 보이는 Q&A만** 넣습니다. 숨은 텍스트를 스키마에 넣지 않습니다.

---

## 4. AI 검색 인프라

| 파일 | 용도 |
| --- | --- |
| `site/llms.txt` | 한 페이지 요약: 정의, canonical facts, 기능, 빠른 시작, 인용 URL, FAQ |
| `site/llms-full.txt` | 확장 브리프: 아키텍처, 요청 흐름, 하드웨어, 거버넌스, 한계, 인용 가이드 |
| `site/en/llms.txt` | 영어 요약 |
| `site/robots.txt` | 전체 허용 + GPTBot·ClaudeBot·PerplexityBot·Google-Extended 등 명시, llms 경로 안내 |
| `site/sitemap.xml` | 네 HTML 페이지(`/`, `/en/`, `/open-source/`, `/en/open-source/`) + hreflang, lastmod 2026-11-13 |

`robots.txt`에는 다음을 명시합니다: `GPTBot`, `OAI-SearchBot`, `ChatGPT-User`, `ClaudeBot`, `Claude-SearchBot`, `PerplexityBot`, `Google-Extended`, `Applebot-Extended`, `CCBot`.

---

## 5. 검증

```bash
python3 scripts/check_site_seo.py
```

검사 항목(sitemap의 모든 페이지):
- title 25–65자, description 100–180자
- canonical · hreflang(ko/en/x-default) · robots
- OG/Twitter 필수 태그, OG 이미지 실파일 존재, 1200×630
- JSON-LD 파싱 및 페이지 유형별 필수 타입(랜딩: FAQPage 포함 / 하위: CollectionPage·BreadcrumbList), 랜딩 FAQ 3개 이상
- H1 1개, heading 레벨 점프 없음, 모든 `<img>` alt
- robots·sitemap·llms.txt·llms-full.txt·en/llms.txt 존재 및 canonical URL
- 경고: OG 이미지 400KB 초과, AI 크롤러 미명시, llms 링크 누락

현재 결과: **failures 0, warnings 0**.

배포 후에는 실제 URL을 검사합니다.

```bash
python3 scripts/check_site_live.py
```

- sitemap의 모든 페이지 HTTP 200 · `text/html`
- 배포된 title·canonical·`html lang`이 로컬 파일과 일치
- H1 1개, JSON-LD 파싱 및 FAQPage 존재
- `og:image` 실제 200 · `image/*`
- `robots.txt`·`sitemap.xml`·`llms.txt`·`llms-full.txt`·`en/llms.txt`·IndexNow 키 파일 200

IndexNow 제출은 `python3 scripts/submit_indexnow.py`입니다(자세한 절차는 [docs/promotion-playbook.md](promotion-playbook.md)).

---

## 6. 변경 시 체크리스트

1. `site/index.html` / `site/en/index.html`의 title·description·FAQ 수정
2. `python3 scripts/apply_site_meta.py all` — OG/Twitter/JSON-LD 동기화
3. `python3 scripts/check_site_seo.py` — 실패 0 확인
4. 썸네일 문구가 바뀌면 `site/assets/og.jpg`·`og-en.jpg` 재생성(1200×630, <400KB)
5. `site/llms.txt`·`llms-full.txt`의 버전·기능·URL 갱신
6. `site/sitemap.xml` lastmod 갱신
7. PR 생성 → CI·security-sweep 통과 후 머지 (main 직접 push 차단)
8. Pages 배포 완료 후 `python3 scripts/check_site_live.py`
9. 새 URL이면 `python3 scripts/submit_indexnow.py`로 제출

---

## 7. 한계

- GitHub Pages는 커스텀 `X-Robots-Tag`·콘텐츠 협상을 지원하지 않습니다.
- `*.github.io`는 GitHub의 플랫폼 정책을 따르므로 robots 규칙이 플랫폼 수준 제한을 덮어쓸 수 없습니다.
- 구조화 데이터는 리치 결과를 **보장하지 않습니다**. Google Search Console·Rich Results Test로 주기 확인이 필요합니다.
- OG 이미지는 정적 파일이므로 문구 변경 시 수동 재생성이 필요합니다.
- GitHub Pages는 배포 직후 수 분간 이전 HTML을 캐시할 수 있어, 라이브 검증은 워크플로 완료 후 실행해야 합니다.
- 커스텀 도메인 `baryon-ax.baryon.ai`가 라이브이므로 IndexNow 키를 도메인 루트에 둡니다. `keyLocation`은 `https://baryon-ax.baryon.ai/<key>.txt`입니다.
