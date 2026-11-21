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
| HTTPS | ✅ | `https_enforced: true`, 인증서 approved (2027-02-11 만료, 자동 갱신) |
| 커스텀 도메인 | ✅ | `https://baryon-ax.baryon.ai/` 라이브, `baryonlabs.github.io/baryon-ax/` → 301 |
| Google Search Console | ✅ | `sc-domain:baryon.ai` sitemap 4개 발견, 4개 페이지 모두 색인. 2026-11-16 홈페이지 개편 후 `/`, `/en/` 색인 재요청 |
| Naver Search Advisor | ✅ | 소유확인 완료, `sitemap.xml` 제출 완료(2026-11-13 03:22) |
| Bing Webmaster Tools | ✅ | 2026-11-16 `msvalidate.01` 메타로 소유 확인, `sitemap.xml` 제출 |
| IndexNow | ✅ | 2026-11-13 새 도메인 4개 URL 제출, `api.indexnow.org`·`bing.com` 모두 200 |
| Daum 검색등록 | 🟡 | 등록 신청 완료(2026-11-13), 검토 결과 이메일 대기 |
| SEO/AEO/GEO 자산 | ✅ | title/description, canonical, hreflang, JSON-LD 8종, FAQ, `llms.txt` 3종, OG 2종 |
| 자동 검증 | ✅ | `check_site_seo.py` 0/0, `check_site_live.py` 새 도메인 0 failures |
| 분석 | ✅ | Cloudflare Web Analytics(11/13), Microsoft Clarity·GA4(11/16). 모두 `site/assets/analytics.js` 한 곳에서 로드. 클릭 이벤트 4종은 [10. 핵심 지표](#10-핵심-지표) 참고 |

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

`baryon.ai`의 **도메인 속성(`sc-domain:baryon.ai`)** 이 DNS TXT로 인증되어 있으므로 `baryon-ax.baryon.ai`는 자동으로 포함됩니다. 서브도메인별 상세 리포트가 필요할 때만 URL 접두어 속성을 추가합니다.

1. https://search.google.com/search-console → `baryon.ai` 속성 선택
2. **Sitemaps** → `https://baryon-ax.baryon.ai/sitemap.xml` 제출
3. 발견된 URL이 **4개**인지 확인 (`/`, `/en/`, `/open-source/`, `/en/open-source/`)
4. **URL 검사** → `/`, `/en/`, `/open-source/`, `/en/open-source/` 각각 **색인 생성 요청**
5. (선택) URL 접두어 속성을 추가할 경우에만 HTML 태그 방식을 사용:

```bash
python3 scripts/set_site_verification.py --google "<token>"
python3 scripts/check_site_seo.py
# PR 생성 → CI/security-sweep 통과 → squash merge → Pages 배포 대기
```

> 소유자는 개인 계정이 아니라 회사 장기 운영 계정(`ai.baryon.ai@gmail.com` 등)으로 추가하고, 2FA·복구 수단을 설정하세요.

---

## 3. Naver Search Advisor

소유확인 메타태그가 이미 배포되어 있습니다(2026-11-13). 콘솔에서 **소유확인**과 **sitemap 제출**만 남았습니다.

1. https://searchadvisor.naver.com/console/board → 사이트 추가
2. URL: `https://baryon-ax.baryon.ai/`
3. 콘솔에서 **소유확인** 클릭 (CAPTCHA는 사람이 직접)
4. **요청 → 사이트맵 제출** → `https://baryon-ax.baryon.ai/sitemap.xml`

배포된 메타 확인:

```bash
curl -s https://baryon-ax.baryon.ai/ | grep naver-site-verification
```

토큰을 교체해야 할 때만:

```bash
python3 scripts/set_site_verification.py --naver "<token>"
python3 scripts/check_site_seo.py
# PR → CI/security-sweep 통과 → 머지 → Pages 배포
```

> 커스텀 도메인 `baryon-ax.baryon.ai`를 사용하므로 공용 `github.io` 호스팅 제한은 해당되지 않습니다.

---

## 4. Bing · IndexNow

### 4.1 IndexNow (로그인 불필요)

키 파일과 스크립트가 준비되어 있습니다.

```bash
python3 scripts/submit_indexnow.py
```

- host: `baryon-ax.baryon.ai`
- keyLocation: `https://baryon-ax.baryon.ai/45ad07e4552fd93fc2b21d7bc6cb45a6.txt`
- URL 목록: `site/sitemap.xml` (4개)
- 엔드포인트: `api.indexnow.org`, `www.bing.com/indexnow`
- 결과 (2026-11-13): 4개 URL 제출, 두 엔드포인트 모두 **200**

키를 교체하려면 `site/<새 32자 hex>.txt`를 만들고 기존 파일을 삭제한 뒤 스크립트를 다시 실행합니다.

> 커스텀 도메인이 생기면서 키 파일을 도메인 루트에 둘 수 있게 되었습니다. `keyLocation`은 sitemap과 같은 호스트를 사용합니다.

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

추가 채널 링크(2026-11 채널 다변화):

| 채널 | 링크 |
|---|---|
| Threads | `https://baryon-ax.baryon.ai/?utm_source=threads&utm_medium=social&utm_campaign=<캠페인>&utm_content=<게시물>` |
| GeekNews | `https://baryon-ax.baryon.ai/?utm_source=geeknews&utm_medium=community&utm_campaign=<캠페인>` |
| Discord·Slack 커뮤니티 | `https://baryon-ax.baryon.ai/?utm_source=<커뮤니티명>&utm_medium=community&utm_campaign=<캠페인>` |
| 기술 블로그(자체) | `https://baryon-ax.baryon.ai/?utm_source=blog&utm_medium=owned&utm_campaign=<글_주제>` |
| 발표·밋업 슬라이드 | `https://baryon-ax.baryon.ai/?utm_source=talk&utm_medium=event&utm_campaign=<행사명>` |

규칙:

- `utm_source`는 아래 고정 목록에서만 고릅니다: `threads`, `linkedin`, `x`, `facebook`, `geeknews`, `naver_blog`, `blog`, `github`, `email`, `talk`, 커뮤니티 이름(소문자). 같은 채널을 다른 이름으로 적으면 GA4에서 둘로 갈라집니다.
- `utm_medium`은 `social`, `community`, `owned`, `referral`, `email`, `event` 중 하나입니다.
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

첫 주(11/09~11/16) 방문의 약 55%가 Threads 한 곳에서 왔습니다. 게시물 하나에 유입이 몰리면 다음 주에 급감하므로, 아래 우선순위로 채널을 넓힙니다.

| 우선순위 | 채널 | 왜 | 첫 액션 |
|---|---|---|---|
| 1 | GeekNews | 한국 개발자·기술 리더가 오픈소스 신작을 찾는 곳. HWP·온프레미스 주제와 맞음 | v0.7.0 릴리스에 맞춰 "Show GN" 1건 |
| 2 | LinkedIn | DX·AX 담당자와 보안 검토자에게 직접 닿음 | 아키텍처 도식 + 보안 CI 글 주 1회 |
| 3 | 자체 기술 블로그·Naver 블로그 | 검색 유입이 오래 남음 (HWP RAG, 폐쇄망 설치) | 설치·HWP 파싱 실무 글 월 2회 |
| 4 | Slack·Discord 커뮤니티 | 봇 예제(Slack·Discord)를 직접 써 볼 사람들 | 봇 예제 공개 후 사용기 1건 |
| 유지 | Threads | 현재 최대 유입. 단, 링크에 UTM을 반드시 붙임 | 릴리스마다 1건 |

| 채널 | 내용 | 빈도 | 상태 |
|---|---|---|---|
| 웹사이트 | 기능·문서·FAQ 갱신 | 릴리스 시 | ✅ |
| Threads | 출시·기능 소식 | 릴리스 시 | ✅ (첫 주 최대 유입) |
| GeekNews | Show GN | 릴리스 시 | ❌ |
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

## 10. 핵심 지표

홈페이지의 목표는 **방문자가 첫 화면에서 다음 행동으로 넘어가는 것**입니다. 그래서 방문 수보다 "행동한 방문의 비율"을 먼저 봅니다.

| 구분 | 지표 | 정의 | 출처 |
|---|---|---|---|
| 핵심 | 행동 전환율 | `github_click` 또는 `copy_command`가 1회 이상 있는 세션 ÷ 전체 세션 | GA4 |
| 보조 | 시작 의향 | `copy_command` 중 `block`이 "Terminal · macOS"(1줄 시작)인 비율 | GA4 이벤트 매개변수 |
| 보조 | 문의 의향 | `support_click` 수 | GA4 |
| 보조 | 깊이 | 첫 화면 아래까지 스크롤한 세션 비율 | Clarity |
| 진단 | 유입 분산 | 상위 1개 유입원이 차지하는 방문 비율 (목표: 50% 미만) | Cloudflare WA · GA4 |
| 진단 | 검색 노출 | `*baryon-ax.baryon.ai` 필터 노출·클릭 | Search Console |

이벤트는 `site/assets/analytics.js`가 Clarity와 GA4에 같은 이름으로 보냅니다.

| 이벤트 | 언제 |
|---|---|
| `copy_command` | 코드 블록의 복사 버튼 (`block`: 블록 제목, `lang`) |
| `start_cta_click` | 헤더·히어로의 "1줄로 시작" (`place`: header/hero) |
| `github_click` | 저장소로 가는 링크 |
| `support_click` | GitHub Issues(기술지원)로 가는 링크 |

GA4에서는 `copy_command`, `github_click`, `support_click`을 **주요 이벤트**로 표시해 두면 획득 보고서에서 유입원별 전환을 바로 볼 수 있습니다(이벤트가 한 번 수집된 뒤 관리 → 이벤트에서 설정).

주간 리포트는 위 표 순서대로 작성하고, 숫자는 대시보드에서 읽은 값만 씁니다. 트래픽 수치는 이 공개 저장소에 커밋하지 않습니다.

---

## 11. 커스텀 도메인 이전 시

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

## 12. 트러블슈팅

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

## 13. 계정·자산

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

## 14. 다음 액션

1. ~~커스텀 도메인 이전 + HTTPS 강제~~ (2026-11-13 완료, 인증서 approved)
2. ~~`python3 scripts/check_site_live.py`~~ (2026-11-13 완료, 새 도메인 failures 0)
3. ~~`python3 scripts/submit_indexnow.py`~~ (2026-11-13 완료, 4개 URL · 200/200)
4. ~~Google Search Console sitemap 제출 + 핵심 URL 색인 요청~~ (2026-11-13 완료, 4개 발견)
5. ~~Naver Search Advisor 소유확인 + sitemap 제출~~ (2026-11-13 완료)
6. ~~Daum 검색등록 신청~~ (2026-11-13 완료, 검토 대기)
7. Bing Webmaster Tools 로그인·사이트 추가 (IndexNow는 완료, 대시보드는 선택)
8. ~~분석 도구 연결~~ (2026-11-13 Cloudflare Web Analytics 설치)
9. LinkedIn·Naver 블로그 첫 글 발행 ([docs/promotion-drafts.md](promotion-drafts.md))

---

## 15. 개시 준비 상태 (2026-11-13)

### 완료된 항목

- [x] DNS `baryon-ax.baryon.ai` → `baryonlabs.github.io` (DNS only)
- [x] GitHub Pages 커스텀 도메인 + Let's Encrypt 인증서 approved (`https_enforced: true`)
- [x] `https://baryonlabs.github.io/baryon-ax/` → `https://baryon-ax.baryon.ai/` 301
- [x] `python3 scripts/check_site_seo.py` → failures 0, warnings 0
- [x] `python3 scripts/check_site_live.py` → failures 0, warnings 0
- [x] `python3 scripts/submit_indexnow.py` → 4개 URL, `api.indexnow.org` 200, `www.bing.com` 200
- [x] Google 도메인 속성 인증용 DNS TXT 확인 (`baryon.ai`)
- [x] Google Search Console sitemap 제출(4개 발견) + 핵심 URL 색인
- [x] Naver Search Advisor 소유확인 + sitemap 제출
- [x] Daum 검색등록 신청 (검토 대기)
- [x] Cloudflare Web Analytics beacon 설치 (`baryon-ax.baryon.ai`)
- [x] title/description/canonical/hreflang, JSON-LD, FAQ, `llms.txt` 3종, OG 2종, sitemap 4개 URL
- [x] `robots.txt` AI 크롤러 허용 + sitemap 명시
- [x] 홍보 문안·UTM 링크 ([docs/promotion-drafts.md](promotion-drafts.md))

### 검색엔진 등록 결과 (2026-11-13)

| 엔진 | 결과 | 비고 |
|---|---|---|
| Google Search Console | ✅ sitemap 제출, **4개 발견**, `/`·`/en/`·`/open-source/` 색인 완료 | `/en/open-source/` 색인 생성 요청됨 |
| Naver Search Advisor | ✅ 소유확인 완료, `sitemap.xml` 제출(03:22) | 수집·색인 리포트는 시간 필요 |
| Daum 검색등록 | 🟡 신청 완료 | `ai.baryon.ai@gmail.com`으로 검토 결과 발송 예정 |
| Bing IndexNow | ✅ 4개 URL, 200/200 | BWT 대시보드와 별개로 수집 진행 |
| Bing Webmaster Tools | 🟡 보류 | Microsoft 세션이 BWT에서 인식되지 않음. IndexNow로 URL 제출은 완료 |

### 남은 항목

#### 1) Bing Webmaster Tools (선택)

Bing 수집은 IndexNow로 이미 진행 중입니다. 대시보드가 필요할 때만:

1. https://www.bing.com/webmasters → Microsoft 로그인
2. **Import from Google Search Console** 또는 **사이트 추가**
3. `https://baryon-ax.baryon.ai/` 등록
4. sitemap 제출 → URL 검사
5. 메타 검증이 필요할 때만 `python3 scripts/set_site_verification.py --bing "<token>"`

> 2026-11-13 확인 시 `chrome-k70n` 프로필은 Bing에는 `Seungwoo`로 로그인되어 있었지만, BWT 대시보드는 계속 `Sign In`을 표시했습니다. Microsoft 계정 쪽 로그인 루프로 보이며, IndexNow 제출은 정상입니다.

#### 2) Daum 검토 결과 확인

- `ai.baryon.ai@gmail.com` 메일함에서 Daum 검색등록 검토 결과 확인
- 거절 시 사유에 따라 설명·디렉토리 보완 후 재신청
- 신청 URL: `http://baryon-ax.baryon.ai/` (Daum 폼이 http prefix를 사용)

#### 3) 분석 도구 — 완료

- Cloudflare Web Analytics에 `baryon-ax.baryon.ai` 사이트를 추가하고 beacon 스니펫을 `site/index.html`·`site/en/index.html`의 `</body>` 앞에 삽입했습니다.
- 배포 후 **Cloudflare Dashboard → Web Analytics → baryon-ax.baryon.ai**에서 페이지뷰·방문자를 확인합니다.
- GA4를 추가로 붙일 경우 measurement ID를 `gtag.js`로 삽입합니다.

### 개시 당일 순서

1. `main` 최신 커밋이 Pages에 배포됐는지 확인
   ```bash
   gh run list --workflow=pages.yml --limit 1
   python3 scripts/check_site_live.py
   ```
2. IndexNow 재제출 (콘텐츠 변경이 있었다면)
   ```bash
   python3 scripts/submit_indexnow.py
   ```
3. Google Search Console: 4개 URL 발견·색인 상태 확인
4. Naver: 수집·색인 현황 확인 (리포트 생성까지 시간 필요)
5. Daum 검토 결과 메일 확인
6. LinkedIn·Naver 블로그·GitHub 릴리스 발행 ([docs/promotion-drafts.md](promotion-drafts.md))
7. 24시간 후 `check_site_live.py`와 검색엔진 수집 현황 확인
