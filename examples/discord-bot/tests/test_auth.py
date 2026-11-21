from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baryon_auth import TokenSource  # noqa: E402
from baryon_discord_bot import BaryonClient, Config  # noqa: E402

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
                return httpx.Response(200, json={"access_token": f"tok-{self.issued}", "expires_in": self.expires_in})
            self.api_requests.append(request)
            if request.headers.get("authorization", "").removeprefix("Bearer ") in self.revoked:
                return httpx.Response(401, json={"detail": "invalid token"})
            if request.url.path == "/v1/chat/completions":
                return httpx.Response(200, json={"id": "t", "choices": [{"message": {"content": "ok"}}], "citations": []})
            if request.url.path == "/v1/tacit/entries":
                return httpx.Response(200, json={"id": "tac_1"})
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
    kwargs = token_kwargs or {"token_url": TOKEN_URL, "client_id": "baryon-discord-bot", "client_secret": "s3cret"}
    return BaryonClient(config, http=http, tokens=TokenSource(http, clock=clock or Clock(), **kwargs))


def test_oidc_per_user_calls_are_delegated_without_dev_headers() -> None:
    fake = FakeIdpAndApi()
    baryon = oidc_client(fake)
    baryon.ask([{"role": "user", "content": "연차?"}], 111, thread_id="discord:500:501")
    baryon.save_answer(222, "q", "a", "gap_a")

    for request, user in zip(fake.api_requests, ("discord:111", "discord:222")):
        headers = request.headers
        assert headers["authorization"] == "Bearer tok-1"
        assert headers["x-on-behalf-of"] == user and headers["x-on-behalf-of-roles"] == "member"
        for name in ("x-organization-id", "x-user-id", "x-roles"):
            assert name not in headers


def test_oidc_bot_calls_send_no_on_behalf_of() -> None:
    fake = FakeIdpAndApi()
    baryon = oidc_client(fake)
    baryon.open_gaps()
    baryon.dismiss_gap("gap_b")
    for request in fake.api_requests:
        assert request.headers["authorization"] == "Bearer tok-1"
        for name in ("x-on-behalf-of", "x-on-behalf-of-roles", "x-organization-id", "x-user-id", "x-roles"):
            assert name not in request.headers


def test_token_is_cached_until_expiry_minus_margin() -> None:
    fake, clock = FakeIdpAndApi(expires_in=300), Clock()
    baryon = oidc_client(fake, clock)
    baryon.open_gaps()
    baryon.snippets("연차휴가", 111)
    assert fake.issued == 1
    assert parse_qs(fake.token_requests[0].content.decode())["grant_type"] == ["client_credentials"]
    clock.now += 271  # past expires_in - 30 s
    baryon.open_gaps()
    assert fake.issued == 2 and fake.api_requests[-1].headers["authorization"] == "Bearer tok-2"


def test_refresh_once_on_401_then_give_up() -> None:
    fake = FakeIdpAndApi()
    baryon = oidc_client(fake)
    baryon.open_gaps()
    fake.revoked.add("tok-1")
    baryon.ask([{"role": "user", "content": "q"}], 111, thread_id="t")
    assert fake.issued == 2
    assert [r.headers["authorization"] for r in fake.api_requests[-2:]] == ["Bearer tok-1", "Bearer tok-2"]

    fake.revoked.update({"tok-2", "tok-3"})
    with pytest.raises(httpx.HTTPStatusError):
        baryon.open_gaps()
    assert fake.issued == 3  # one refresh per call, no loop


def test_static_token_fallback(monkeypatch) -> None:
    monkeypatch.setenv("BARYON_API_TOKEN", "static-tok")
    fake = FakeIdpAndApi()
    baryon = BaryonClient(Config(api_url="http://api.test"), http=fake.client())
    fake.revoked.add("static-tok")
    with pytest.raises(httpx.HTTPStatusError):
        baryon.open_gaps()
    assert fake.issued == 0 and len(fake.api_requests) == 1
    assert fake.api_requests[0].headers["authorization"] == "Bearer static-tok"
    assert "x-organization-id" not in fake.api_requests[0].headers


def test_dev_mode_keeps_bot_identity_and_organization() -> None:
    fake = FakeIdpAndApi()
    baryon = BaryonClient(Config(api_url="http://api.test", organization_id="org"), service_roles="admin",
                          http=fake.client())
    baryon.open_gaps()
    baryon.publish(222, "tac_1")
    as_bot, as_user = (request.headers for request in fake.api_requests)
    assert (as_bot["x-user-id"], as_bot["x-roles"], as_bot["x-organization-id"]) == ("discord-bot", "admin", "org")
    assert "x-on-behalf-of" not in as_bot
    assert (as_user["x-user-id"], as_user["x-on-behalf-of"], as_user["x-organization-id"]) == ("discord-bot", "discord:222", "org")
    assert "authorization" not in as_user and fake.issued == 0
