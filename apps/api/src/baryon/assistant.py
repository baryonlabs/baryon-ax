"""Voice secretary bridge (apps/secretary).

The secretary runs on the operator's workstation, where Claude Code and its MCP
connections (mail, calendar, to-dos, the DureClaw fleet) are signed in. It can read
mail and dispatch work, so only the users listed in SECRETARY_OWNERS may talk to it,
and every conversation is tied to the user who started it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx


@dataclass
class AssistantConfig:
    base_url: str
    api_key: str
    owners: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_env(cls) -> "AssistantConfig":
        return cls(
            base_url=os.getenv("SECRETARY_URL", "").strip().rstrip("/"),
            api_key=os.getenv("SECRETARY_API_KEY", "").strip(),
            owners=frozenset(owner.strip() for owner in os.getenv("SECRETARY_OWNERS", "").split(",") if owner.strip()),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.owners)

    def allowed(self, user_id: str) -> bool:
        return self.enabled and user_id in self.owners

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


class AssistantUnavailable(Exception):
    pass


async def call(config: AssistantConfig, method: str, path: str, *, timeout: float = 30.0, **kwargs) -> tuple[int, dict]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, f"{config.base_url}{path}", headers=config.headers(), **kwargs)
    except httpx.HTTPError as error:
        raise AssistantUnavailable(f"voice secretary unreachable: {type(error).__name__}") from error
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text[:200]}
    return response.status_code, body
