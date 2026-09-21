#!/usr/bin/env python3
"""Revoke a Keycloak token (a user's login token or an OBO token) via RFC 7009.

    POST {keycloak}/realms/{realm}/protocol/openid-connect/revoke

Keycloak only lets the client a token was issued to revoke it, so the client is
taken from the token's `azp` claim: `web` for a user's login token,
`token-exchange` for an OBO token. Its secret is read from
infra/local-minikube/generated/keycloak_client_secret_<client>. Before and after,
the token is checked with the introspection endpoint so you can see the effect.

OBO tokens can't go through /revoke: the Keycloak provider strips their `typ`
claim (for Vault), and Keycloak sorts tokens by it, so it answers
`unsupported_token_type`. For those, --end-session ends the Keycloak session the
token belongs to (its `sid`), which invalidates every token of that session,
the user's login token included.

NOTE: revocation is recorded in Keycloak. It is seen by anything that asks
Keycloak (introspection, userinfo), but not by services that verify the JWT
locally from its signature and expiry, such as the LiteLLM PEP, user-mcp or Vault.

    python3 scripts/revoke_keycloak_token.py <token>
    python3 scripts/revoke_keycloak_token.py --token-file obo.txt --end-session
    python3 scripts/revoke_keycloak_token.py --token-file token.txt
    echo "$TOKEN" | python3 scripts/revoke_keycloak_token.py -
"""
import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "infra/local-minikube/generated"


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def client_secret(client_id: str) -> str:
    path = GENERATED / f"keycloak_client_secret_{client_id.replace('-', '_')}"
    if not path.exists():
        sys.exit(f"no secret for client '{client_id}' at {path}; pass --client-id and --client-secret")
    return path.read_text().strip()


def post_form(url: str, form: dict) -> tuple[int, str]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def is_active(base: str, token: str, c: dict) -> str:
    """Introspect as a client named in the token's audience (Keycloak refuses other clients
    with `active: false`), falling back to the client it was issued to."""
    aud = c.get("aud")
    candidates = list(dict.fromkeys([*([aud] if isinstance(aud, str) else aud or []), c.get("azp")]))
    tried = []
    for client_id in filter(None, candidates):
        path = GENERATED / f"keycloak_client_secret_{client_id.replace('-', '_')}"
        if not path.exists():
            continue
        status, body = post_form(f"{base}/protocol/openid-connect/token/introspect",
                                 {"token": token, "client_id": client_id, "client_secret": path.read_text().strip()})
        if status == 200 and json.loads(body).get("active"):
            return f"active (introspected as {client_id})"
        tried.append(client_id)
    return f"NOT active (introspected as {', '.join(tried) or 'no client available'})"


def end_session(url: str, realm: str, sid: str) -> int:
    """Delete the Keycloak session *sid* through the admin API; returns the HTTP status."""
    admin_pw = (GENERATED / "keycloak_admin_password").read_text().strip()
    status, body = post_form(f"{url.rstrip('/')}/realms/master/protocol/openid-connect/token",
                             {"grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": admin_pw})
    if status != 200:
        sys.exit(f"admin login failed: HTTP {status}")
    req = urllib.request.Request(f"{url.rstrip('/')}/admin/realms/{realm}/sessions/{sid}", method="DELETE",
                                 headers={"Authorization": f"Bearer {json.loads(body)['access_token']}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("token", nargs="?", help="the JWT to revoke, or - to read it from stdin")
    ap.add_argument("--token-file", help="read the token from this file")
    ap.add_argument("--hint", default="access_token", choices=["access_token", "refresh_token"],
                    help="token_type_hint sent to Keycloak")
    ap.add_argument("--url", default="http://localhost:8081", help="Keycloak base URL")
    ap.add_argument("--realm", default="demo")
    ap.add_argument("--client-id", help="client to authenticate as (default: the token's azp)")
    ap.add_argument("--client-secret", help="its secret (default: from infra/local-minikube/generated)")
    ap.add_argument("--end-session", action="store_true",
                    help="end the token's whole Keycloak session (its sid) instead of calling /revoke; "
                         "invalidates ALL tokens of that session (the user's login token and its "
                         "OBO tokens share one). Still needs the token: <token>, --token-file or -.")
    args = ap.parse_args()

    if args.token_file:
        token = Path(args.token_file).read_text().strip()
    elif args.token == "-":
        token = sys.stdin.read().strip()
    elif args.token:
        token = args.token.strip()
    else:
        ap.error("give a token, --token-file, or - for stdin")

    c = claims(token)
    client_id = args.client_id or c.get("azp")
    if not client_id:
        sys.exit("the token has no azp claim; pass --client-id")
    secret = args.client_secret or client_secret(client_id)
    base = f"{args.url.rstrip('/')}/realms/{args.realm}"

    kind = "OBO token" if c.get("act") else "user token"
    print(f"{kind}: user={c.get('preferred_username')} client={client_id} acr={c.get('acr')} "
          f"scope={c.get('scope')!r} jti={c.get('jti')} sid={c.get('sid')}")
    print(f"before: {is_active(base, token, c)}")

    if args.end_session:
        if not c.get("sid"):
            sys.exit("the token has no sid claim; nothing to end")
        status = end_session(args.url, args.realm, c["sid"])
        after = is_active(base, token, c)
        # A user's login token and the OBO tokens made from it share one session, so ending
        # it for one leaves nothing to end for the others (404): that's already the goal.
        already = status == 404 and after.startswith("NOT active")
        print(f"end session {c['sid']}: HTTP {status}" + (" (already ended, e.g. via another token of the same login)" if already else ""))
        print(f"after:  {after}")
        sys.exit(0 if status in (200, 204) or already else 1)

    status, body = post_form(f"{base}/protocol/openid-connect/revoke",
                             {"token": token, "token_type_hint": args.hint,
                              "client_id": client_id, "client_secret": secret})
    print(f"revoke: HTTP {status}" + (f" {body}" if body else ""))
    if status != 200:
        if "unsupported_token_type" in body:
            print("hint: OBO tokens have no `typ` claim, so /revoke can't classify them; "
                  "use --end-session to end the session they belong to")
        sys.exit(1)

    print(f"after:  {is_active(base, token, c)}")


if __name__ == "__main__":
    main()
