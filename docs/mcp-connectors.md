# MCP SaaS 커넥터 레이어

> 상태: 1단계(읽기 전용 카탈로그·도구 검색·정책 판정) 구현
> 이슈: #49
> 게이트웨이: IBM ContextForge (Apache-2.0, optional profile)
> 관련: [docs/auth-oidc.md](auth-oidc.md)

Baryon AX는 1,000개 SaaS 커넥터를 직접 만들지 않습니다. **게이트웨이 + OpenAPI 생성 + Top 30 큐레이션 + 도구 검색 + OAuth 볼트/정책/감사**로 확장합니다.

---

## 1. 구조

```text
Agent / Chat
   │
   ▼
Baryon AX Tool Router ── Qdrant/FastEmbed 도구 인덱스 (2단계)
   │  의도별 top-k 10~20개만 모델에 노출
   ▼
ContextForge (MCP Gateway, optional)
   ├─ 큐레이션 MCP 서버
   ├─ OpenAPI → MCP (롱테일)
   └─ 선택적 외부 어댑터 (Nango / Composio / Metorial)
   │
   ├─ Credential Vault (사용자·조직별 OAuth/API 키)
   ├─ Policy (읽기/쓰기, 역할, 승인)
   └─ Audit (도구 호출, 인자 해시, 결과, 지연)
```

핵심은 **도구를 한꺼번에 모델에 넣지 않는 것**입니다. 1,000개 SaaS × 수십 액션은 컨텍스트에 들어가지 않습니다. 검색·라우팅으로 매 턴 필요한 도구만 노출합니다.

---

## 2. 현재 구현 (1단계)

| 구성 | 상태 | 설명 |
|---|---|---|
| 커넥터 카탈로그 | ✅ | Top 30, `apps/api/src/baryon/connectors.py` |
| 도구 검색 | ✅ | 어휘 기반 `GET /v1/tools/search` (한/영 alias 지원) |
| 정책 판정 | ✅ | 읽기 허용, 쓰기는 관리자 또는 승인 필요 표시 |
| ContextForge 프로필 | ✅ | `docker-compose.mcp.yml` (선택 실행) |
| 실행/OAuth | ⛔ | 2단계 |
| Qdrant 임베딩 검색 | ⛔ | 2단계 (인터페이스는 동일) |
| OpenAPI 생성기 | ⛔ | 2단계 |

### API

```bash
curl -s http://127.0.0.1:8000/v1/connectors | jq '.total, .categories'
curl -s 'http://127.0.0.1:8000/v1/tools/search?q=slack%20message&limit=5' | jq
curl -s 'http://127.0.0.1:8000/v1/tools/search?q=슬랙%20메시지' | jq
curl -s http://127.0.0.1:8000/v1/connectors/slack | jq
curl -s http://127.0.0.1:8000/v1/mcp/status | jq
```

`AUTH_MODE=oidc`에서는 `Authorization: Bearer <token>`이 필요합니다. `AUTH_MODE=dev`에서는 기존 `X-Roles` 헤더로 정책을 확인할 수 있습니다.

응답의 `decision`:

```json
{
  "allowed": true,
  "requires_approval": true,
  "reason": "write tool requires approval"
}
```

읽기 도구는 `member`도 사용할 수 있고, 쓰기 도구는 `admin`이 아니면 승인 플래그가 붙습니다. 실제 승인·실행은 2단계입니다.

---

## 3. Top 30

| 분야 | 커넥터 |
|---|---|
| 커뮤니케이션 | Slack, Microsoft Teams, Gmail, Zoom, Naver Works, Dooray |
| 문서·지식 | Notion, Confluence, Google Drive, SharePoint, OneDrive, Box, Dropbox |
| 프로젝트 | Jira, Linear, Asana, Trello, Monday.com, Airtable |
| 개발 | GitHub, GitLab |
| CRM·CS | Salesforce, HubSpot, Zendesk, ServiceNow |
| ERP·HCM | SAP S/4HANA, Oracle Fusion, Workday |
| 디자인·일정 | Figma, Google Calendar |

각 커넥터는 읽기/쓰기 액션 2~3개를 메타데이터로 정의합니다. 실제 API 스키마·OAuth 범위는 2단계에서 연결합니다.

> 기본 정책: SAP S/4HANA, Oracle Fusion, Workday는 **관리자 전용**입니다. 일반 멤버에게는 노출되지 않으며, 관리자 대시보드에서만 전체 목록을 볼 수 있습니다.

---

## 4. ContextForge 실행

```bash
cp .env.mcp.example .env.mcp
# .env.mcp의 change-me 값을 실제 값으로 교체
docker compose --env-file .env.mcp -f docker-compose.mcp.yml up -d
```

| 항목 | 값 |
|---|---|
| ContextForge Admin UI | http://127.0.0.1:4444/admin |
| API | http://127.0.0.1:4444 |
| 저장소 | `mcp-data` 볼륨의 SQLite |
| 기본 바인딩 | `127.0.0.1`만. API 컨테이너에서 접근하려면 `MCP_GATEWAY_BIND=172.17.0.1` |

API가 게이트웨이를 바라보게 하려면 `.env`에 다음을 추가합니다.

```env
# API가 같은 호스트에서 실행될 때
MCP_GATEWAY_URL=http://127.0.0.1:4444
# API가 Docker 컨테이너일 때
# MCP_GATEWAY_URL=http://host.docker.internal:4444
```

> 운영에서는 `MCP_GATEWAY_IMAGE`를 digest로 고정하고, `start` 모드·TLS·외부 DB·SSO를 사용하세요. `latest` 태그는 개발용입니다.

---

## 5. 라이선스

| 구성 | 라이선스 | 코어 번들 |
|---|---|---|
| ContextForge | Apache-2.0 | ✅ |
| Docker MCP Gateway | MIT | ✅ (대안) |
| Activepieces Community | MIT | ✅ (선택) |
| Arcade MCP | MIT | ✅ (선택) |
| Nango | Elastic-2.0 | ❌ 별도 서비스 |
| Metorial Platform | FSL-1.1 | ❌ 별도 서비스 |
| Metorial Catalog | Apache-2.0 | ✅ (카탈로그만) |
| Composio | SDK MIT / hosted | ❌ 에어갭 불가 |

- Nango/Metorial/Composio는 **선택적 확장 어댑터**입니다. MIT 코어에 코드를 복사하지 않습니다.
- 외부 SaaS로 데이터가 나가는 구성은 고객의 에어갭·데이터 주권 요구와 충돌할 수 있습니다.

---

## 6. 2단계 로드맵

1. **OAuth 볼트**: 사용자·조직별 연결, 토큰 암호화, 갱신, 연결 해제
2. **실행 프록시**: `POST /v1/tools/{id}/call` → ContextForge/MCP, 쓰기 승인 게이트
3. **Qdrant 도구 검색**: 도구 설명 임베딩, top-k + LLM shortlist
4. **OpenAPI → MCP**: 스펙에서 도구 스키마 생성·검증·버전 관리
5. **Top 30 연결 검증**: 인증·권한·rate limit·오류 처리
6. **선택적 어댑터**: Nango/Composio/Metorial을 별도 서비스로 연결

---

## 7. 보안 원칙

- 도구 자격증명은 **LLM에 전달하지 않습니다.** 서버 측 볼트에서 주입합니다.
- 쓰기 도구는 **사람 승인** 또는 관리자 역할이 필요합니다.
- 조직·역할별 **allowlist**와 egress 허용 목록을 적용합니다.
- 도구 출력의 프롬프트 인젝션을 신뢰하지 않습니다.
- 모든 호출은 **감사 로그**에 남깁니다(인자 해시·결과 코드·지연).
- 서드파티 MCP 서버는 컨테이너/프로세스 격리와 최소 권한으로 실행합니다.
