# 커스텀 도메인 이전 절차 (baryon-ax.baryon.ai)

> 권장 도메인: `https://baryon-ax.baryon.ai/`
> 현재: `https://baryonlabs.github.io/baryon-ax/` (GitHub Pages 프로젝트 사이트)
> 목적: `github.io` 공용 호스트 제약을 없애고, Search Console·Naver 등록과 IndexNow 키 루트 호스팅을 정상화한다.

---

## 0. 왜 이 도메인인가

- `baryon.ai`는 이미 Cloudflare DNS(`sara.ns.cloudflare.com`, `terin.ns.cloudflare.com`)로 운영 중입니다.
- Search Console에 `sc-domain:baryon.ai` **도메인 속성**이 인증되어 있다면, 하위 도메인은 별도 인증 없이 자동 포함됩니다.
- 저장소 이름과 일치해 링크·문서에서 혼동이 없습니다.
- Naver는 공용 호스트(`github.io`) 등록을 제한할 수 있어 커스텀 도메인이 유리합니다.

다른 이름을 쓰려면 아래 명령의 `--domain`만 바꾸면 됩니다.

---

## 1. DNS 레코드 추가 (사람 1회)

Cloudflare → `baryon.ai` → DNS → **Add record**

| 항목 | 값 |
|---|---|
| Type | `CNAME` |
| Name | `baryon-ax` |
| Target | `baryonlabs.github.io` |
| Proxy status | **DNS only (회색 구름)** |
| TTL | Auto |

프록시(주황 구름)를 켜면 GitHub의 인증서 발급이 실패할 수 있습니다. GitHub Pages가 자체 인증서를 발급하므로 DNS only가 맞습니다.

터미널에서 확인:

```bash
dig +short baryon-ax.baryon.ai
# baryonlabs.github.io. 가 나와야 함
```

### 토큰으로 자동 생성 (선택)

`Zone.DNS:Edit` 권한이 있는 Cloudflare API 토큰이 있으면:

```bash
export CLOUDFLARE_API_TOKEN=...
python3 scripts/set_site_dns.py --dry-run
python3 scripts/set_site_dns.py --apply
```

---

## 2. URL 일괄 이전

```bash
# 1) 변경 대상 확인 (파일만 출력, 쓰기 없음)
python3 scripts/apply_site_domain.py --domain baryon-ax.baryon.ai

# 2) 적용
python3 scripts/apply_site_domain.py --domain baryon-ax.baryon.ai --write
```

스크립트가 하는 일:

- `site/`·`scripts/`·`docs/`·`README.md`의 `https://baryonlabs.github.io/baryon-ax/` → `https://baryon-ax.baryon.ai/`
- `site/404.html`의 `"/baryon-ax/...` 절대 경로 → `"/...`
- `site/CNAME` 생성 (`baryon-ax.baryon.ai`)
- 남은 이전 URL 참조 수 확인

적용 후:

```bash
python3 scripts/check_site_seo.py   # failures 0, warnings 0
```

---

## 3. PR · 배포

```bash
git checkout -b feat/custom-domain
git add -A
git commit -m "feat(site): move to baryon-ax.baryon.ai"
git push -u origin feat/custom-domain
gh pr create --base main --head feat/custom-domain ...
```

CI·`vulnerability sweep` 통과 후 squash merge → Pages 워크플로 완료 대기.

> **순서 주의**: DNS가 먼저 준비되어야 합니다. DNS 없이 URL만 바꾸면 canonical이 죽은 도메인을 가리킵니다.

---

## 4. GitHub Pages 커스텀 도메인 활성화

머지 직후 실행합니다.

```bash
gh api -X PUT repos/baryonlabs/baryon-ax/pages -f cname=baryon-ax.baryon.ai
gh api repos/baryonlabs/baryon-ax/pages --jq '{cname,https_enforced,status:.https_certificate.state}'
```

또는 저장소 → Settings → Pages → Custom domain에 `baryon-ax.baryon.ai` 입력.

인증서 발급 완료(`https_certificate.state == "approved"`) 후 HTTPS 강제:

```bash
gh api -X PUT repos/baryonlabs/baryon-ax/pages -F https_enforced=true
```

확인:

```bash
curl -I https://baryon-ax.baryon.ai/
# HTTP/2 200
```

GitHub Pages는 이전 `https://baryonlabs.github.io/baryon-ax/` URL을 새 도메인으로 자동 리다이렉트합니다.

---

## 5. 이전 후 검증

```bash
python3 scripts/check_site_seo.py
python3 scripts/check_site_live.py --base https://baryon-ax.baryon.ai/
python3 scripts/submit_indexnow.py --base https://baryon-ax.baryon.ai/
```

IndexNow 키 파일은 새 루트에서 `https://baryon-ax.baryon.ai/<key>.txt`로 접근됩니다.

---

## 6. 검색엔진 재등록

| 엔진 | 작업 |
|---|---|
| Google Search Console | `sc-domain:baryon.ai` 도메인 속성이 있으면 하위 도메인 자동 포함. Sitemaps에 `https://baryon-ax.baryon.ai/sitemap.xml` 제출 |
| Naver Search Advisor | 새 URL로 사이트 추가 → `set_site_verification.py --naver <token>` → 소유확인 |
| Bing Webmaster | 새 URL 추가 또는 GSC import → sitemap 제출 |
| Daum | 새 URL 등록 |

도메인 이전 **후에** 토큰을 주입해야 합니다. 이전 전 `github.io` 속성에 넣은 토큰은 새 도메인에 적용되지 않습니다.

```bash
python3 scripts/set_site_verification.py --google "<token>" --naver "<token>" --bing "<token>"
python3 scripts/check_site_live.py --base https://baryon-ax.baryon.ai/
```

---

## 7. 롤백

문제가 생기면:

1. GitHub Pages → Custom domain 제거 (`gh api -X PUT ... -f cname=""`)
2. 이전 커밋으로 revert PR 생성
3. DNS 레코드는 남겨도 무해하지만, 원하면 삭제

`https://baryonlabs.github.io/baryon-ax/`는 커스텀 도메인을 제거하면 즉시 다시 동작합니다.

---

## 8. 체크리스트

- [ ] Cloudflare CNAME `baryon-ax` → `baryonlabs.github.io` (DNS only)
- [ ] `dig +short baryon-ax.baryon.ai` 확인
- [ ] `apply_site_domain.py --domain ... --write`
- [ ] `check_site_seo.py` 0/0
- [ ] PR 머지 + Pages 배포
- [ ] GitHub Pages custom domain 설정
- [ ] 인증서 approved + HTTPS 강제
- [ ] `check_site_live.py --base https://baryon-ax.baryon.ai/` 0 failures
- [ ] `submit_indexnow.py` 새 도메인 제출
- [ ] Search Console sitemap 제출
- [ ] Naver/Bing/Daum 새 URL 등록
- [ ] README·릴리스 노트의 링크 갱신 (스크립트가 자동 처리)
