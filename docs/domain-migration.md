# 커스텀 도메인 운영 (baryon-ax.baryon.ai)

> 현재: `https://baryon-ax.baryon.ai/`
> 이전: `https://baryonlabs.github.io/baryon-ax/` (GitHub Pages가 새 도메인으로 자동 리다이렉트)
> 적용일: 2026-11-13

---

## 1. 현재 구성

| 항목 | 값 |
|---|---|
| DNS | Cloudflare `baryon.ai` zone, `CNAME baryon-ax → baryonlabs.github.io` (**DNS only**) |
| 호스팅 | GitHub Pages, `main` 머지 시 `.github/workflows/pages.yml` 배포 |
| 커스텀 도메인 | 저장소 Settings → Pages → `baryon-ax.baryon.ai` |
| CNAME 파일 | `site/CNAME` |
| HTTPS | GitHub Pages 인증서 (Let's Encrypt) |

DNS 확인:

```bash
dig +short baryon-ax.baryon.ai
# baryonlabs.github.io.
# 185.199.108.153 ...
```

Pages 상태:

```bash
gh api repos/baryonlabs/baryon-ax/pages \
  --jq '{cname,https_enforced,status:.https_certificate.state}'
```

---

## 2. 배포 후 검증

```bash
python3 scripts/check_site_seo.py
python3 scripts/check_site_live.py
python3 scripts/submit_indexnow.py
```

`check_site_live.py`는 sitemap의 모든 페이지, canonical·OG, `llms.txt`, IndexNow 키 파일을 검사하고 DNS·소유확인 메타 상태를 함께 출력합니다.

---

## 3. 다른 도메인으로 다시 이전할 때

```bash
# 1) DNS: CNAME <sub> -> baryonlabs.github.io (DNS only)
# 2) URL 일괄 이전
python3 scripts/apply_site_domain.py \
  --domain <new-domain> \
  --old-base https://baryon-ax.baryon.ai/
python3 scripts/apply_site_domain.py \
  --domain <new-domain> \
  --old-base https://baryon-ax.baryon.ai/ --write
```

스크립트가 하는 일:

- `site/`·`scripts/`·`docs/`·`README.md`의 이전 base URL을 새 도메인으로 교체
- HTML의 절대 프로젝트 경로(`/baryon-ax/...`)를 루트 상대 경로로 변환
- `site/CNAME` 생성·갱신
- 남은 이전 URL 참조 수 확인

이후:

```bash
python3 scripts/check_site_seo.py
git checkout -b feat/custom-domain
git add -A && git commit -m "feat(site): move to <new-domain>"
git push -u origin feat/custom-domain
gh pr create --base main --head feat/custom-domain ...
```

머지·배포 후 GitHub Pages 도메인 갱신:

```bash
gh api -X PUT repos/baryonlabs/baryon-ax/pages -f cname=<new-domain>
gh api repos/baryonlabs/baryon-ax/pages --jq '.https_certificate.state'
gh api -X PUT repos/baryonlabs/baryon-ax/pages -F https_enforced=true
```

---

## 4. 검색엔진 재등록

도메인을 바꾸면 소유확인 토큰도 새로 넣어야 합니다.

| 엔진 | 작업 |
|---|---|
| Google Search Console | `sc-domain:baryon.ai` 도메인 속성이 있으면 하위 도메인 자동 포함. Sitemaps에 `https://baryon-ax.baryon.ai/sitemap.xml` 제출 |
| Naver Search Advisor | 새 URL 추가 → `set_site_verification.py --naver <token>` → 소유확인 |
| Bing Webmaster | 새 URL 추가 또는 GSC import → sitemap 제출 |
| Daum | 새 URL 등록 |

```bash
python3 scripts/set_site_verification.py --google "<token>" --naver "<token>" --bing "<token>"
python3 scripts/check_site_live.py
```

---

## 5. 롤백

1. GitHub Pages → Custom domain 제거 (`gh api -X PUT repos/baryonlabs/baryon-ax/pages -f cname=""`)
2. 이전 커밋 revert PR 생성
3. DNS 레코드는 남겨도 무해하지만, 원하면 삭제

커스텀 도메인을 제거하면 `https://baryonlabs.github.io/baryon-ax/`가 즉시 다시 동작합니다.

---

## 6. 체크리스트

- [x] Cloudflare CNAME `baryon-ax` → `baryonlabs.github.io` (DNS only)
- [x] `dig +short baryon-ax.baryon.ai` 확인
- [x] `apply_site_domain.py --write`
- [x] `check_site_seo.py` 0/0
- [x] PR 머지 + Pages 배포
- [x] GitHub Pages custom domain 설정
- [x] 인증서 approved + HTTPS 강제 (2026-11-13, 만료 2027-02-11)
- [x] `check_site_live.py` 0 failures
- [x] `submit_indexnow.py` 새 도메인 제출 (4개 URL, 200/200)
- [x] Search Console sitemap 제출 (4개 발견, 핵심 URL 색인)
- [x] Naver 소유확인 메타 배포 (2026-11-13)
- [x] Naver 콘솔 소유확인 + sitemap 제출 (2026-11-13)
- [x] Daum 검색등록 신청 (2026-11-13, 검토 대기)
- [ ] Bing Webmaster Tools 등록 (IndexNow는 완료)

---

## 7. 완료 상태 (2026-11-13)

| 항목 | 결과 |
|---|---|
| DNS | `baryon-ax.baryon.ai` → `baryonlabs.github.io` (DNS only) |
| TLS | Let's Encrypt 인증서 `approved`, 2027-02-11 만료 |
| HTTPS | `https_enforced: true` |
| GitHub Pages | `cname: baryon-ax.baryon.ai`, `build_type: workflow` |
| 이전 URL | `https://baryonlabs.github.io/baryon-ax/` → 새 도메인 301 |
| 로컬 SEO | `check_site_seo.py` failures 0, warnings 0 |
| 라이브 검증 | `check_site_live.py` failures 0, warnings 0 |
| IndexNow | 4개 URL, `api.indexnow.org` 200, `www.bing.com` 200 |
| Google 인증 | `baryon.ai` DNS TXT 확인 (도메인 속성) |
| Google 등록 | sitemap 4개 발견, 핵심 URL 색인 완료 |
| Naver 인증 | 소유확인 완료, sitemap 제출 완료 (2026-11-13 03:22) |
| Daum 등록 | 신청 완료, 검토 결과 이메일 대기 |
| Bing | IndexNow 4개 URL 200/200, BWT 대시보드는 보류 |

남은 일은 검색엔진 콘솔 로그인 후 sitemap 제출·소유확인뿐입니다. 절차는 [docs/promotion-playbook.md](promotion-playbook.md)의 14장을 참고하세요.
