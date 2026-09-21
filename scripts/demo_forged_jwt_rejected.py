#!/usr/bin/env python3
"""Demo: a forged JWT gets nothing out of the platform.

Crafts a self-signed JWT that *looks* like a real Keycloak access token
(iss/preferred_username/groups all set so it would sail through the local
`groups`-claim checks) but is signed with a throwaway key that Keycloak never
issued, then tries it two ways:

  1. token-exchange directly: POST /v1/identity/obo-token with it as the
     `subject_token`. token-exchange only reads its claims locally; the
     signature is checked by Keycloak during the RFC 8693 exchange, which
     rejects it.

  2. the ai-agent directly: POST /v1/agent/query with it as the Bearer token,
     asking to "list all users". The agent only checks exp/nbf (not the
     signature), so the request is accepted at its edge - but every tool call
     needs an OBO exchange (test 1), which fails, so no user data comes back.
     The test passes when none of the demo users' data appears in the reply.

  3. fake LoA 2: the same kind of forged JWT, this time dressed up as a
     step-up-authenticated writer (acr/loa=2, amr=mfa, users.write) and sent
     with hand-set X-PEP-LoA / X-PEP-Decision headers, calling the MCP
     `create_user` tool through LiteLLM. LoA is the `acr` Keycloak signs into
     the token after an OTP step-up login, so an unsigned claim or a typed
     header proves nothing: the LiteLLM PEP must reject the token's signature. The call is
     made from inside the ai-agent pod, which is an admitted mesh caller, so
     the rejection is the PEP's and not the mesh's.

Port-forwards to the in-cluster Services unless --token-exchange-url /
--agent-url is given. Test 3 always runs via `kubectl exec deploy/ai-agent`.

    python3 scripts/demo_forged_jwt_rejected.py
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]
CTX = "local-minikube-demo"


# Runs inside the ai-agent pod (stdlib only): one MCP tools/call against
# LiteLLM, printing {"http": ..., "body": ...} as the last line.
INCLUSTER_MCP_CALL = r"""
import json, os, urllib.request, urllib.error
job = json.loads(os.environ["UAT_JOB"])
req = urllib.request.Request(
    job["url"],
    data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": job["tool"], "arguments": job["arguments"]}}).encode(),
    headers={"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", **job["headers"]},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = {"http": resp.status, "body": resp.read().decode()}
except urllib.error.HTTPError as exc:
    out = {"http": exc.code, "body": exc.read().decode()}
print(json.dumps(out))
"""


def build_forged_jwt(issuer: str, extra_claims: dict | None = None) -> str:
    """Return a JWT with realistic Keycloak-shaped claims, signed with a
    throwaway key unknown to Keycloak."""
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    claims = {
        "iss": issuer,
        "sub": "forged-subject",
        "preferred_username": "attacker",
        "groups": ["admin"],  # would satisfy token-exchange's local scope check
        "iat": now,
        "exp": now + 300,
        **(extra_claims or {}),
    }
    return jwt.encode(claims, forged_key, algorithm="RS256")


def post_json(url: str, body: dict, headers: dict, timeout: float) -> tuple[int, str]:
    """POST *body* as JSON; returns (http status, response text)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.status, exc.read().decode()


def port_forward(target: str, local_port: int, remote_port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["kubectl", "--context", CTX, "port-forward", target, f"{local_port}:{remote_port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    return proc


def test_token_exchange(base_url: str, forged: str, scope: str) -> bool:
    print(f"\n=== 1. token-exchange: POST {base_url}/v1/identity/obo-token")
    # The actor_token normally identifies a real Vault-issued identity; a
    # placeholder is enough here since the subject_token is what gets rejected.
    status, text = post_json(
        f"{base_url}/v1/identity/obo-token",
        {"subject_token": forged, "actor_token": "forged-actor-token", "scope": scope},
        {},
        timeout=30,
    )
    rejected = status >= 400
    print(f"{'PASS - rejected' if rejected else 'FAIL - accepted'}: HTTP {status}\n{text}")
    return rejected


def test_agent(base_url: str, forged: str, users_file: Path) -> bool:
    print(f"\n=== 2. ai-agent: POST {base_url}/v1/agent/query (\"list all users\")")
    markers = [u["email"] for u in json.loads(users_file.read_text())]
    status, text = post_json(
        f"{base_url}/v1/agent/query",
        {"messages": [{"role": "user", "content": "list all users"}]},
        {"Authorization": f"Bearer {forged}"},
        timeout=120,
    )
    leaked = [m for m in markers if m in text]
    ok = status >= 400 or not leaked
    print(f"{'PASS - no user data returned' if ok else 'FAIL - user data leaked'}: HTTP {status}")
    if status < 400:
        print("(the agent accepts the token at its edge; the tool-call OBO is what fails)")
    print(text)
    return ok


def test_fake_loa2_create_user(mcp_url: str, forged: str) -> bool:
    print(f"\n=== 3. LiteLLM PEP: forged LoA 2 token -> tools/call create_user ({mcp_url})")
    email = f"forged-loa2-{int(time.time())}@example.test"
    job = {
        "url": mcp_url,
        "tool": "create_user",
        "arguments": {"user": {
            "first_name": "Forged", "last_name": "LoA2", "ssn": "000-00-0000",
            "phone": "+1-000-000-0000", "email": email,
            "credit_card_number": "0000-0000-0000-0000", "ip_address": "10.0.0.1",
        }},
        "headers": {
            "Authorization": f"Bearer {forged}",
            # What the PEP injects after a real CIBA approval; an attacker just types them.
            "X-PEP-Decision": "ALLOW",
            "X-PEP-LoA": "2",
            "X-PEP-Required-LoA": "2",
        },
    }
    proc = subprocess.run(
        ["kubectl", "--context", CTX, "exec", "deploy/ai-agent", "-c", "ai-agent", "--",
         "env", f"UAT_JOB={json.dumps(job)}", "python3", "-c", INCLUSTER_MCP_CALL],
        capture_output=True, text=True, timeout=120,
    )
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        print(f"FAIL - could not run the in-cluster call: {proc.stderr or proc.stdout}")
        return False
    status, text = result["http"], result["body"]
    # MCP may answer 200 with a JSON-RPC error / isError result, possibly as SSE.
    refused = '"error"' in text or '"isError": true' in text or '"isError":true' in text
    created = status < 400 and not refused
    ok = not created
    print(f"{'PASS - rejected' if ok else 'FAIL - user created with a forged LoA 2'}: HTTP {status}\n{text}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token-exchange-url", help="token-exchange base URL; skips its port-forward")
    ap.add_argument("--agent-url", help="ai-agent base URL; skips its port-forward")
    ap.add_argument("--issuer", default="http://keycloak:8080/realms/demo",
                    help="iss claim to forge into the token (default matches the demo realm)")
    ap.add_argument("--scope", default="users.read")
    ap.add_argument("--litellm-mcp-url", default="http://litellm-gateway.virtual.consul:4000/user_mcp/mcp",
                    help="LiteLLM user-mcp endpoint as seen from inside the ai-agent pod (test 3)")
    ap.add_argument("--users-file", default=str(ROOT / "user-mcp/users_repository.json"),
                    help="JSON list of users whose emails mark a data leak")
    args = ap.parse_args()

    forged = build_forged_jwt(args.issuer)
    print("Forged token claims (unverified, decoded locally):")
    print(json.dumps(jwt.decode(forged, options={"verify_signature": False}), indent=2))

    forwards = []
    try:
        te_url = args.token_exchange_url
        if not te_url:
            forwards.append(port_forward("svc/token-exchange", 18081, 8080))
            te_url = "http://localhost:18081"
        agent_url = args.agent_url
        if not agent_url:
            forwards.append(port_forward("deploy/ai-agent", 18000, 8000))
            agent_url = "http://localhost:18000"

        results = [
            test_token_exchange(te_url, forged, args.scope),
            test_agent(agent_url, forged, Path(args.users_file)),
            test_fake_loa2_create_user(
                args.litellm_mcp_url,
                build_forged_jwt(args.issuer, {
                    "scope": "users.write",
                    "acr": "2", "loa": 2, "amr": ["pwd", "mfa"],
                }),
            ),
        ]
    finally:
        for proc in forwards:
            proc.terminate()

    print(f"\n{sum(results)}/{len(results)} forged-token attempts blocked")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
