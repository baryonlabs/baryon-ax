"""Identity-provider provisioning for invite-code signup.

``stub`` is the default whenever ``AUTH_MODE=dev`` or Keycloak is not fully
configured. It writes nothing and is safe for tests and local demos.

``keycloak`` uses the Keycloak Admin REST API with a service account:

* create an Organization whose alias is the tenant id;
* find or create the first admin user by e-mail;
* add the user to the Organization and to the admin group;
* send Keycloak's "execute actions" e-mail (set password, verify e-mail).

If a step fails, ``provision`` removes what it created before raising
``IdpError``. The signup endpoint then removes the tenant record (compensating
transaction), so a failed signup leaves no half-provisioned organization.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("baryon.ax.idp")


class IdpError(RuntimeError):
    """The identity provider could not be prepared. The message is safe to log."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class IdpSettings:
    mode: str = "stub"
    base_url: str = ""
    realm: str = ""
    client_id: str = ""
    client_secret: str = ""
    admin_group: str = "baryon-admin"
    web_client_id: str = "baryon-ax-web"
    timeout: float = 10.0

    @classmethod
    def from_env(cls) -> "IdpSettings":
        raw_mode = os.getenv("IDP_MODE", "auto").strip().lower() or "auto"
        if raw_mode not in {"auto", "stub", "keycloak"}:
            raise ValueError("IDP_MODE must be 'auto', 'stub' or 'keycloak'")

        base_url = os.getenv("KEYCLOAK_URL", "").strip().rstrip("/")
        realm = os.getenv("KEYCLOAK_REALM", "").strip()
        issuer = os.getenv("OIDC_ISSUER", "").strip().rstrip("/")
        # Derive both from the OIDC issuer when the operator already set only that.
        if "/realms/" in issuer:
            issuer_base, issuer_realm = issuer.rsplit("/realms/", 1)
            base_url = base_url or issuer_base
            realm = realm or issuer_realm.split("/")[0]
        client_id = os.getenv("KEYCLOAK_ADMIN_CLIENT_ID", "").strip()
        client_secret = os.getenv("KEYCLOAK_ADMIN_CLIENT_SECRET", "").strip()
        configured = bool(base_url and realm and client_id and client_secret)
        if raw_mode == "auto":
            mode = "keycloak" if os.getenv("AUTH_MODE", "dev").strip().lower() == "oidc" and configured else "stub"
        else:
            mode = raw_mode
        if mode == "keycloak" and not configured:
            raise ValueError(
                "IDP_MODE=keycloak requires KEYCLOAK_URL, KEYCLOAK_REALM, "
                "KEYCLOAK_ADMIN_CLIENT_ID and KEYCLOAK_ADMIN_CLIENT_SECRET"
            )
        return cls(
            mode=mode,
            base_url=base_url,
            realm=realm,
            client_id=client_id,
            client_secret=client_secret,
            admin_group=os.getenv("KEYCLOAK_ADMIN_GROUP", "baryon-admin").strip() or "baryon-admin",
            web_client_id=os.getenv("KEYCLOAK_WEB_CLIENT_ID", "baryon-ax-web").strip() or "baryon-ax-web",
            timeout=float(os.getenv("KEYCLOAK_HTTP_TIMEOUT_SECONDS", "10")),
        )


@dataclass
class ProvisionedOrganization:
    organization_id: str
    external_id: str
    admin_email: str
    admin_user_id: str | None = None
    created_user: bool = False
    admin_invited: bool = False
    delivery: str = ""


class StubIdpAdapter:
    """No-op IdP for dev/tests. TenantGate only needs the registry entry."""

    mode = "stub"

    def provision(self, organization_id: str, organization_name: str, admin_email: str, login_url: str) -> ProvisionedOrganization:
        logger.info(
            "stub IdP: organization %s (%s) ready for %s; sign-in %s",
            organization_id, organization_name, admin_email, login_url,
        )
        return ProvisionedOrganization(
            organization_id=organization_id,
            external_id=f"stub:{organization_id}",
            admin_email=admin_email,
            admin_invited=True,
            delivery="stub",
        )

    def rollback(self, provisioned: ProvisionedOrganization) -> None:
        logger.info("stub IdP: rollback organization %s", provisioned.organization_id)


class KeycloakAdminAdapter:
    """Keycloak 26 Organizations + Admin REST, authenticated as a service account."""

    mode = "keycloak"

    def __init__(self, settings: IdpSettings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client or httpx.Client(timeout=settings.timeout)
        self._token_value: str | None = None
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ transport

    def _token(self) -> str:
        with self._lock:
            if self._token_value and self._token_expires_at > time.monotonic() + 30:
                return self._token_value
            url = f"{self.settings.base_url}/realms/{quote(self.settings.realm)}/protocol/openid-connect/token"
            try:
                response = self._client.post(
                    url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.settings.client_id,
                        "client_secret": self.settings.client_secret,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise IdpError(f"Keycloak token request failed ({type(error).__name__})") from error
            token = str(payload.get("access_token") or "")
            if not token:
                raise IdpError("Keycloak token response had no access_token")
            self._token_value = token
            self._token_expires_at = time.monotonic() + max(30.0, float(payload.get("expires_in", 300)) - 30)
            return token

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.settings.base_url}/admin/realms/{quote(self.settings.realm)}{path}"
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}
        try:
            response = self._client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise IdpError(f"Keycloak admin request failed ({method} {path}, {type(error).__name__})") from error
        if response.status_code >= 400:
            # Never include the body: it can echo user attributes or realm details.
            raise IdpError(
                f"Keycloak admin request failed ({method} {path} -> {response.status_code})",
                status_code=response.status_code,
            )
        return response

    @staticmethod
    def _id_from_location(response: httpx.Response) -> str:
        location = response.headers.get("location", "").rstrip("/")
        return location.rsplit("/", 1)[-1] if location else ""

    # ------------------------------------------------------------------ resources

    def _find_organization(self, alias: str) -> dict[str, Any] | None:
        response = self._request("GET", "/organizations", params={"search": alias, "exact": "true"})
        try:
            items = response.json()
        except ValueError:
            return None
        for item in items if isinstance(items, list) else []:
            if str(item.get("alias") or "") == alias:
                return item
        return None

    def _create_organization(self, organization_id: str, organization_name: str, login_url: str) -> dict[str, Any]:
        body = {
            "name": organization_name[:120],
            "alias": organization_id,
            "enabled": True,
            "redirectUrl": login_url,
        }
        try:
            created = self._request("POST", "/organizations", json=body)
        except IdpError as error:
            if error.status_code != 409:
                raise
            existing = self._find_organization(organization_id)
            if existing is None:
                raise
            return existing
        identifier = self._id_from_location(created)
        if identifier:
            return {"id": identifier, "alias": organization_id, "name": organization_name}
        existing = self._find_organization(organization_id)
        if existing is None:
            raise IdpError("Keycloak created an organization without an id")
        return existing

    def _find_user(self, email: str) -> dict[str, Any] | None:
        response = self._request("GET", "/users", params={"email": email, "exact": "true"})
        try:
            items = response.json()
        except ValueError:
            return None
        for item in items if isinstance(items, list) else []:
            if str(item.get("email") or "").lower() == email.lower():
                return item
        return None

    def _ensure_user(self, email: str) -> tuple[dict[str, Any], bool]:
        existing = self._find_user(email)
        if existing is not None:
            return existing, False
        created = self._request(
            "POST",
            "/users",
            json={
                "username": email,
                "email": email,
                "enabled": True,
                "emailVerified": False,
            },
        )
        identifier = self._id_from_location(created)
        if identifier:
            return {"id": identifier, "email": email}, True
        found = self._find_user(email)
        if found is None:
            raise IdpError("Keycloak created a user without an id")
        return found, True

    def _group_id(self) -> str:
        name = self.settings.admin_group
        response = self._request("GET", "/groups", params={"search": name, "exact": "true"})
        try:
            groups = response.json()
        except ValueError:
            groups = []
        for group in groups if isinstance(groups, list) else []:
            if str(group.get("name") or "") == name:
                return str(group["id"])
        created = self._request("POST", "/groups", json={"name": name})
        identifier = self._id_from_location(created)
        if identifier:
            return identifier
        response = self._request("GET", "/groups", params={"search": name, "exact": "true"})
        try:
            groups = response.json()
        except ValueError:
            groups = []
        for group in groups if isinstance(groups, list) else []:
            if str(group.get("name") or "") == name:
                return str(group["id"])
        raise IdpError(f"Keycloak group '{name}' could not be resolved")

    def _add_member(self, organization_key: str, user_id: str) -> None:
        try:
            # This endpoint takes the bare user id as JSON, not an object.
            self._request("POST", f"/organizations/{quote(organization_key)}/members", json=user_id)
        except IdpError as error:
            # 409 means the membership already exists (recovery/retry).
            if error.status_code != 409:
                raise

    def _add_admin_group(self, user_id: str) -> None:
        group_id = self._group_id()
        try:
            self._request("PUT", f"/users/{quote(user_id)}/groups/{quote(group_id)}")
        except IdpError as error:
            if error.status_code != 409:
                raise

    def _send_invitation(self, user_id: str) -> None:
        self._request(
            "PUT",
            f"/users/{quote(user_id)}/execute-actions-email",
            params={"client_id": self.settings.web_client_id},
            json=["UPDATE_PASSWORD", "VERIFY_EMAIL"],
        )

    # ------------------------------------------------------------------ public API

    def provision(self, organization_id: str, organization_name: str, admin_email: str, login_url: str) -> ProvisionedOrganization:
        organization = self._create_organization(organization_id, organization_name, login_url)
        organization_key = str(organization.get("id") or organization_id)
        user: dict[str, Any] | None = None
        created_user = False
        try:
            user, created_user = self._ensure_user(admin_email)
            user_id = str(user["id"])
            self._add_member(organization_key, user_id)
            self._add_admin_group(user_id)
            self._send_invitation(user_id)
        except Exception:
            # Leave the realm as we found it, then let the caller compensate the tenant.
            if user is not None and created_user:
                try:
                    self._request("DELETE", f"/users/{quote(str(user['id']))}")
                except IdpError:
                    logger.warning("Could not remove a half-created Keycloak user for %s", organization_id)
            try:
                self._request("DELETE", f"/organizations/{quote(organization_key)}")
            except IdpError:
                logger.warning("Could not remove a half-created Keycloak organization %s", organization_id)
            raise
        return ProvisionedOrganization(
            organization_id=organization_id,
            external_id=organization_key,
            admin_email=admin_email,
            admin_user_id=str(user["id"]),
            created_user=created_user,
            admin_invited=True,
            delivery="keycloak",
        )

    def rollback(self, provisioned: ProvisionedOrganization) -> None:
        if provisioned.external_id:
            try:
                self._request("DELETE", f"/organizations/{quote(provisioned.external_id)}")
            except IdpError as error:
                if error.status_code != 404:
                    logger.warning("Could not roll back Keycloak organization %s: %s", provisioned.organization_id, error)
        if provisioned.created_user and provisioned.admin_user_id:
            try:
                self._request("DELETE", f"/users/{quote(provisioned.admin_user_id)}")
            except IdpError as error:
                if error.status_code != 404:
                    logger.warning("Could not roll back Keycloak user for %s: %s", provisioned.organization_id, error)


def build_idp_adapter(settings: IdpSettings | None = None) -> StubIdpAdapter | KeycloakAdminAdapter:
    settings = settings or IdpSettings.from_env()
    if settings.mode == "keycloak":
        return KeycloakAdminAdapter(settings)
    return StubIdpAdapter()
