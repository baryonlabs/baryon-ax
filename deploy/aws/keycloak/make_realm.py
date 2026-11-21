#!/usr/bin/env python3
"""Build the AWS realm template from the development realm.

    python3 deploy/aws/keycloak/make_realm.py   # rewrites realm-baryon-ax.aws.json

The development realm (deploy/keycloak/realm-baryon-ax.json) ships fixed demo
passwords, a fixed bot client secret and localhost redirect URIs. None of that may
reach a public environment, so this script removes users and secrets, requires TLS,
adds the platform-admin group, and declares the user profile so the `organization`
attribute (the tenant) is editable by administrators only. Users must never be
able to change their own organization: that would move them into another tenant.

Redirect URIs, the bot client secret and users are set per environment at first
boot by bootstrap.sh from Secrets Manager.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "deploy/keycloak/realm-baryon-ax.json"
TARGET = Path(__file__).with_name("realm-baryon-ax.aws.json")

ADMIN_ONLY = {"view": ["admin"], "edit": ["admin"]}
USER_PROFILE = {
    "attributes": [
        {"name": "username", "displayName": "${username}", "multivalued": False,
         "validations": {"length": {"min": 3, "max": 255}, "username-prohibited-characters": {},
                         "up-username-not-idn-homograph": {}},
         "permissions": {"view": ["admin", "user"], "edit": ["admin"]}},
        {"name": "email", "displayName": "${email}", "multivalued": False,
         "validations": {"email": {}, "length": {"max": 255}},
         "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]}},
        {"name": "firstName", "displayName": "${firstName}", "multivalued": False,
         "validations": {"length": {"max": 255}, "person-name-prohibited-characters": {}},
         "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]}},
        {"name": "lastName", "displayName": "${lastName}", "multivalued": False,
         "validations": {"length": {"max": 255}, "person-name-prohibited-characters": {}},
         "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]}},
        {"name": "organization", "displayName": "Organization (tenant)", "multivalued": False,
         "validations": {"pattern": {"pattern": "^[a-z0-9][a-z0-9-]{1,39}$", "error-message": "invalid organization id"}},
         "permissions": ADMIN_ONLY},
    ],
    "groups": [{"name": "user-metadata", "displayHeader": "User metadata",
                "displayDescription": "Attributes, which refer to user metadata"}],
    "unmanagedAttributePolicy": "ADMIN_EDIT",
}


def main() -> None:
    realm = json.loads(SOURCE.read_text(encoding="utf-8"))
    realm.pop("users", None)
    realm["sslRequired"] = "external"
    realm["registrationAllowed"] = False
    realm["bruteForceProtected"] = True
    realm["editUsernameAllowed"] = False
    realm["resetPasswordAllowed"] = False  # no mail server in the demo; admins reset passwords
    realm["passwordPolicy"] = "length(12) and notUsername and notEmail"

    groups = {group["name"] for group in realm.get("groups", [])}
    if "baryon-platform-admin" not in groups:
        realm.setdefault("groups", []).append({"name": "baryon-platform-admin", "path": "/baryon-platform-admin"})

    for client in realm.get("clients", []):
        client.pop("secret", None)
        if client.get("clientId") == "baryon-ax-web":
            client["redirectUris"] = []
            client["webOrigins"] = []
            client.setdefault("attributes", {})["post.logout.redirect.uris"] = "+"

    clients = {client["clientId"]: client for client in realm.get("clients", [])}
    if "baryon-ax-cli" not in clients and "baryon-ax-web" in clients:
        # Operators and scripts/tenant_admin.py sign in with the device flow: no
        # password ever passes through the terminal, and no client secret exists.
        realm["clients"].append({
            "clientId": "baryon-ax-cli",
            "name": "Baryon AX CLI (device login)",
            "enabled": True,
            "publicClient": True,
            "standardFlowEnabled": False,
            "implicitFlowEnabled": False,
            "directAccessGrantsEnabled": False,
            "serviceAccountsEnabled": False,
            "protocol": "openid-connect",
            "attributes": {"oauth2.device.authorization.grant.enabled": "true", "pkce.code.challenge.method": "S256"},
            "protocolMappers": [{k: v for k, v in mapper.items() if k != "id"}
                                for mapper in clients["baryon-ax-web"].get("protocolMappers", [])],
        })

    realm["components"] = {
        "org.keycloak.userprofile.UserProfileProvider": [{
            "providerId": "declarative-user-profile",
            "subComponents": {},
            "config": {"kc.user.profile.config": [json.dumps(USER_PROFILE, ensure_ascii=False)]},
        }]
    }

    TARGET.write_text(json.dumps(realm, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
