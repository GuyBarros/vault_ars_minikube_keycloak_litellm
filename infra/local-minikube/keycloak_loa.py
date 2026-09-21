#!/usr/bin/env python3
"""Configure Keycloak's Level of Authentication (LoA) for the demo realm.

Idempotent. Run by keycloak.sh after the realm import:

  * `acr` client scope (Keycloak's ACR mapper) on the `web` client, so login
    tokens carry acr
  * `browser-loa` flow bound as the realm's browser flow:
      Cookie | forms -> LoA 1: Username Password Form
                        LoA 2: OTP Form (only when acr_values=2 is requested)
    Keycloak then issues acr=1 after a password login and acr=2 after a
    step-up login with an OTP (from Vault's TOTP engine, see keycloak.sh).

The `token-exchange` client does not get the `acr` scope: the
keycloak-providers mapper copies the subject token's acr onto exchanged tokens.

    python3 keycloak_loa.py --url http://localhost:18091 --admin-password-file generated/keycloak_admin_password
"""
import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

REALM = "demo"
TOP = "browser-loa"
FORMS = "browser-loa forms"
LOA1, LOA2 = "loa1 condition flow", "loa2 condition flow"


def q(alias: str) -> str:
    return urllib.parse.quote(alias, safe="")


class Keycloak:
    def __init__(self, url: str, password: str):
        self.url = url.rstrip("/")
        body = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                       "username": "admin", "password": password}).encode()
        with urllib.request.urlopen(f"{self.url}/realms/master/protocol/openid-connect/token", body, timeout=30) as resp:
            self.token = json.loads(resp.read())["access_token"]

    def call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            f"{self.url}/admin/realms/{REALM}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def add_execution(self, flow: str, provider: str) -> None:
        self.call("POST", f"/authentication/flows/{q(flow)}/executions/execution", {"provider": provider})

    def add_subflow(self, flow: str, alias: str) -> None:
        self.call("POST", f"/authentication/flows/{q(flow)}/executions/flow",
                  {"alias": alias, "type": "basic-flow", "provider": "registration-page-form", "description": alias})

    def require(self, flow: str, display: str, requirement: str, config: dict | None = None) -> None:
        executions = self.call("GET", f"/authentication/flows/{q(flow)}/executions")
        execution = next(e for e in executions if e["displayName"] == display)
        execution["requirement"] = requirement
        self.call("PUT", f"/authentication/flows/{q(flow)}/executions", execution)
        if config:
            self.call("POST", f"/authentication/executions/{execution['id']}/config",
                      {"alias": f"{flow}-{display}".replace(" ", "-"), "config": config})


def ensure_acr_scope(kc: Keycloak) -> None:
    scopes = {s["name"]: s for s in kc.call("GET", "/client-scopes")}
    if "acr" not in scopes:
        kc.call("POST", "/client-scopes", {
            "name": "acr", "protocol": "openid-connect",
            "attributes": {"include.in.token.scope": "false", "display.on.consent.screen": "false"},
            "protocolMappers": [{
                "name": "acr loa level", "protocol": "openid-connect", "protocolMapper": "oidc-acr-mapper",
                "consentRequired": False,
                "config": {"id.token.claim": "true", "access.token.claim": "true", "introspection.token.claim": "true"},
            }],
        })
        scopes = {s["name"]: s for s in kc.call("GET", "/client-scopes")}
    web = kc.call("GET", "/clients?clientId=web")[0]
    if "acr" not in (web.get("defaultClientScopes") or []):
        kc.call("PUT", f"/clients/{web['id']}/default-client-scopes/{scopes['acr']['id']}")


def ensure_browser_loa_flow(kc: Keycloak) -> None:
    if any(f["alias"] == TOP for f in kc.call("GET", "/authentication/flows")):
        return
    kc.call("POST", "/authentication/flows", {"alias": TOP, "providerId": "basic-flow", "topLevel": True,
                                             "builtIn": False, "description": "LoA 1 password, LoA 2 OTP"})
    kc.add_execution(TOP, "auth-cookie")
    kc.add_subflow(TOP, FORMS)
    kc.require(TOP, "Cookie", "ALTERNATIVE")
    kc.require(TOP, FORMS, "ALTERNATIVE")

    kc.add_subflow(FORMS, LOA1)
    kc.add_subflow(FORMS, LOA2)
    kc.require(FORMS, LOA1, "CONDITIONAL")
    kc.require(FORMS, LOA2, "CONDITIONAL")

    kc.add_execution(LOA1, "conditional-level-of-authentication")
    kc.add_execution(LOA1, "auth-username-password-form")
    kc.require(LOA1, "Condition - Level of Authentication", "REQUIRED",
               {"loa-condition-level": "1", "loa-max-age": "36000"})
    kc.require(LOA1, "Username Password Form", "REQUIRED")

    kc.add_execution(LOA2, "conditional-level-of-authentication")
    kc.add_execution(LOA2, "auth-otp-form")
    kc.require(LOA2, "Condition - Level of Authentication", "REQUIRED",
               {"loa-condition-level": "2", "loa-max-age": "0"})
    kc.require(LOA2, "OTP Form", "REQUIRED")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="Keycloak base URL")
    ap.add_argument("--admin-password-file", required=True)
    args = ap.parse_args()

    keycloak = Keycloak(args.url, Path(args.admin_password_file).read_text().strip())
    ensure_acr_scope(keycloak)
    ensure_browser_loa_flow(keycloak)
    keycloak.call("PUT", "", {"browserFlow": TOP})
    print(f"Keycloak LoA configured: browserFlow={keycloak.call('GET', '')['browserFlow']}")
