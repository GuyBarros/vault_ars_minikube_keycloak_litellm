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

  3. fake LoA 2: the same kind of forged JWT, this time dressed up as a writer
     who just passed the OTP step-up (acr=2, a fresh acr_time, users.write, amr=mfa),
     carrying a real Keycloak `kid` so it reaches the signature check, and sent
     with hand-set X-PEP-LoA / X-PEP-Decision headers to the MCP `create_user`
     tool through LiteLLM. LoA is the `acr` Keycloak signs into the token after a
     step-up login (password + OTP), so an unsigned claim or a typed header proves
     nothing: the LiteLLM PEP must fail the signature.

  4. tampered LoA: a genuine writer token (acr=1, password login only) with its
     payload edited to acr=2 and a fresh acr_time, signature left as issued.
     The PEP must fail the signature.

  5. genuine LoA 1: the same writer token, untouched. It is validly signed but
     only proves the password, so `create_user` must be refused with a step-up
     requirement (the OPA loa2 rule), not silently allowed.

Tests 3-5 run from inside the ai-agent pod, an admitted mesh caller, so any
refusal is the PEP's and not the mesh's. A test passes only if no user is created.
Tests 4-5 log in as the demo `writer` (password login, no OTP) through Keycloak
on localhost:8081.

Port-forwards to the in-cluster Services unless --token-exchange-url /
--agent-url is given. Tests 3-5 always run via `kubectl exec deploy/ai-agent`.

    python3 scripts/demo_forged_jwt_rejected.py
"""
import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from demo_expired_jwt_rejected import incluster_post
from keycloak_login import claims as jwt_claims, login

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


def build_forged_jwt(issuer: str, extra_claims: dict | None = None, kid: str | None = None) -> str:
    """Return a JWT with realistic Keycloak-shaped claims, signed with a
    throwaway key unknown to Keycloak. *kid* borrows a real key id so a verifier
    looks up Keycloak's real key and fails on the signature itself."""
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    claims = {
        "iss": issuer,
        "sub": "forged-subject",
        "preferred_username": "attacker",
        "groups": ["admin"],  # would satisfy token-exchange's local scope check
        "iat": now,
        "exp": now + 300,
        "acr":2,
        **(extra_claims or {}),
    }
    return jwt.encode(claims, forged_key, algorithm="RS256", headers={"kid": kid} if kid else None)


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


def genuine_writer_obo() -> str:
    """A real OBO token for `writer` with users.write, from a password-only login (acr=1)."""
    subject = login("writer", "writer")["access_token"]
    obo = incluster_post({
        "url": "http://token-exchange.virtual.consul/v1/identity/obo-token", "add_actor": True,
        "body": {"subject_token": subject, "scope": "users.write"},
    })
    if obo["http"] != 200:
        sys.exit(f"could not get a genuine writer OBO token: HTTP {obo['http']} {obo['body']}")
    return json.loads(obo["body"])["access_token"]


def tamper_to_loa2(token: str) -> str:
    """Edit a real token's payload to acr=2 with a fresh acr_time; keep header and signature."""
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims.update({"acr": "2", "acr_time": int(time.time())})
    edited = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return f"{header}.{edited}.{signature}"


def create_user_via_litellm(mcp_url: str, token: str, extra_headers: dict | None = None) -> tuple[int, str, bool]:
    """tools/call create_user through LiteLLM from inside the ai-agent pod.
    Returns (http status, response text, created)."""
    email = f"forged-loa2-{int(time.time())}@example.com"  # a valid domain: a bypass must really create the user
    job = {
        "url": mcp_url,
        "tool": "create_user",
        "arguments": {"user": {"first_name": "Forged", "last_name": "LoA2", "email": email}},
        "headers": {"Authorization": f"Bearer {token}", **(extra_headers or {})},
    }
    proc = subprocess.run(
        ["kubectl", "--context", CTX, "exec", "deploy/ai-agent", "-c", "ai-agent", "--",
         "env", f"UAT_JOB={json.dumps(job)}", "python3", "-c", INCLUSTER_MCP_CALL],
        capture_output=True, text=True, timeout=120,
    )
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        raise RuntimeError(f"could not run the in-cluster call: {proc.stderr or proc.stdout}")
    status, text = result["http"], result["body"]
    # MCP may answer 200 with a JSON-RPC error / isError result, possibly as SSE.
    refused = '"error"' in text or '"isError": true' in text or '"isError":true' in text
    return status, text, status < 400 and not refused and email in text


def test_fake_loa2_create_user(mcp_url: str, forged: str) -> bool:
    print(f"\n=== 3. LiteLLM PEP: forged LoA 2 token -> tools/call create_user ({mcp_url})")
    status, text, created = create_user_via_litellm(mcp_url, forged, {
        # What the PEP itself derives from the token's acr; anyone can type them.
        "X-PEP-Decision": "ALLOW", "X-PEP-LoA": "2", "X-PEP-Required-LoA": "2",
    })
    print(f"{'FAIL - user created with a forged LoA 2' if created else 'PASS - rejected'}: HTTP {status}\n{text}")
    return not created


def test_tampered_loa2(mcp_url: str, genuine: str) -> bool:
    print("\n=== 4. LiteLLM PEP: genuine token edited from acr=1 to acr=2 -> tools/call create_user")
    tampered = tamper_to_loa2(genuine)
    print("edited claims:", {k: v for k, v in jwt_claims(tampered).items() if k in ("preferred_username", "acr", "acr_time")})
    status, text, created = create_user_via_litellm(mcp_url, tampered)
    print(f"{'FAIL - user created with an edited token' if created else 'PASS - rejected'}: HTTP {status}\n{text}")
    return not created


def test_genuine_loa1(mcp_url: str, genuine: str) -> bool:
    print("\n=== 5. LiteLLM PEP: genuine password-only token (acr=1) -> tools/call create_user")
    print("token claims:", {k: v for k, v in jwt_claims(genuine).items() if k in ("preferred_username", "acr", "scope")})
    status, text, created = create_user_via_litellm(mcp_url, genuine)
    ok = not created and "requires LoA 2" in text
    print(f"{'PASS - step-up required' if ok else 'FAIL - not refused with a step-up requirement'}: HTTP {status}\n{text}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token-exchange-url", help="token-exchange base URL; skips its port-forward")
    ap.add_argument("--agent-url", help="ai-agent base URL; skips its port-forward")
    ap.add_argument("--issuer", default="http://localhost:8081/realms/demo",
                    help="iss claim to forge into the token (default matches the demo realm)")
    ap.add_argument("--scope", default="users.read")
    ap.add_argument("--litellm-mcp-url", default="http://litellm-gateway.virtual.consul:4000/user_mcp/mcp",
                    help="LiteLLM user-mcp endpoint as seen from inside the ai-agent pod (tests 3-5)")
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

        genuine = genuine_writer_obo()
        real_kid = jwt.get_unverified_header(genuine)["kid"]
        forged_loa2 = build_forged_jwt(args.issuer, {
            "scope": "users.write", "groups": ["writer"], "preferred_username": "writer",
            "acr": "2", "acr_time": int(time.time()), "amr": ["pwd", "otp"],
        }, kid=real_kid)

        results = [
            test_token_exchange(te_url, forged, args.scope),
            test_agent(agent_url, forged, Path(args.users_file)),
            test_fake_loa2_create_user(args.litellm_mcp_url, forged_loa2),
            test_tampered_loa2(args.litellm_mcp_url, genuine),
            test_genuine_loa1(args.litellm_mcp_url, genuine),
        ]
    finally:
        for proc in forwards:
            proc.terminate()

    print(f"\n{sum(results)}/{len(results)} attempts blocked")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
