from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baryon_auth import TokenSource  # noqa: E402
from baryon_slack_bot import BaryonClient, Config  # noqa: E402
from proactive import KnowledgeClient  # noqa: E402

TOKEN_URL = "http://idp.test/realms/baryon-ax/protocol/openid-connect/token"


class FakeIdpAndApi:
    """Token endpoint and API on one MockTransport; the API rejects tokens listed in ``revoked``."""

    def __init__(self, expires_in: int = 300) -> None:
        self.expires_in = expires_in
        self.issued = 0
        self.revoked: set[str] = set()
        self.token_requests: list[httpx.Request] = []
        self.api_requests: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == TOKEN_URL:
                self.token_requests.append(request)
                self.issued += 1
                return httpx.Response(200, json={"access_token": f"tok-{self.issued}", "expires_in": self.expires_in,
                                                 "token_type": "Bearer"})
            self.api_requests.append(request)
            if request.headers.get("authorization", "").removeprefix("Bearer ") in self.revoked:
                return httpx.Response(401, json={"detail": "invalid token"})
            if request.url.path == "/v1/chat/completions":
                return httpx.Response(200, json={"id": "t", "choices": [{"message": {"content": "ok"}}], "citations": []})
            return httpx.Response(200, json={"data": []})
        return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def oidc_client(fake: FakeIdpAndApi, clock: Clock | None = None, **token_kwargs) -> BaryonClient:
    config = Config(api_url="http://api.test", organization_id="org-from-env", roles="member")
    http = fake.client()
    kwargs = token_kwargs or {"token_url": TOKEN_URL, "client_id": "baryon-slack-bot", "client_secret": "s3cret"}
    tokens = TokenSource(http, clock=clock or Clock(), **kwargs)
    return BaryonClient(config, http=http, tokens=tokens)


def test_oidc_per_user_call_is_delegated_without_dev_headers() -> None:
    fake = FakeIdpAndApi()
    baryon = oidc_client(fake)
    baryon.ask([{"role": "user", "content": "연차?"}], "U1", thread_id="slack:C1:1.1")

    headers = fake.api_requests[0].headers
    assert headers["authorization"] == "Bearer tok-1"
    assert headers["x-on-behalf-of"] == "slack:U1" and headers["x-on-behalf-of-roles"] == "member"
    # organization comes from the token; identity headers would be ignored anyway
    for name in ("x-organization-id", "x-user-id", "x-roles"):
        assert name not in headers


def test_oidc_bot_call_sends_no_on_behalf_of() -> None:
    fake = FakeIdpAndApi()
    knowledge = KnowledgeClient(oidc_client(fake))
    knowledge.open_gaps()

    headers = fake.api_requests[0].headers
    assert headers["authorization"] == "Bearer tok-1"
    for name in ("x-on-behalf-of", "x-on-behalf-of-roles", "x-organization-id", "x-user-id", "x-roles"):
        assert name not in headers


def test_client_credentials_request_and_cache_until_expiry() -> None:
    fake, clock = FakeIdpAndApi(expires_in=300), Clock()
    baryon = oidc_client(fake, clock)
    for _ in range(3):
        baryon.snippets("연차휴가", "U1")
    assert fake.issued == 1
    form = parse_qs(fake.token_requests[0].content.decode())
    assert form == {"grant_type": ["client_credentials"], "client_id": ["baryon-slack-bot"], "client_secret": ["s3cret"]}

    clock.now += 300 - 31  # still inside expires_in minus the 30 s margin
    baryon.snippets("연차휴가", "U1")
    assert fake.issued == 1
    clock.now += 2  # within the margin: fetch a fresh token before it expires
    baryon.snippets("연차휴가", "U1")
    assert fake.issued == 2
    assert fake.api_requests[-1].headers["authorization"] == "Bearer tok-2"


def test_refresh_once_on_401() -> None:
    fake = FakeIdpAndApi()
    baryon = oidc_client(fake)
    baryon.snippets("연차휴가", "U1")
    fake.revoked.add("tok-1")  # revoked before its expiry

    baryon.ask([{"role": "user", "content": "q"}], "U1", thread_id="t")
    assert fake.issued == 2
    assert [r.headers["authorization"] for r in fake.api_requests[-2:]] == ["Bearer tok-1", "Bearer tok-2"]
    assert fake.api_requests[-1].headers["x-on-behalf-of"] == "slack:U1"


def test_second_401_is_not_retried_again() -> None:
    fake = FakeIdpAndApi()
    baryon = oidc_client(fake)
    fake.revoked.update({"tok-1", "tok-2", "tok-3"})
    with pytest.raises(httpx.HTTPStatusError):
        baryon.ask([{"role": "user", "content": "q"}], "U1", thread_id="t")
    assert fake.issued == 2 and len(fake.api_requests) == 2


def test_static_token_fallback_is_sent_as_is_without_retry() -> None:
    fake = FakeIdpAndApi()
    baryon = oidc_client(fake, static_token="static-tok")
    fake.revoked.add("static-tok")
    with pytest.raises(httpx.HTTPStatusError):
        baryon.ask([{"role": "user", "content": "q"}], "U1", thread_id="t")
    assert fake.issued == 0 and len(fake.api_requests) == 1
    headers = fake.api_requests[0].headers
    assert headers["authorization"] == "Bearer static-tok" and "x-organization-id" not in headers


def test_env_selects_client_credentials_over_static_token(monkeypatch) -> None:
    monkeypatch.setenv("BARYON_API_TOKEN", "static-tok")
    monkeypatch.setenv("BARYON_OIDC_TOKEN_URL", TOKEN_URL)
    monkeypatch.setenv("BARYON_OIDC_CLIENT_ID", "baryon-slack-bot")
    monkeypatch.setenv("BARYON_OIDC_CLIENT_SECRET", "s3cret")
    fake = FakeIdpAndApi()
    baryon = BaryonClient(Config(api_url="http://api.test"), http=fake.client())
    baryon.snippets("연차휴가", "U1")
    assert baryon.auth.oidc and fake.api_requests[0].headers["authorization"] == "Bearer tok-1"


def test_dev_mode_without_token() -> None:
    fake = FakeIdpAndApi()
    baryon = BaryonClient(Config(api_url="http://api.test", organization_id="org"), http=fake.client(), bot_roles="admin")
    assert not baryon.auth.oidc
    KnowledgeClient(baryon).open_gaps()
    baryon.snippets("연차휴가", "U1")
    as_bot, as_user = (request.headers for request in fake.api_requests)
    assert (as_bot["x-user-id"], as_bot["x-roles"], as_bot["x-organization-id"]) == ("slack-bot", "admin", "org")
    assert "x-on-behalf-of" not in as_bot and "authorization" not in as_bot
    assert (as_user["x-user-id"], as_user["x-on-behalf-of"], as_user["x-organization-id"]) == ("slack-bot", "slack:U1", "org")
    assert fake.issued == 0
