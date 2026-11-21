# OIDC 인증 (Keycloak)

> 상태: API 토큰 검증 · 서비스 계정 위임 · 인증 감사 이벤트 · 웹 PKCE 로그인 구현
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
- `AUTH_MODE=oidc`: Bearer 토큰이 없거나 유효하지 않으면 **401**. 클라이언트가 보낸 신원 헤더는 **덮어씁니다**. 봇·MCP 클라이언트의 사용자 대리 호출은 [6절](#6-서비스-계정-위임봇mcp-클라이언트)을 따릅니다.
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
| 봇 서비스 계정 | `baryon-ax-bot` (confidential, client credentials, 개발용 시크릿 `baryon-bot-dev-secret`) |
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

## 6. 서비스 계정 위임(봇·MCP 클라이언트)

Slack·Discord 봇이나 MCP 클라이언트는 여러 채팅 사용자를 대신해 API를 호출합니다. 봇마다 사용자 토큰을 받을 수 없으므로, 봇은 **서비스 계정 토큰 하나**로 인증하고 요청마다 실제 사용자를 `X-On-Behalf-Of`로 알려 줍니다. 미들웨어는 이 요청을 그 사용자로 처리하되, 권한은 서비스 계정이 가진 범위를 넘지 못하게 막습니다.

```text
Slack 사용자 U123 ── Slack 봇 ──┐
                              ├─ Authorization: Bearer <서비스 계정 토큰>
                              ├─ X-On-Behalf-Of: slack:U123
                              └─ X-On-Behalf-Of-Roles: member   (선택)
                                    └─ OIDCAuthMiddleware
                                         ├─ 토큰 검증 (일반 사용자와 동일)
                                         ├─ OIDC_SERVICE_ROLE 보유 확인 → 없으면 403
                                         ├─ 사용자 ID 형식 검사 → 잘못되면 400
                                         ├─ 역할 = 요청 역할 ∩ 서비스 계정 역할 (상승 불가)
                                         └─ X-User-Id=slack:U123, 조직=토큰의 조직,
                                            X-Acting-Service=service-account-baryon-ax-bot
```

### 규칙

| 항목 | 동작 |
|---|---|
| 위임 가능 토큰 | 역할 클레임(`OIDC_ROLE_CLAIM`)에 `OIDC_SERVICE_ROLE`(기본 `baryon-service`)이 있는 토큰만. 없으면 **403** |
| 사용자 ID | `X-On-Behalf-Of` 값. 허용 문자 `[A-Za-z0-9:._@-]`, 최대 128자. 어긋나면 **400** |
| 접두어 제한 | `OIDC_DELEGATION_USER_PREFIXES=slack:,discord:` 를 설정하면 그 접두어로 시작하는 ID만 허용(그 외 403). 비우면 제한 없음 |
| 조직 | 항상 **서비스 계정 토큰의 조직**. 클라이언트가 보낸 `X-Organization-Id`는 무시 |
| 역할 | `X-On-Behalf-Of-Roles`(쉼표 구분, 생략 시 `member`)와 서비스 계정 역할의 **교집합**. 서비스 계정에 없는 역할은 버리고, 남는 역할이 없으면 **403** |
| 위조 방지 | 클라이언트가 보낸 `X-User-Id`·`X-Roles`·`X-Acting-Service`는 항상 덮어씀. `X-On-Behalf-Of` 없이 `X-On-Behalf-Of-Roles`만 보내면 **400** |
| 위임 없음 | 서비스 계정이 `X-On-Behalf-Of` 없이 호출하면 서비스 계정 자신으로 처리 |

예: 서비스 계정이 `baryon-service` + `baryon-member`만 가졌다면 `X-On-Behalf-Of-Roles: admin`은 403, `admin,member`는 `member`로 처리됩니다. 관리자 명령을 대신 실행해야 하는 운영 봇만 별도 서비스 계정에 `baryon-admin`을 추가하세요.

### Keycloak 설정 (client credentials)

개발 realm에는 `baryon-ax-bot` 클라이언트가 이미 들어 있습니다. 운영 realm에서는 다음처럼 만듭니다.

1. **Clients → Create client**: Client ID `baryon-slack-bot`(봇마다 하나), Client authentication **On**, Service accounts roles **On**, Standard flow·Direct access grants **Off**.
2. **Credentials** 탭에서 Client secret을 복사해 봇의 시크릿 저장소에 넣습니다.
3. **Groups**에 `baryon-service` 그룹을 만들고, **Clients → baryon-slack-bot → Service account roles → (서비스 계정 사용자) → Groups**에서 `baryon-service`와 `baryon-member`에 가입시킵니다.
4. 서비스 계정 사용자 **Attributes**에 `organization=<조직 ID>`를 넣습니다.
5. **Client scopes → Dedicated scope → Mappers**에 웹 클라이언트와 같은 `groups`(Group Membership, full path off), `organization`(User Attribute), `baryon-ax-api` audience 매퍼를 추가합니다.

토큰 발급과 호출:

```bash
BOT_TOKEN=$(curl -s -X POST http://localhost:8180/realms/baryon-ax/protocol/openid-connect/token \
  -d grant_type=client_credentials \
  -d client_id=baryon-ax-bot \
  -d client_secret=baryon-bot-dev-secret | jq -r .access_token)

curl -s http://localhost:8000/v1/auth/me \
  -H "Authorization: Bearer $BOT_TOKEN" \
  -H "X-On-Behalf-Of: slack:U123ABC" \
  -H "X-On-Behalf-Of-Roles: member" | jq
# {"user_id":"slack:U123ABC","organization_id":"local-org","roles":["member"],
#  "acting_service":"service-account-baryon-ax-bot","auth_mode":"oidc"}
```

봇은 토큰을 만료(`expires_in`) 직전까지 캐시하고 다시 발급받으면 됩니다. 시크릿은 봇 컨테이너에만 두고 사용자에게 노출하지 마세요.

### 봇이 보낼 헤더

| 헤더 | 필수 | 값 |
|---|---|---|
| `Authorization` | 예 | `Bearer <client_credentials 토큰>` |
| `X-On-Behalf-Of` | 예 | 채팅 사용자 ID. 플랫폼 접두어 권장: `slack:<user id>`, `discord:<user id>`, `mcp:<client user>` |
| `X-On-Behalf-Of-Roles` | 아니오 | 요청 역할(예: `member`). 생략하면 `member` |

`AUTH_MODE=oidc`에서는 `X-User-Id`·`X-Roles`를 보내도 무시됩니다.

### 감사 로그에 남는 내용

위임 요청에서 발생한 모든 감사 이벤트(`chat.completed`, 문서 업로드 등)에는 사용자와 서비스 계정이 함께 기록됩니다.

```json
{"type": "chat.completed", "user_id": "slack:U123ABC", "organization_id": "local-org",
 "acting_service": "service-account-baryon-ax-bot", "trace_id": "..."}
```

위임이 거부되면 `auth.failed` 이벤트에 `actor`(서비스 계정 또는 사용자), `status`(400/403), `reason`이 남습니다.

### dev 모드에서의 위임

`AUTH_MODE=dev`는 모든 헤더를 신뢰합니다. 운영과 같은 감사 형태를 로컬에서 확인할 수 있도록:

- `X-On-Behalf-Of`는 `X-User-Id`의 별칭이며 더 우선합니다(형식 검사는 동일하게 400).
- `X-On-Behalf-Of-Roles`가 있으면 `X-Roles` 대신 사용합니다. **역할 상한은 적용되지 않습니다.**
- 함께 보낸 `X-User-Id`(또는 `X-Acting-Service`)가 `acting_service`로 기록됩니다.

따라서 봇은 dev/oidc 모두 같은 헤더(`X-On-Behalf-Of`)를 보내고, oidc에서만 `Authorization`을 추가하면 됩니다.

---

## 7. 인증 감사 이벤트

| 이벤트 | 발생 시점 | 주요 필드 |
|---|---|---|
| `auth.login` | 웹이 로그인 콜백 직후 `POST /v1/auth/login` 호출 | `user_id`, `roles`, `auth_mode` |
| `auth.logout` | 웹 로그아웃 버튼 → `POST /v1/auth/logout` | `user_id`, `auth_mode` |
| `auth.failed` | 미들웨어의 401(토큰 없음·무효)·400/403(위임 거부) | `reason`, `status`, `path`, `method`, `actor`(있으면), `suppressed` |

- `auth.failed`에는 **토큰을 절대 기록하지 않습니다.** 사유 문자열만 200자까지 남깁니다.
- 공격성 요청으로 로그가 폭증하지 않도록 프로세스당 분당 `AUTH_FAILED_AUDIT_PER_MINUTE`(기본 60)건까지만 저장합니다. 초과분은 개수만 세어 다음 이벤트의 `suppressed`에 붙입니다(메모리 사용량 고정).
- 위임 거부(400/403)는 토큰의 조직으로, 토큰 자체가 무효인 401은 `OIDC_DEFAULT_ORG`로 기록됩니다. 각 조직 관리자가 `/v1/audit-events`에서 확인합니다.
- `GET /v1/auth/me`는 API가 실제로 인식한 신원(위임 후)을 돌려줍니다. 봇 설정 점검에 사용하세요.

---

## 8. 웹 로그인 (Authorization Code + PKCE)

웹은 **빌드 시** `VITE_AUTH_MODE=oidc`일 때만 OIDC 로그인을 켭니다. 기본값(`dev`)에서는 기존 역할 전환기가 그대로 동작합니다.

```env
VITE_AUTH_MODE=oidc
VITE_OIDC_ISSUER=http://localhost:8180/realms/baryon-ax
VITE_OIDC_CLIENT_ID=baryon-ax-web
# 선택: VITE_OIDC_SCOPE (기본 "openid profile email"), VITE_OIDC_REDIRECT_URI (기본 현재 origin + "/")
```

```bash
# 로컬 개발 서버
cd apps/web && VITE_AUTH_MODE=oidc VITE_OIDC_ISSUER=http://localhost:8180/realms/baryon-ax VITE_OIDC_CLIENT_ID=baryon-ax-web npm run dev
# Docker Compose: .env의 VITE_* 값이 web 이미지 빌드 인자로 전달됩니다
docker compose build web
```

- 첫 접속 시 IdP 로그인으로 이동하고, 돌아오면 코드를 토큰으로 교환한 뒤 `POST /v1/auth/login`(감사 `auth.login`)을 호출합니다.
- 모든 API 호출에 `Authorization: Bearer`를 붙입니다. 만료 30초 전이면 리프레시 토큰으로 갱신하고, 갱신 실패나 API **401**이면 다시 로그인합니다.
- 헤더에 로그인 사용자가 표시되고, 역할은 API가 토큰에서 계산한 값을 씁니다(역할 전환기 숨김). 로그아웃 버튼은 `auth.logout`을 기록한 뒤 IdP 세션을 종료합니다.
- 토큰은 탭 단위 `sessionStorage`에 둡니다. XSS에 노출되지 않도록 CSP와 의존성 관리를 유지하세요.
- IdP 클라이언트의 Redirect URI·Web origin·Post logout redirect URI에 웹 주소를 등록해야 합니다(개발 realm은 `localhost:3001`, `localhost:5173` 등록됨).

---

## 9. 운영 체크리스트

- [ ] `AUTH_MODE=oidc` + `https` issuer
- [ ] 웹 이미지를 `VITE_AUTH_MODE=oidc`로 빌드
- [ ] 봇·MCP 클라이언트마다 별도 서비스 계정, 최소 역할(`baryon-service` + `baryon-member`)
- [ ] `OIDC_DELEGATION_USER_PREFIXES`로 봇이 대신할 수 있는 ID 범위 제한
- [ ] 개발용 `baryon-ax-bot` 시크릿 교체 또는 클라이언트 삭제
- [ ] `OIDC_ALLOW_INSECURE_ISSUER` 제거
- [ ] Keycloak `start-dev` 대신 `start` + TLS + 외부 DB
- [ ] 관리자 콘솔을 공개 인터넷에 노출하지 않음
- [ ] 개발용 계정(`admin`/`member`/`legal`) 삭제
- [ ] `KC_BOOTSTRAP_ADMIN_*` 대신 시크릿 매니저 사용
- [ ] 액세스 토큰 수명(기본 15분)과 리프레시 정책 결정
- [ ] Keycloak DB·realm 설정 백업
- [ ] `auth.failed` 급증 알림(감사 로그 모니터링) 설정

---

## 10. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `401 missing bearer token` | Authorization 헤더 없음 | `Bearer <token>` 추가 |
| `401 invalid token` | 서명·만료·issuer·audience 불일치 | 토큰 `iss`/`aud`와 환경변수 비교 |
| `OIDC discovery failed` | API에서 issuer 접근 불가 | `OIDC_JWKS_URL` 직접 지정, 네트워크 확인 |
| `only RS256 tokens are accepted` | 다른 알고리즘 토큰 | Keycloak 클라이언트 서명 알고리즘을 RS256으로 |
| 관리자 API 403 | 그룹 매핑 실패 | 토큰의 `groups` 클레임과 `OIDC_ADMIN_ROLE` 확인 |
| 웹 UI가 계속 로그인 화면으로 돌아감 | 웹을 `VITE_AUTH_MODE=oidc`로 빌드했지만 API가 dev, 또는 audience 불일치 | API `AUTH_MODE=oidc`, 토큰 `aud`에 `baryon-ax-api` 포함 확인 |
| 로그인 후 `state mismatch` | 다른 탭에서 로그인 시작, 또는 sessionStorage 차단 | 같은 탭에서 다시 시도 |
| `403 Delegation rejected: ... missing service role` | 봇 토큰에 `baryon-service` 없음 | 서비스 계정을 `OIDC_SERVICE_ROLE` 그룹에 추가 |
| `403 ... requested roles exceed` | 서비스 계정에 없는 역할 요청 | `X-On-Behalf-Of-Roles`를 줄이거나 전용 서비스 계정 사용 |
| `400 malformed X-On-Behalf-Of` | 공백·`/`·`;` 등 허용 외 문자, 128자 초과 | `slack:U123` 형태로 보냄 |

---

## 11. 아직 구현되지 않은 것

- Keycloak realm 관리 자동화(운영 계정·그룹 프로비저닝)
- SCIM 프로비저닝
- 서비스 계정별 위임 접두어 매핑(현재는 전역 `OIDC_DELEGATION_USER_PREFIXES`)

이 항목은 #38 후속 PR 또는 별도 이슈로 진행합니다.
