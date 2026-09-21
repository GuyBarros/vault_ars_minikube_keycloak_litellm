#!/usr/bin/env python3
"""Demo (CT-04.2): a genuinely expired JWT is refused by the LiteLLM PEP.

  1. temporarily shorten the realm's access-token lifespan (default 30s) and
     enable direct-access grants on the `web` client so a user token can be
     minted from a script (both restored on exit, even on failure)
  2. log in as `writer`, exchange it for an OBO token via token-exchange
  3. control: MCP tools/call list_all_users through LiteLLM with the fresh
     token -> must succeed
  4. wait until the token is past exp + the PEP's 30s leeway
  5. replay the same token -> the PEP must refuse it with "expired"

The token is validly signed the whole time; only the clock changes, so the
refusal is jwt.ExpiredSignatureError in litellm-gateway/pdp_mcp.py (unlike
tampering with exp, which fails on the signature instead). Token-exchange and
tools/call run inside the ai-agent pod, an admitted mesh caller, so the
rejection is the PEP's and not the mesh's.

Talks to Keycloak on localhost:8081 (the api-gateway NodePort the demo tokens'
`iss` already uses) unless --keycloak-url is given.

    python3 scripts/demo_expired_jwt_rejected.py
"""
import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTX = "local-minikube-demo"
REALM = "demo"
PEP_LEEWAY_SECONDS = 30  # KeycloakJwtValidator default in litellm-gateway/pdp_mcp.py

# Runs inside the ai-agent pod (stdlib only): one HTTP POST, printing
# {"http": ..., "body": ...} as the last line. "add_actor" adds the pod's
# Vault-issued actor token to the JSON body (token-exchange needs it).
INCLUSTER_POST = r"""
import json, os, urllib.request, urllib.error
from pathlib import Path
job = json.loads(os.environ["UAT_JOB"])
body = job["body"]
if job.get("add_actor"):
    body["actor_token"] = Path("/vault/secrets/actor-token").read_text().strip()
req = urllib.request.Request(
    job["url"], data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", **job.get("headers", {})},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = {"http": resp.status, "body": resp.read().decode()}
except urllib.error.HTTPError as exc:
    out = {"http": exc.code, "body": exc.read().decode()}
print(json.dumps(out))
"""


def form_post(url: str, form: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


class KeycloakAdmin:
    def __init__(self, base: str, password: str):
        self.base = base.rstrip("/")
        self.token = form_post(
            f"{self.base}/realms/master/protocol/openid-connect/token",
            {"grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": password},
        )["access_token"]

    def call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            f"{self.base}/admin/realms/{REALM}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None


def incluster_post(job: dict) -> dict:
    proc = subprocess.run(
        ["kubectl", "--context", CTX, "exec", "deploy/ai-agent", "-c", "ai-agent", "--",
         "env", f"UAT_JOB={json.dumps(job)}", "python3", "-c", INCLUSTER_POST],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        sys.exit(f"in-cluster call failed: {proc.stderr or proc.stdout}")


def list_all_users(mcp_url: str, token: str) -> dict:
    return incluster_post({
        "url": mcp_url,
        "body": {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "list_all_users", "arguments": {}}},
        "headers": {"Authorization": f"Bearer {token}"},
    })


def refused(result: dict) -> bool:
    """True when the MCP call was refused (HTTP error, JSON-RPC error or isError result)."""
    text = result["body"]
    return result["http"] >= 400 or '"error"' in text or '"isError": true' in text or '"isError":true' in text


def jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keycloak-url", default="http://localhost:8081")
    ap.add_argument("--lifespan", type=int, default=30, help="temporary access-token lifespan, seconds")
    ap.add_argument("--scope", default="users.read")
    ap.add_argument("--user", default="writer")
    ap.add_argument("--password", default="writer")
    ap.add_argument("--mcp-url", default="http://litellm-gateway.virtual.consul:4000/user_mcp/mcp")
    ap.add_argument("--token-exchange-url", default="http://token-exchange.virtual.consul/v1/identity/obo-token")
    args = ap.parse_args()

    admin_pw = (ROOT / "infra/local-minikube/generated/keycloak_admin_password").read_text().strip()
    web_secret = next(line.split("=", 1)[1].strip()
                      for line in (ROOT / "deploy-k8s/web-app.env").read_text().splitlines()
                      if line.startswith("KEYCLOAK_CLIENT_SECRET="))

    kc = KeycloakAdmin(args.keycloak_url, admin_pw)
    web = kc.call("GET", "/clients?clientId=web")[0]
    orig_lifespan = kc.call("GET", "")["accessTokenLifespan"]
    orig_direct = web["directAccessGrantsEnabled"]

    try:
        print(f"Realm accessTokenLifespan {orig_lifespan}s -> {args.lifespan}s (restored on exit)")
        kc.call("PUT", "", {"accessTokenLifespan": args.lifespan})
        if not orig_direct:
            print("Enabling direct-access grants on client `web` (restored on exit)")
            kc.call("PUT", f"/clients/{web['id']}", {**web, "directAccessGrantsEnabled": True})

        subject = form_post(
            f"{args.keycloak_url}/realms/{REALM}/protocol/openid-connect/token",
            {"grant_type": "password", "client_id": "web", "client_secret": web_secret,
             "username": args.user, "password": args.password,
             "scope": "openid profile email Agent.Invoke"},
        )["access_token"]

        obo = incluster_post({"url": args.token_exchange_url, "add_actor": True,
                              "body": {"subject_token": subject, "scope": args.scope}})
        if obo["http"] != 200:
            sys.exit(f"token-exchange failed: HTTP {obo['http']} {obo['body']}")
        obo_token = json.loads(obo["body"])["access_token"]
        claims = jwt_claims(obo_token)
        print(f"\nOBO token: user={claims.get('preferred_username')} scope={claims.get('scope')} "
              f"lifetime={claims['exp'] - claims['iat']}s")

        print("\n=== control: fresh token -> tools/call list_all_users")
        fresh = list_all_users(args.mcp_url, obo_token)
        fresh_ok = not refused(fresh)
        print(f"{'PASS - accepted' if fresh_ok else 'FAIL - refused'}: HTTP {fresh['http']} {fresh['body'][:200]}")

        wait = max(0, claims["exp"] + PEP_LEEWAY_SECONDS + 5 - int(time.time()))
        print(f"\nWaiting {wait}s (exp + {PEP_LEEWAY_SECONDS}s PEP leeway + 5s)...")
        time.sleep(wait)

        print("\n=== replay: same, now-expired token -> tools/call list_all_users")
        expired = list_all_users(args.mcp_url, obo_token)
        ok = refused(expired) and "expire" in expired["body"].lower()
        print(f"{'PASS - rejected as expired' if ok else 'FAIL - not rejected as expired'}: "
              f"HTTP {expired['http']}\n{expired['body']}")
    finally:
        # The admin token (60s) has expired during the wait; log in again to restore.
        kc = KeycloakAdmin(args.keycloak_url, admin_pw)
        kc.call("PUT", "", {"accessTokenLifespan": orig_lifespan})
        if not orig_direct:
            kc.call("PUT", f"/clients/{web['id']}", {**web, "directAccessGrantsEnabled": False})
        print(f"\nRestored accessTokenLifespan={orig_lifespan}s, directAccessGrantsEnabled={orig_direct}")

    sys.exit(0 if fresh_ok and ok else 1)


if __name__ == "__main__":
    main()
