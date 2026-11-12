# 검색엔진 등록 · 홍보 운영 플레이북 (Baryon AX)

> 대상: https://baryon-ax.baryon.ai/ · `/en/`
> 저장소: https://github.com/baryonlabs/baryon-ax
> 최초 작성: 2026-11-13
> 기술 SEO/AEO/GEO 자산은 [docs/site-seo.md](site-seo.md)에 정리되어 있습니다.

이 문서는 **등록·홍보·측정**만 다룹니다. 2026-11-13 기준 상태를 먼저 적고, 같은 절차를 반복 실행할 수 있게 명령과 체크리스트를 남깁니다.

---

## 0. 현재 상태

| 항목 | 상태 | 비고 |
|---|---|---|
| 호스팅 | ✅ | GitHub Pages, `main` push 시 `.github/workflows/pages.yml` 배포 |
| HTTPS | ✅ | `https_enforced: true` |
| 커스텀 도메인 | ✅ | 2026-11-13 `baryon-ax.baryon.ai` 연결 (DNS only), GitHub Pages 인증서 발급 대기 |
| Google Search Console | ❌ | 등록·sitemap 제출 필요 |
| Naver Search Advisor | ❌ | 등록·소유확인 필요 |
| Bing Webmaster Tools | ❌ | 등록 또는 GSC import 필요 |
| IndexNow | ✅ | 2026-11-13 4개 URL 제출, `api.indexnow.org`·`bing.com` 모두 202 |
| Daum 검색등록 | ❌ | Kakao 계정 필요 |
| SEO/AEO/GEO 자산 | ✅ | title/description, canonical, hreflang, JSON-LD 8종, FAQ, `llms.txt` 3종, OG 2종 |
| 자동 검증 | ✅ | `scripts/check_site_seo.py` (로컬), `scripts/check_site_live.py` (배포) |
| 분석 | ❌ | GA4/Plausible 등 미연결 |

### 자동화 자산

| 파일 | 역할 |
|---|---|
| `site/45ad07e4552fd93fc2b21d7bc6cb45a6.txt` | IndexNow 키 (파일명 = 키) |
| `scripts/submit_indexnow.py` | sitemap URL을 IndexNow로 제출 |
| `scripts/check_site_live.py` | 배포된 URL·메타·OG·llms 실제 검증 |
| `scripts/set_site_verification.py` | Google/Naver/Bing 소유확인 메타 주입·제거 |
| `scripts/set_site_dns.py` | Cloudflare에 GitHub Pages CNAME 생성 (DNS only) |
| `scripts/apply_site_domain.py` | 커스텀 도메인으로 URL 일괄 이전 + `site/CNAME` 생성 |
| `docs/domain-migration.md` | 도메인 이전·인증서·재등록·롤백 절차 |
| `scripts/check_site_seo.py` | 로컬 SEO/AEO/GEO 게이트 |
| `scripts/apply_site_meta.py` | OG/Twitter/JSON-LD 동기화 |

---

## 1. 배포 후 기본 검증

```bash
# 1) 로컬 게이트
python3 scripts/check_site_seo.py

# 2) Pages 워크플로 완료 확인
gh run list --workflow=pages.yml --limit 3

# 3) 라이브 검증 (배포 후 수 분 대기)
python3 scripts/check_site_live.py

# 4) IndexNow 제출
python3 scripts/submit_indexnow.py --dry-run   # payload 확인
python3 scripts/submit_indexnow.py             # 실제 제출
```

정상 기준:

- `check_site_seo.py`: `failures: 0, warnings: 0`
- `check_site_live.py`: `failures: 0`
- IndexNow: `api.indexnow.org` 200/202, `www.bing.com` 200/202

> GitHub Pages는 배포 직후 몇 분간 이전 HTML을 캐시할 수 있습니다. `check_site_live.py`가 제목 불일치를 보고하면 5분 뒤 재실행하세요.

---

## 2. Google Search Console

`*.github.io`는 도메인 속성(`sc-domain:`)을 쓸 수 없으므로 **URL 접두어 속성**으로 등록합니다.

1. https://search.google.com/search-console → **속성 추가** → **URL 접두어**
2. `https://baryon-ax.baryon.ai/` 입력
3. 소유확인 방법에서 **HTML 태그** 선택 → `content` 값 복사
4. 메타 주입 후 PR·머지·배포:

```bash
python3 scripts/set_site_verification.py --google "<token>"
python3 scripts/check_site_seo.py
# PR 생성 → CI/security-sweep 통과 → squash merge → Pages 배포 대기
```

5. 배포 확인:

```bash
curl -s https://baryon-ax.baryon.ai/ | grep google-site-verification
```

6. 콘솔에서 **확인** 클릭
7. **Sitemaps** → `https://baryon-ax.baryon.ai/sitemap.xml` 제출 → 발견 URL 4개 확인
8. **URL 검사** → `/` 와 `/en/` 각각 **색인 생성 요청**

> 소유자는 개인 계정이 아니라 연구소/회사 장기 운영 계정으로 추가하고, 2FA·복구 수단을 설정하세요.

---

## 3. Naver Search Advisor

1. https://searchadvisor.naver.com/console/board → 사이트 추가
2. URL: `https://baryon-ax.baryon.ai/`
3. 소유확인 → **HTML 태그** → 토큰 복사
4. 주입:

```bash
python3 scripts/set_site_verification.py --naver "<token>"
```

5. 배포 후 `curl -s https://baryon-ax.baryon.ai/ | grep naver-site-verification` 확인
6. 콘솔에서 **소유확인** (CAPTCHA는 사람이 직접)
7. **요청 → 사이트맵 제출** → `https://baryon-ax.baryon.ai/sitemap.xml`

> Naver는 공용 호스팅 서브도메인(`github.io`) 등록을 제한할 수 있습니다. 거절되면 커스텀 도메인 이전 후 재시도하는 편이 빠릅니다.

---

## 4. Bing · IndexNow

### 4.1 IndexNow (로그인 불필요)

키 파일과 스크립트가 준비되어 있습니다.

```bash
python3 scripts/submit_indexnow.py
```

- host: `baryonlabs.github.io`
- keyLocation: `https://baryon-ax.baryon.ai/45ad07e4552fd93fc2b21d7bc6cb45a6.txt`
- URL 목록: `site/sitemap.xml`
- 엔드포인트: `api.indexnow.org`, `www.bing.com/indexnow`

키를 교체하려면 `site/<새 32자 hex>.txt`를 만들고 기존 파일을 삭제한 뒤 스크립트를 다시 실행합니다.

### 4.2 Bing Webmaster Tools

1. https://www.bing.com/webmasters → Microsoft 로그인
2. **Import from Google Search Console** 또는 **사이트 추가**
3. `https://baryon-ax.baryon.ai/` 등록
4. 메타 검증이 필요하면: `python3 scripts/set_site_verification.py --bing "<token>"`
5. sitemap 제출 → URL 검사

---

## 5. Daum · Kakao

1. https://register.daum.net/ → Kakao 로그인
2. 사이트 추가: `https://baryon-ax.baryon.ai/`
3. 소유확인(메타태그) → sitemap 제출

---

## 6. UTM 링크

`utm_source`는 채널, `utm_medium`은 유형, `utm_campaign`은 캠페인, `utm_content`는 소재 구분에 사용합니다. 같은 링크를 재사용하지 말고 캠페인마다 새로 만드세요.

| 채널 | 링크 |
|---|---|
| LinkedIn (출시) | `https://baryon-ax.baryon.ai/?utm_source=linkedin&utm_medium=social&utm_campaign=launch_2026_11&utm_content=post_ko` |
| LinkedIn (EN) | `https://baryon-ax.baryon.ai/en/?utm_source=linkedin&utm_medium=social&utm_campaign=launch_2026_11&utm_content=post_en` |
| X / Threads | `https://baryon-ax.baryon.ai/?utm_source=x&utm_medium=social&utm_campaign=launch_2026_11&utm_content=thread` |
| Naver 블로그 | `https://baryon-ax.baryon.ai/?utm_source=naver_blog&utm_medium=owned&utm_campaign=hwp_rag_guide&utm_content=post` |
| GitHub 릴리스 | `https://baryon-ax.baryon.ai/?utm_source=github&utm_medium=referral&utm_campaign=release_v0_6_0&utm_content=release_notes` |
| README | `https://baryon-ax.baryon.ai/?utm_source=github&utm_medium=referral&utm_campaign=readme&utm_content=website_link` |
| 이메일 서명 | `https://baryon-ax.baryon.ai/?utm_source=email&utm_medium=signature&utm_campaign=always_on&utm_content=default` |

규칙:

- `utm_campaign`은 `런칭시기_주제` 형식(소문자·밑줄)으로 통일합니다.
- 문서·README에는 **UTM 없는 canonical URL**만 넣습니다. UTM은 홍보 링크에만 붙입니다.
- 커스텀 도메인으로 이전하면 위 링크의 호스트만 교체합니다.

---

## 7. AI 답변엔진 인용 추적

월 1회 아래 프롬프트를 ChatGPT·Perplexity·Gemini·Claude에서 실행하고, **인용 URL·문장·날짜**를 기록합니다.

### 한국어

```text
온프레미스 AI 에이전트 플랫폼 추천
사내 문서 RAG 출처 인용 오픈소스
HWP HWPX PDF 검색 AI 에이전트
폐쇄망 에어갭 AI 챗봇 구축
암묵지 지식화 AI 플랫폼
Baryon AX
```

### English

```text
on-premises AI agent platform open source
grounded RAG with source citations self-hosted
air-gapped enterprise RAG platform
HWP HWPX document AI search
tacit knowledge capture AI platform
Baryon AX
```

기록 템플릿:

| 날짜 | 엔진 | 프롬프트 | 인용됨 | 인용 URL/문장 | 비고 |
|---|---|---|---|---|---|
| 2026-11-13 | Perplexity | on-premises AI agent platform | ❌ | — | 초기 |

---

## 8. 채널 운영

| 채널 | 내용 | 빈도 | 상태 |
|---|---|---|---|
| 웹사이트 | 기능·문서·FAQ 갱신 | 릴리스 시 | ✅ |
| GitHub | README, 릴리스 노트, topics | 릴리스 시 | ✅ (topics 9개) |
| LinkedIn | 제품 소개, 아키텍처·보안 CI | 주 1회 | ❌ |
| Naver 블로그 | HWP/RAG/에어갭 실무 글 | 주 1회 | ❌ |
| YouTube | 데모·설치 영상 | 월 1회 | ❌ |
| 뉴스레터 | 릴리스·사례 | 월 1회 | ❌ |
| 외부 커뮤니티 | Hacker News, Reddit r/MachineLearning, Hugging Face | 릴리스 시 | ❌ |

홍보 문안은 [docs/promotion-drafts.md](promotion-drafts.md)에 있습니다.

---

## 9. 모니터링

### 주간

- [ ] Google Search Console: 색인 오류·수동 조치 확인
- [ ] Naver Search Advisor: 수집·색인 현황
- [ ] `python3 scripts/check_site_live.py`
- [ ] sitemap/robots/llms 200 확인 (스크립트에 포함)
- [ ] AI 답변엔진 인용 1회 확인

### 월간

- [ ] Search Console 실적(노출·클릭) 캡처
- [ ] Naver 유입 키워드 확인
- [ ] 분석 도구(referrer 포함) 확인
- [ ] GitHub 유입 클릭·star 추이
- [ ] OG 이미지·메타 최신성 확인
- [ ] 새 페이지 추가 시 sitemap·llms·FAQ 갱신

### 분기

- [ ] KPI 재보정 (색인 수, 인용 수, GitHub 유입)
- [ ] 외부 백링크·인용 확보
- [ ] 콘텐츠 허브 확장 (설치 가이드, HWP 파싱, 보안 CI)
- [ ] 미완료 채널(Bing·Daum) 정리
- [ ] 커스텀 도메인 이전 검토

---

## 10. 커스텀 도메인 이전 시

`baryon-ax.baryon.ai`로 이전하는 전체 절차는 [docs/domain-migration.md](domain-migration.md)에 있습니다.

```bash
# DNS (Zone.DNS:Edit 토큰 필요)
python3 scripts/set_site_dns.py --dry-run
python3 scripts/set_site_dns.py --apply

# URL 일괄 이전
python3 scripts/apply_site_domain.py --domain baryon-ax.baryon.ai
python3 scripts/apply_site_domain.py --domain baryon-ax.baryon.ai --write
```

`apply_site_domain.py`가 `site/CNAME`, canonical·OG·hreflang, sitemap, robots, llms, 스크립트 `BASE`, README·docs를 한 번에 갱신합니다. 이전 후 `python3 scripts/check_site_seo.py && python3 scripts/check_site_live.py --base https://baryon-ax.baryon.ai/`로 확인합니다.

---

## 11. 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| 라이브 제목이 로컬과 다름 | Pages 캐시/배포 지연 | 워크플로 완료 확인, 5분 후 재실행 |
| `og:image` 404 | 경로·배포 누락 | `site/assets/og*.jpg` 존재 확인, `check_site_live.py` |
| Google 소유확인 실패 | 메타 미배포/캐시 | `curl`로 태그 확인 후 재시도 |
| Naver 소유확인 실패 | 공용 도메인 제한/캐시 | 커스텀 도메인 이전 검토 |
| IndexNow 403 | keyLocation 불일치 | 키 파일명·내용 일치 확인, 배포 후 재실행 |
| sitemap 미발견 | canonical·noindex 충돌 | `check_site_seo.py`, URL 검사 |
| AI 크롤러 차단 | robots.txt | AI crawler allow 확인 |

---

## 12. 계정·자산

| 자산 | 위치 | 비고 |
|---|---|---|
| 소스 코드 | GitHub `baryonlabs/baryon-ax` | public, MIT |
| 배포 | GitHub Pages (workflow) | `main` 머지 시 자동 |
| IndexNow 키 | `site/45ad07e4552fd93fc2b21d7bc6cb45a6.txt` | 공개 키 |
| 검증 메타 | `site/index.html`, `site/en/index.html` | `set_site_verification.py`로 관리 |
| OG 이미지 | `site/assets/og.jpg`, `og-en.jpg` | 1200×630 |
| SEO 가이드 | `docs/site-seo.md` | 자산·검증·한계 |
| 홍보 문안 | `docs/promotion-drafts.md` | LinkedIn/Naver/GitHub |

**문서에 남기지 말 것**: 비밀번호, OAuth 토큰, Cloudflare/GitHub API 토큰, 개인 연락처.

---

## 13. 다음 액션

1. ~~머지·배포 후 `python3 scripts/check_site_live.py` 1회~~ (2026-11-13 완료, failures 0)
2. ~~`python3 scripts/submit_indexnow.py` 1회~~ (2026-11-13 완료, 202 Accepted)
3. Google Search Console URL 접두어 속성 등록 + sitemap 제출
4. Naver Search Advisor 등록 (거절 시 도메인 이전 검토)
5. Bing Webmaster(GSC import) · Daum 등록
6. 분석 도구 연결 (GA4 또는 Plausible)
7. LinkedIn·Naver 블로그 첫 글 발행 ([docs/promotion-drafts.md](promotion-drafts.md))
