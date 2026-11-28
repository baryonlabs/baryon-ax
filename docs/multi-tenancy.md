# 멀티테넌시 (pooled · silo)

> 상태: 테넌트 레지스트리 · 플랫폼 관리자 API · 테넌트 게이트 · 쿼터·사용량 계량 · 오프보딩 삭제(purge) 구현
> 기본 모드: `TENANCY_MODE=single` (지금까지와 동일, 레지스트리 불필요). 공유 인스턴스는 `TENANCY_MODE=pooled`.
> 관련 문서: [auth-oidc.md](auth-oidc.md) (조직 클레임, 역할 매핑)

Baryon AX는 같은 코드로 두 가지 방식의 고객 배포를 지원합니다.

| 방식 | 구성 | 언제 쓰나 |
|---|---|---|
| **pooled** (공유) | API 1개, 고객 조직마다 테넌트 1개. 저장소·벡터 DB·모델 게이트웨이를 함께 씀 | 표준 SaaS 플랜, 빠른 온보딩, 낮은 운영비 |
| **silo** (전용) | 고객마다 스택 1벌 (`TENANCY_MODE=single`) | 망 분리, 전용 모델, 데이터 위치 요건, 계약상 물리 분리 |

silo는 지금까지의 단일 조직 배포와 같습니다. 이 문서는 주로 pooled 운영을 다룹니다.

---

## 1. 동작 방식

```text
Browser / Bot / MCP client
  └─ Authorization: Bearer <access_token>
        └─ OIDCAuthMiddleware        서명·iss·aud 검증, organization 클레임 → X-Organization-Id
             └─ TenantGateMiddleware 활성 테넌트인지 확인 (pooled 전용)
                   ├─ 미등록 조직      → 403 tenant_not_registered
                   ├─ suspended/deleting → 403 tenant_suspended
                   └─ 기존 엔드포인트 (모든 저장소가 organization_id로 범위 제한)
```

- **테넌트 = 조직 ID**입니다. 토큰의 조직 클레임 값이 레지스트리의 테넌트 `id`와 같아야 합니다.
- 게이트 예외: 공개 경로(`OIDC_PUBLIC_PATHS`: `/health`, `/docs*`, `/openapi.json`, `/v1/open-source`, `/v1/signup` 등)와 `/v1/platform/*`(플랫폼 관리자 자체 권한 검사). `OIDC_PUBLIC_PATHS`를 직접 지정하면 `/v1/signup`을 반드시 포함하세요.
- 오류 응답은 기계가 읽을 수 있는 형식입니다.

```json
{"detail": {"code": "tenant_suspended", "message": "Tenant is suspended", "tenant_id": "acme", "status": "suspended"}}
```

---

## 2. 설정

```env
TENANCY_MODE=pooled
TENANT_STORE=/data/tenants          # tenants.json, usage.json, memory_users.json, invites.json
USAGE_FLUSH_SECONDS=5               # 사용량 카운터 디스크 반영 주기(초)
OIDC_PLATFORM_ADMIN_ROLE=baryon-platform-admin
TENANT_SHARED_GATEWAY_ALLOWED=false # 공유 MCP 게이트웨이 도구 실행 허용 여부
AUTH_MODE=oidc                      # pooled에서는 반드시 oidc

# 초대 코드 가입 (docs/multi-tenancy.md 6절)
APP_BASE_URL=https://ax.example.com # 초대 메일 링크의 기준 주소
SIGNUP_RATE_LIMIT_PER_HOUR=20        # IP당 시간당 가입 시도
SIGNUP_CODE_RATE_LIMIT_PER_HOUR=10   # 코드당 시간당 가입 시도
INVITE_CLAIM_TIMEOUT_SECONDS=900     # 처리 중인 코드의 재시도 대기
TRUST_PROXY_HEADERS=false            # 신뢰하는 프록시 뒤에서만 true (X-Forwarded-For)
SMTP_HOST=                           # 비우면 개발 모드: 링크를 로그에만 남김
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
SMTP_FROM=no-reply@baryon.local
SMTP_STARTTLS=true
SMTP_SSL=false
IDP_MODE=auto                        # auto | stub | keycloak
KEYCLOAK_URL=https://id.example.com
KEYCLOAK_REALM=baryon-ax
KEYCLOAK_ADMIN_CLIENT_ID=baryon-ax-admin
KEYCLOAK_ADMIN_CLIENT_SECRET=...
KEYCLOAK_ADMIN_GROUP=baryon-admin
```

- `AUTH_MODE=dev`와 `pooled`를 함께 쓰면 `X-Organization-Id` 헤더를 그대로 믿습니다. 시작 시 경고를 남기며 **테스트 전용**입니다.
- `TENANCY_MODE=pooled`에서는 조직 클레임이 없는 토큰을 `OIDC_DEFAULT_ORG`로 보내지 않고 **403 `organization_claim_missing`**으로 거절합니다. 서로 다른 고객 사용자가 기본 조직 하나로 섞이는 것을 막기 위해서입니다.
- 레지스트리와 카운터 파일은 임시 파일에 쓴 뒤 `os.replace`로 교체합니다(원자적 쓰기). `TENANT_STORE`는 영구 볼륨에 두세요.

---

## 3. 테넌트와 조직 클레임 매핑

### Keycloak Organizations (권장, Keycloak 26+)

1. Realm 설정 → **Organizations** 활성화.
2. 고객마다 Organization을 만들고 **alias를 테넌트 ID와 같게** 지정합니다(예: `acme`). 테넌트 ID 규칙은 `[a-z0-9-]{2,40}`입니다.
3. `baryon-ax-web` 클라이언트의 Client scopes에 `organization` 스코프를 추가합니다. 토큰에 `"organization": ["acme"]`가 들어갑니다.
4. "Add organization attributes"를 켜면 `{"acme": {...}}` 형태의 맵이 오는데, API는 맵의 키를 조직 ID로 읽습니다.

### 그룹·속성 매퍼 (Keycloak 25 이하, 다른 IdP)

사용자 속성 `organization`을 두고 **User Attribute 매퍼**로 액세스 토큰의 `organization` 클레임에 넣습니다. 클레임 이름이 다르면 `OIDC_ORG_CLAIM`을 바꿉니다(점 표기 지원: `tenant.id`).

### 여러 조직에 속한 사용자

토큰에 조직이 여러 개면 첫 번째 조직을 씁니다. 클라이언트는 `X-Organization-Id`로 다른 조직을 고를 수 있지만, **토큰에 있는 조직만** 선택됩니다. 목록에 없는 값은 무시됩니다.

---

## 4. 역할: 플랫폼 관리자 vs 테넌트 관리자

| 역할 | IdP 그룹 (기본) | 할 수 있는 일 |
|---|---|---|
| `platform-admin` | `baryon-platform-admin` (`OIDC_PLATFORM_ADMIN_ROLE`) | `/v1/platform/*`: 테넌트 생성·수정·정지·삭제, 사용량·감사 조회 |
| `admin` | `baryon-admin` | **자기 테넌트 안에서만** 문서 ACL, 지식 공백, 온톨로지 팩, 감사 로그, 설정 |
| `member`, `legal` | `baryon-member`, `baryon-legal` | 자기 테넌트 문서 검색·대화 |

- 테넌트 `admin`은 `/v1/platform/*`에서 **403**입니다.
- 플랫폼 관리자도 테넌트 데이터 엔드포인트(`/v1/documents` 등)는 **자기 조직이 등록된 테넌트일 때만** 쓸 수 있습니다. 운영 조직(예: `platform-ops`)은 보통 등록하지 않으므로 고객 문서에 접근하지 못합니다.
- 서비스 계정 위임(`X-On-Behalf-Of`)으로는 `platform-admin`을 넘길 수 없습니다.
- OIDC 모드에서는 `OIDC_PLATFORM_ADMIN_ROLE`에 지정한 IdP 역할만 플랫폼 관리자로 인정합니다(`platform-admin`이라는 이름 그대로는 인정하지 않음).
- **중요**: 이 역할은 운영자만 부여할 수 있어야 합니다. 고객 관리자가 공유 realm의 그룹을 직접 관리하게 하면 스스로 플랫폼 관리자가 될 수 있습니다. 고객에게는 자기 Organization의 멤버 관리 권한만 주고, 플랫폼 관리자 역할은 realm 역할(또는 운영자 전용 클라이언트 역할)로 두어 운영자 계정에만 할당하세요.
- dev 모드에서는 `X-Roles: platform-admin` 헤더로 시험합니다.

---

## 5. 플랫폼 API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/v1/platform/tenants` | 생성 (201). 중복 409, 형식 오류 422 |
| `GET` | `/v1/platform/tenants?status=` | 목록 |
| `GET` | `/v1/platform/tenants/{id}` | 조회 |
| `PATCH` | `/v1/platform/tenants/{id}` | `name`·`plan`·`quotas`(보낸 키만 변경, `null`=무제한)·`status`(active/suspended)·`notes` |
| `POST` | `/v1/platform/tenants/{id}/suspend` | 정지 (모든 요청 403) |
| `POST` | `/v1/platform/tenants/{id}/resume` | 재개 |
| `DELETE` | `/v1/platform/tenants/{id}` | 오프보딩: `deleting`으로 막고 모든 저장소에서 데이터 삭제 |
| `GET` | `/v1/platform/tenants/{id}/usage` | 문서 수·저장 용량·일별/월별 사용량 |
| `GET` | `/v1/platform/tenants/{id}/audit-events` | 테넌트 감사 로그 + 해당 테넌트에 대한 플랫폼 조치 |
| `POST` | `/v1/platform/invites` | 초대 코드 발급 (201). 응답의 `code`는 **이때 한 번만** |
| `GET` | `/v1/platform/invites?status=` | 초대 목록 (코드·해시 없음) |
| `DELETE` | `/v1/platform/invites/{id}` | 회수. 이미 사용된 코드는 409 |
| `POST` | `/v1/signup` | **공개** 가입: 코드·이메일·조직명 → 테넌트 생성 + 첫 관리자 초대 |

테넌트 레코드:

```json
{
  "id": "acme", "name": "Acme Corp", "status": "active", "plan": "standard",
  "quotas": {"max_documents": 5000, "max_storage_bytes": 10737418240,
             "chat_requests_per_day": 2000, "tokens_per_month": 20000000},
  "created_at": "...", "updated_at": "...", "notes": ""
}
```

감사 이벤트는 **행위자(플랫폼 관리자)의 조직**으로 기록되고 `tenant_id`를 담습니다: `tenant.created`, `tenant.updated`, `tenant.suspended`, `tenant.resumed`, `tenant.deleted`, `tenant.purged`, `tenant.audit_viewed`. 테넌트 관리자가 보는 `/v1/audit-events`는 지금처럼 자기 조직 이벤트만 돌려줍니다.

간단한 CLI도 있습니다(표준 라이브러리만 사용).

```bash
export BARYON_API_URL=https://ax.example.com
export BARYON_TOKEN=<platform-admin access token>
python scripts/tenant_admin.py create acme "Acme Corp" --plan standard --max-documents 5000 --chat-per-day 2000
python scripts/tenant_admin.py list
python scripts/tenant_admin.py usage acme
python scripts/tenant_admin.py suspend acme
python scripts/tenant_admin.py delete acme --yes
```

---

## 6. 온보딩 절차

두 가지 경로가 있습니다. 초대 코드 가입은 pooled 배포의 기본 온보딩이고, 수동 생성은 초대 없이 운영자가 직접 준비할 때 씁니다.

### 6.1 초대 코드 가입 (권장)

1. **코드 발급** (플랫폼 관리자): 조직 ID·첫 관리자 이메일·만료(기본 7일)를 정해 발급합니다. 코드 원문은 응답에서 한 번만 보입니다.

   ```bash
   python scripts/invite_admin.py create --org acme --email admin@acme.example --expires 7d
   # 또는
   curl -X POST https://ax.example.com/v1/platform/invites \
     -H "Authorization: Bearer $PLATFORM_TOKEN" -H "Content-Type: application/json" \
     -d '{"organization_id":"acme","email":"admin@acme.example","expires_days":7}'
   ```

2. **가입 화면**: 초대받은 사람이 `/signup`에서 코드·이메일·조직명을 넣고 제출합니다. 조직 ID는 코드에 묶여 있으므로 사용자가 바꿀 수 없습니다.

3. **자동 처리** (`POST /v1/signup`):
   - 코드 검증(1회용·만료·회수 확인, 상수 시간 비교) → 테넌트 생성(`/v1/platform/tenants`와 같은 경로·기본 plan/쿼터)
   - IdP 어댑터로 Organization(`alias`=테넌트 ID) 준비 + 첫 관리자 초대
   - 감사 이벤트 `tenant.created`, `invite.redeemed` (코드 원문 없음)
   - 초대 메일 발송: "조직 X가 만들어졌습니다. 여기서 로그인하세요" (한국어+영어)
   - IdP 준비가 실패하면 테넌트를 되돌리고(보상 트랜잭션) 코드를 다시 쓸 수 있게 풉니다.

4. **로그인**: 메일의 링크(`APP_BASE_URL/?organization=<id>`)를 열어 첫 관리자로 로그인합니다. 확인은 `GET /v1/auth/me`의 `organization_id`와 `/v1/admin/settings`의 `tenant.status=active`입니다.

5. **회수·조회**:

   ```bash
   python scripts/invite_admin.py list --status pending
   python scripts/invite_admin.py revoke <invite-id>
   ```

**개발 모드**: `AUTH_MODE=dev`이거나 Keycloak 설정이 없으면 IdP는 **stub**입니다(레지스트리에 테넌트만 생성). `SMTP_HOST`가 비어 있으면 메일 대신 서버 로그에 로그인 링크를 남기고 가입은 성공 처리합니다. 같은 코드·이메일로 다시 제출하면 테넌트를 새로 만들지 않고 메일만 다시 보냅니다(멱등).

**IdP 어댑터 설정**: `IDP_MODE=auto`는 `AUTH_MODE=oidc`이고 아래 값이 모두 있을 때만 Keycloak을 씁니다. 서비스 계정에는 `manage-users`와 `manage-realm`(Organizations) 권한이 필요합니다. Keycloak 어댑터는 사용자를 찾거나 만들고, Organization 멤버십과 `KEYCLOAK_ADMIN_GROUP`(기본 `baryon-admin`)을 부여한 뒤 비밀번호 설정·이메일 인증 메일을 보냅니다.

```env
IDP_MODE=keycloak
KEYCLOAK_URL=https://id.example.com
KEYCLOAK_REALM=baryon-ax
KEYCLOAK_ADMIN_CLIENT_ID=baryon-ax-admin
KEYCLOAK_ADMIN_CLIENT_SECRET=...
KEYCLOAK_ADMIN_GROUP=baryon-admin
```

> 공유 realm에서는 `baryon-admin`이 realm 전역 역할입니다. 한 사용자가 여러 Organization에 속하면 그중 어느 조직에서든 관리자 권한을 갖습니다. 고객별로 관리자 역할을 완전히 분리해야 하면 silo 배포를 쓰거나, 조직별 그룹·역할 매핑을 별도로 구성하세요.

**보안 요약**:

- 코드는 `AX-XXXX-XXXX-XXXX` 형식의 고엔트로피 값이고 `TENANT_STORE/invites.json`에는 SHA-256 해시만 저장합니다.
- 1회용·만료·회수 가능, 사용 시각과 사용자 이메일을 기록합니다.
- `/v1/signup`은 IP·코드 단위로 레이트리밋합니다(`SIGNUP_RATE_LIMIT_PER_HOUR`, `SIGNUP_CODE_RATE_LIMIT_PER_HOUR`).
- 잘못된 코드, 만료, 회수, 다른 이메일은 모두 같은 `invite_invalid` 응답이라 코드·이메일·조직의 존재 여부를 알 수 없습니다.
- 초대 API는 플랫폼 관리자만 호출할 수 있습니다. 테넌트 관리자는 403입니다.
- 감사 로그에는 `invite_id`·테넌트·이메일·행위자만 남고 코드 원문은 남지 않습니다.

### 6.2 수동 온보딩 (초대 없이)

1. **테넌트 생성**: `POST /v1/platform/tenants` (또는 CLI `create`). ID는 IdP의 조직 alias와 같게 정합니다.
2. **IdP 준비**: Keycloak에 같은 alias로 Organization(또는 조직 속성)을 만들고, 첫 관리자 계정을 만들어 `baryon-admin` 그룹과 해당 Organization에 넣습니다.
3. **확인**: 첫 관리자로 로그인해 `GET /v1/auth/me`의 `organization_id`가 테넌트 ID인지, `/v1/admin/settings`의 `tenant.status`가 `active`인지 봅니다.
4. **인계**: 고객 관리자에게 로그인 주소, 쿼터, 지원 채널을 전달합니다. 이후 사용자·그룹 관리는 고객 관리자가 IdP에서 합니다.

---

## 7. 쿼터와 계량

쿼터는 **pooled 모드에서만** 적용되고 계량도 pooled 모드에서만 기록합니다(single 모드는 디스크에 아무것도 쓰지 않음).

| 쿼터 | 적용 지점 | 초과 시 |
|---|---|---|
| `max_documents` | 업로드, Ossie 가져오기, 암묵지 게시 | 429 |
| `max_storage_bytes` | 같은 지점. 원본 파일 크기(없으면 본문 크기) 합계 | 429 |
| `chat_requests_per_day` | `/v1/chat/completions`, MCP `ask` (UTC 자정 기준) | 429 + `Retry-After` |
| `tokens_per_month` | 같은 지점 (UTC 월 기준, 입력+출력 토큰) | 429 + `Retry-After` |

```json
{"detail": {"code": "quota_exceeded", "message": "Tenant quota 'chat_requests_per_day' is exhausted",
            "tenant_id": "acme", "quota": "chat_requests_per_day", "used": 2000, "limit": 2000}}
```

초과할 때마다 `tenant.quota_exceeded` 감사 이벤트가 테넌트 조직으로 남습니다.

**계량 항목** (일별·월별, UTC): `chat_requests`, `prompt_tokens`, `completion_tokens`, `uploads`, `upload_bytes`. 문서 수와 저장 용량은 현재 값을 그때그때 계산합니다.

**토큰 수**: 모델 서버가 응답에 `usage`를 주면 그 값을 씁니다. 스트리밍은 pooled 모드에서 `stream_options.include_usage=true`를 요청합니다(vLLM·Ollama·OpenAI 호환). `usage`가 없으면 **문자 3개당 1토큰**으로 추정합니다(한국어·영어 혼합 기준의 보수적 근사치이며 청구용 수치가 아님). 감사 이벤트 `chat.completed`의 `usage.estimated`로 구분됩니다.

**동시성**: 일일 요청 수는 한 잠금 안에서 확인과 증가를 같이 하므로 같은 프로세스의 동시 요청이 한도를 넘지 못합니다. 업로드는 처리 중인 업로드를 예약해 두고 계산합니다. 월 토큰 한도는 응답이 끝난 뒤 기록하므로 동시에 진행 중인 요청만큼 조금 넘을 수 있습니다(soft limit).

---

## 8. 오프보딩과 데이터 삭제

`DELETE /v1/platform/tenants/{id}`:

1. 상태를 `deleting`으로 바꿉니다. 이 순간부터 모든 요청이 403입니다. `tenant.deleted` 감사.
2. 테넌트 데이터를 모든 저장소에서 지웁니다.
   - 문서 JSON과 원본 파일 (`DOCUMENT_STORE`, `raw/`)
   - Qdrant 벡터 포인트 (`organization_id` 필터로 삭제)
   - 지식 공백, 암묵지 답변, 피드백 (JSONL 파일을 원자적으로 다시 씀)
   - 온톨로지 팩 파일
   - Mem0 기억 (테넌트에서 기억을 저장한 사용자 네임스페이스별 `delete_all`)
   - 비동기 수집 작업 기록
3. 요약을 레코드(`purged_at`, `purge_summary`)에 남기고 `tenant.purged` 감사 이벤트를 기록합니다.

- **감사 로그는 지우지 않습니다.** 삭제 이후에도 `/v1/platform/tenants/{id}/audit-events`로 조회할 수 있습니다.
- 레코드는 `deleting` 상태로 남아 같은 ID가 실수로 재사용되지 않습니다. 재활성화는 409입니다.
- 일부 저장소가 실패하면(예: Mem0 연결 불가) 응답의 `complete=false`, `errors`에 남습니다. 원인을 고친 뒤 `DELETE`를 다시 호출하면 남은 데이터만 지웁니다(멱등).
- 사용량 카운터는 청구 근거로 남겨 둡니다.
- 백업·스냅샷에 남은 사본은 백업 보존 정책에 따라 별도로 만료시켜야 합니다.

---

## 9. 무엇이 격리되고 무엇이 공유되나

| 영역 | pooled | 비고 |
|---|---|---|
| 문서·원본 파일·청크 | 조직별 격리 | 모든 조회가 조직 일치 + 역할 ACL 확인. 다른 조직 문서는 404 |
| 벡터 검색 (Qdrant) | 조직별 격리 | 검색 필터의 `must`에 `organization_id` |
| 키워드·하이브리드 검색 | 조직별 격리 | 같은 `can_view` 규칙 |
| 지식 공백·클러스터·온톨로지 팩·Ossie | 조직별 격리 | 다른 조직 ID는 404 |
| 암묵지·피드백 | 조직별 격리 | 첨부는 자기 조직 문서만 |
| Mem0 기억 | 조직별 격리 | 사용자 ID를 `조직::사용자`로 네임스페이스. 같은 사용자 ID라도 공유 안 됨 |
| Langfuse 트레이스 | 사용자 ID 네임스페이스 | 운영자 도구이므로 테넌트에 노출하지 않음 |
| 감사 로그 | 조직별 조회 | 플랫폼 관리자는 테넌트별 조회 API 사용 |
| 관리 설정 화면 | 테넌트 수치만 | 호스트·컨테이너·공유 서비스 지표와 운영 대시보드 링크는 숨김 |
| 재색인 | 자기 조직 문서만 | |
| MCP 서버 (`/mcp`) | REST와 동일 | 같은 핸들러·같은 쿼터 |
| 커넥터 도구 실행 | **기본 차단** | 게이트웨이 토큰 하나를 모든 테넌트가 쓰기 때문. `TENANT_SHARED_GATEWAY_ALLOWED=true`는 게이트웨이 쪽에서 테넌트별 자격 증명을 분리한 경우에만 |
| API 프로세스, 모델 게이트웨이, 임베딩 | **공유** | 시끄러운 이웃은 쿼터와 `RATE_LIMIT_PER_MINUTE`로 완화 |
| Qdrant·PostgreSQL·Redis 인스턴스 | **공유** | 논리 격리(페이로드 필터, 행 단위 조직 열) |

더 강한 분리(전용 모델·전용 DB·네트워크 분리·고객 관리 키)가 필요하면 **silo**로 배포합니다. silo는 `TENANCY_MODE=single`인 같은 이미지입니다.

---

## 10. 한계와 다음 단계

- **API 태스크 1개 전제**: 레지스트리·사용량 카운터·업로드 예약·비동기 작업 기록이 프로세스 메모리에 있습니다. API를 여러 개로 늘리려면 레지스트리를 PostgreSQL로, 카운터를 Redis(`INCRBY` + 만료) 또는 PostgreSQL로 옮겨야 합니다. 그 전에는 ECS 서비스의 desired count를 1로 유지하세요.
- 카운터는 `USAGE_FLUSH_SECONDS`마다 디스크에 반영되므로, 프로세스가 비정상 종료되면 마지막 몇 초의 사용량이 빠질 수 있습니다(정상 종료 시에는 모두 기록).
- 문서 저장소는 아직 로컬 JSON 파일입니다. 공유 볼륨(EFS 등)에 두고, 대규모 테넌트는 silo로 분리하세요.
- 테넌트 정지·삭제는 진행 중인 스트리밍 응답을 끊지 않습니다. 다음 요청부터 막힙니다.
- 커넥터 도구의 테넌트별 자격 증명 분리는 아직 없습니다(기본 차단).
