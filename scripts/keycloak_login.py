#!/usr/bin/env python3
"""Log a demo user into the `web` client through Keycloak's browser flow.

Default is a plain password login (LoA 1). With --acr 2 Keycloak steps up to
LoA 2 and asks for the OTP, which this script reads from Vault's TOTP secrets
engine (`vault read totp/code/<user>`), the users' authenticator here.

    python3 scripts/keycloak_login.py writer            # LoA 1
    python3 scripts/keycloak_login.py writer --acr 2    # LoA 2 (password + OTP from Vault)
"""
import argparse
import base64
import hashlib
import html
import json
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KC = "http://localhost:8081"
REDIRECT = "http://localhost:8080/api/auth/callback"


def vault_totp_code(user: str) -> str:
    token = (ROOT / "infra/local-minikube/generated/vault_token").read_text().strip()
    return subprocess.run(
        ["kubectl", "--context", "local-minikube-demo", "-n", "vault", "exec", "vault-0", "-c", "vault", "--",
         "env", f"VAULT_TOKEN={token}", "VAULT_ADDR=https://127.0.0.1:8200", "VAULT_SKIP_VERIFY=true",
         "vault", "read", "-field=code", f"totp/code/{user}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _form_action(page: str) -> str:
    return html.unescape(re.search(r'<form[^>]*action="([^"]+)"', page).group(1))


def login(user: str, password: str, acr: str | None = None, secret: str | None = None) -> dict:
    """Return Keycloak's token response for `web`; acr="2" forces the OTP step-up."""
    secret = secret or next(
        line.split("=", 1)[1].strip()
        for line in (ROOT / "deploy-k8s/web-app.env").read_text().splitlines()
        if line.startswith("KEYCLOAK_CLIENT_SECRET=")
    )
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    # Keycloak marks its cookies Secure even on http://localhost, which Python's
    # cookie jar won't send back (browsers do), so keep the cookies by hand.
    opener = urllib.request.build_opener(_NoRedirect)
    cookies: dict[str, str] = {}
    params = {"client_id": "web", "redirect_uri": REDIRECT, "response_type": "code", "scope": "openid profile email Agent.Invoke",
              "state": "s", "code_challenge": challenge, "code_challenge_method": "S256"}
    if acr:
        params["acr_values"] = acr

    def call(url, form=None):
        req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode() if form else None,
                                     headers={"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())})
        try:
            resp = opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            resp = exc
        for header in resp.headers.get_all("Set-Cookie") or []:
            name, _, rest = header.partition("=")
            cookies[name] = rest.split(";", 1)[0]
        return resp.status if hasattr(resp, "status") else resp.code, resp.read().decode(), resp.headers

    _, page, _ = call(f"{KC}/realms/demo/protocol/openid-connect/auth?{urllib.parse.urlencode(params)}")
    status, page, headers = call(_form_action(page), {"username": user, "password": password})
    if status == 200 and 'name="otp"' in page:
        status, page, headers = call(_form_action(page), {"otp": vault_totp_code(user)})
        if status == 200 and 'name="otp"' in page:
            # Keycloak refuses to reuse a code within its 30s window: wait for the next one.
            time.sleep(31 - time.time() % 30)
            status, page, headers = call(_form_action(page), {"otp": vault_totp_code(user)})
    location = headers.get("Location", "")
    if status != 302 or "code=" not in location:
        text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", page, flags=re.S)
        raise RuntimeError(f"login did not finish (HTTP {status}): {' '.join(text.split())[:300]}")
    code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": "web", "client_secret": secret,
                                   "code": code, "redirect_uri": REDIRECT, "code_verifier": verifier}).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{KC}/realms/demo/protocol/openid-connect/token", data=body), timeout=30) as resp:
        return json.loads(resp.read())


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("user")
    ap.add_argument("--password", help="defaults to the username (demo realm)")
    ap.add_argument("--acr", help="requested LoA, e.g. 2")
    args = ap.parse_args()
    tokens = login(args.user, args.password or args.user, args.acr)
    c = claims(tokens["access_token"])
    print(json.dumps({k: c.get(k) for k in ("preferred_username", "acr", "loa", "azp", "scope")}))
    if "--token" in sys.argv:
        print(tokens["access_token"])
