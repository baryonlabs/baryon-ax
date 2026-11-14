# OIDC 인증 (Keycloak)

> 상태: API 토큰 검증 구현 완료 · 웹 PKCE 로그인은 후속 작업
> 관련 이슈: #38
> 기본 모드: `AUTH_MODE=dev` (기존 개발 헤더). 프로덕션에서는 `AUTH_MODE=oidc`.

Baryon AX는 특정 IdP에 종속되지 않는 **OIDC Bearer 토큰 검증**을 사용합니다. Keycloak을 기본 레퍼런스로 제공하지만, Entra ID·Okta·ADFS·사내 OIDC도 같은 설정으로 연결할 수 있습니다.

---

## 1. 동작 방식

```text
Browser / API client
  └─ Authorization: Bearer <access_token>
        └─ OIDCAuthMiddleware
             ├─ JWKS로 RS256 서명 검증
             ├─ iss / aud / exp / nbf 검증
             ├─ groups → member/admin/legal 매핑
             └─ 검증된 X-User-Id / X-Organization-Id / X-Roles 주입
                   └─ 기존 엔드포인트
```

- `AUTH_MODE=dev`: 클라이언트가 보낸 `X-User-Id`·`X-Roles`를 그대로 사용합니다. 로컬 데모·테스트 전용입니다.
- `AUTH_MODE=oidc`: Bearer 토큰이 없거나 유효하지 않으면 **401**. 클라이언트가 보낸 신원 헤더는 **덮어씁니다**.
- 공개 경로: `/health`, `/docs*`, `/redoc*`, `/openapi.json`, `/v1/open-source`.

---

## 2. Keycloak 개발 프로필

```bash
docker compose -f docker-compose.auth.yml up -d
```

| 항목 | 값 |
|---|---|
| Keycloak | http://localhost:8180 |
| 관리자 콘솔 | http://localhost:8180/admin (기본 `admin` / `admin`) |
| Realm | `baryon-ax` |
| 웹 클라이언트 | `baryon-ax-web` (public, PKCE, direct grant 허용) |
| API audience | `baryon-ax-api` |

개발용 계정:

| 사용자 | 비밀번호 | 그룹 | Baryon 역할 |
|---|---|---|---|
| `admin` | `baryon-admin` | `/baryon-admin` | `admin` |
| `member` | `baryon-member` | `/baryon-member` | `member` |
| `legal` | `baryon-legal` | `/baryon-legal` | `legal` |

> 이 계정은 **로컬 개발 전용**입니다. 운영에서는 삭제하고 고객 IdP 또는 별도 계정을 사용하세요.

토큰 발급 확인:

```bash
curl -s -X POST \
  http://localhost:8180/realms/baryon-ax/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d grant_type=password \
  -d client_id=baryon-ax-web \
  -d username=admin \
  -d password=baryon-admin \
  -d scope=openid | jq -r .access_token
```

---

## 3. API 실행

로컬에서 API만 띄우는 경우:

```bash
cd apps/api
AUTH_MODE=oidc \
OIDC_ISSUER=http://localhost:8180/realms/baryon-ax \
OIDC_AUDIENCE=baryon-ax-api \
OIDC_ALLOW_INSECURE_ISSUER=true \
uv run uvicorn baryon.main:app --port 8000
```

Docker Compose에서 API 컨테이너가 Keycloak에 접근하는 경우:

```env
AUTH_MODE=oidc
OIDC_ISSUER=http://localhost:8180/realms/baryon-ax
OIDC_AUDIENCE=baryon-ax-api
OIDC_JWKS_URL=http://keycloak:8080/realms/baryon-ax/protocol/openid-connect/certs
OIDC_ALLOW_INSECURE_ISSUER=true
```

- `OIDC_ISSUER`는 **토큰의 `iss`와 정확히 일치**해야 합니다. 브라우저가 보는 주소(`localhost:8180`)를 기준으로 설정하세요.
- API 컨테이너는 내부 주소(`keycloak:8080`)로 JWKS를 가져오므로 `OIDC_JWKS_URL`을 따로 지정합니다.
- 운영에서는 `https` issuer를 사용하고 `OIDC_ALLOW_INSECURE_ISSUER`를 제거하세요.

호출:

```bash
TOKEN=$(curl -s -X POST http://localhost:8180/realms/baryon-ax/protocol/openid-connect/token \
  -d grant_type=password -d client_id=baryon-ax-web \
  -d username=admin -d password=baryon-admin -d scope=openid | jq -r .access_token)

curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/admin/settings | jq '{version, auth, organization_id}'
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/admin/gaps | jq '.items | length'
```

`member` 토큰으로 `/v1/admin/*`를 호출하면 403이어야 합니다.

---

## 4. 클레임 계약

| Baryon | 기본 소스 | 비고 |
|---|---|---|
| 사용자 ID | `preferred_username` → `email` → `sub` | `OIDC_USER_CLAIM` |
| 조직 | `organization` 클레임 | 없으면 `OIDC_DEFAULT_ORG` |
| 역할 | `groups` 클레임 | `OIDC_ROLE_CLAIM` |
| 관리자 | `baryon-admin` 또는 `admin` | `OIDC_ADMIN_ROLE` |
| 멤버 | `baryon-member` 또는 `member` | `OIDC_MEMBER_ROLE` |
| 법무 | `baryon-legal` 또는 `legal` | `OIDC_LEGAL_ROLE` |

Keycloak 그룹 이름을 바꾸려면 realm의 그룹과 `OIDC_*_ROLE` 값을 함께 바꿉니다.

---

## 5. Entra ID / 사내 OIDC

동일한 어댑터를 사용합니다.

```env
AUTH_MODE=oidc
OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
OIDC_AUDIENCE=<application-client-id>
OIDC_ROLE_CLAIM=roles
OIDC_ORG_CLAIM=organization
OIDC_ADMIN_ROLE=Baryon.Admin
OIDC_MEMBER_ROLE=Baryon.Member
OIDC_LEGAL_ROLE=Baryon.Legal
```

- Entra ID는 **앱 역할(app roles)** 을 `roles` 클레임으로 내보내는 구성을 권장합니다. 그룹 클레임은 대규모 조직에서 overage가 발생할 수 있습니다.
- 사내 OIDC는 `groups` 또는 `roles` 클레임 이름만 맞추면 됩니다.
- 폐쇄망에서는 Keycloak이 고객의 AD/LDAP/Entra를 브로커링하도록 구성할 수 있습니다.

---

## 6. 운영 체크리스트

- [ ] `AUTH_MODE=oidc` + `https` issuer
- [ ] `OIDC_ALLOW_INSECURE_ISSUER` 제거
- [ ] Keycloak `start-dev` 대신 `start` + TLS + 외부 DB
- [ ] 관리자 콘솔을 공개 인터넷에 노출하지 않음
- [ ] 개발용 계정(`admin`/`member`/`legal`) 삭제
- [ ] `KC_BOOTSTRAP_ADMIN_*` 대신 시크릿 매니저 사용
- [ ] 액세스 토큰 수명(기본 15분)과 리프레시 정책 결정
- [ ] Keycloak DB·realm 설정 백업
- [ ] `docs/security.md`의 OIDC 항목 갱신

---

## 7. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `401 missing bearer token` | Authorization 헤더 없음 | `Bearer <token>` 추가 |
| `401 invalid token` | 서명·만료·issuer·audience 불일치 | 토큰 `iss`/`aud`와 환경변수 비교 |
| `OIDC discovery failed` | API에서 issuer 접근 불가 | `OIDC_JWKS_URL` 직접 지정, 네트워크 확인 |
| `only RS256 tokens are accepted` | 다른 알고리즘 토큰 | Keycloak 클라이언트 서명 알고리즘을 RS256으로 |
| 관리자 API 403 | 그룹 매핑 실패 | 토큰의 `groups` 클레임과 `OIDC_ADMIN_ROLE` 확인 |
| 웹 UI가 401 | PKCE 로그인 미구현 | 현재는 API/curl로 검증. 웹 로그인은 후속 작업 |

---

## 8. 아직 구현되지 않은 것

- 웹 PKCE 로그인·콜백·리프레시 토큰
- 로그인/로그아웃 감사 이벤트
- Keycloak realm 관리 자동화(운영 계정·그룹 프로비저닝)
- SCIM 프로비저닝

이 항목은 #38 후속 PR 또는 별도 이슈로 진행합니다.
