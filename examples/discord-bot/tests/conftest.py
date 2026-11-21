import pytest

AUTH_ENV = ("BARYON_API_TOKEN", "BARYON_OIDC_TOKEN_URL", "BARYON_OIDC_CLIENT_ID", "BARYON_OIDC_CLIENT_SECRET")


@pytest.fixture(autouse=True)
def dev_mode_by_default(monkeypatch):
    """Tests start in dev mode regardless of the caller's shell or .env."""
    for name in AUTH_ENV:
        monkeypatch.delenv(name, raising=False)
