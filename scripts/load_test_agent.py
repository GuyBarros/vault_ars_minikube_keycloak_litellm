#!/usr/bin/env python3
"""Load-test the AI agent: N loops of "list all users" then "find a random user
by first name", each an independent /v1/agent/query, so traffic fans out over
the mesh (litellm-gateway, token-exchange, opa, user-mcp, ...) for Consul's
Envoy metrics to pick up.

Logs in as a demo user via the Keycloak authorization-code flow (the realm has
direct access grants disabled, so no password grant), then port-forwards to
the ai-agent pod unless --agent-url is given.

    python3 scripts/load_test_agent.py -n 20 -c 4
"""
import argparse
import base64
import hashlib
import html
import json
import random
import re
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTX = "local-minikube-demo"
REDIRECT_URI = "http://localhost:8080/api/auth/callback"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def load_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class TokenSource:
    """Keycloak access token for one demo user, re-login when near expiry."""

    def __init__(self, keycloak: str, realm: str, username: str, password: str):
        env = load_env(ROOT / "deploy-k8s/web-app.env")
        self.client_id = env["KEYCLOAK_CLIENT_ID"]
        self.client_secret = env["KEYCLOAK_CLIENT_SECRET"]
        self.scopes = env["KEYCLOAK_SCOPES"]
        self.base = f"{keycloak}/realms/{realm}/protocol/openid-connect"
        self.username, self.password = username, password
        self._token, self._exp = "", 0.0
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if time.time() > self._exp - 30:
                self._token, self._exp = self._login()
            return self._token

    def _login(self) -> tuple[str, float]:
        verifier = base64.urlsafe_b64encode(hashlib.sha256(str(random.random()).encode()).digest()).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        # Keycloak marks its session cookies Secure even over the http NodePort.
        jar = CookieJar(DefaultCookiePolicy(secure_protocols=("http", "https")))
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)
        auth_url = f"{self.base}/auth?" + urllib.parse.urlencode({
            "client_id": self.client_id, "response_type": "code", "scope": self.scopes,
            "redirect_uri": REDIRECT_URI, "code_challenge": challenge, "code_challenge_method": "S256",
        })
        page = opener.open(auth_url, timeout=30).read().decode()
        form = re.search(r'<form[^>]*action="([^"]+)"', page)
        if not form:
            raise RuntimeError("Keycloak login form not found")
        try:
            opener.open(urllib.request.Request(
                html.unescape(form.group(1)),
                data=urllib.parse.urlencode({"username": self.username, "password": self.password}).encode(),
            ), timeout=30)
            raise RuntimeError("login did not redirect - wrong username/password?")
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location", "")
        code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code")
        if not code:
            raise RuntimeError(f"no auth code in redirect: {location[:200]}")
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code", "code": code[0], "redirect_uri": REDIRECT_URI,
            "client_id": self.client_id, "client_secret": self.client_secret, "code_verifier": verifier,
        }).encode()
        with urllib.request.urlopen(f"{self.base}/token", data=body, timeout=30) as resp:
            tokens = json.load(resp)
        return tokens["access_token"], time.time() + tokens["expires_in"]


def ask(agent_url: str, token: str, prompt: str, timeout: float) -> tuple[float, int]:
    """One agent query; returns (seconds, http status - 0 if unreachable)."""
    req = urllib.request.Request(
        f"{agent_url}/v1/agent/query",
        data=json.dumps({"messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except OSError:
        status = 0
    return time.perf_counter() - start, status


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p / 100 * len(ordered)))]


def report(name: str, results: list[tuple[float, int]]) -> None:
    if not results:
        return
    lat = [r[0] for r in results]
    failed = sum(1 for r in results if r[1] != 200)
    print(f"{name:<12} n={len(lat):<4} failed={failed:<3} "
          f"p50={statistics.median(lat):.2f}s p95={pct(lat, 95):.2f}s p99={pct(lat, 99):.2f}s max={max(lat):.2f}s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--loops", type=int, required=True, help="number of loops (each = list all + one by-name lookup)")
    ap.add_argument("-c", "--concurrency", type=int, default=1, help="loops run in parallel (default 1)")
    ap.add_argument("--username", default="user")
    ap.add_argument("--password", default="user")
    ap.add_argument("--keycloak", default="http://localhost:8081")
    ap.add_argument("--realm", default="demo")
    ap.add_argument("--agent-url", help="skip the automatic port-forward and use this ai-agent URL")
    ap.add_argument("--users-file", default=str(ROOT / "user-mcp/users_repository.json"),
                    help="JSON list of users to draw first names from")
    ap.add_argument("--timeout", type=float, default=120, help="per-query timeout in seconds")
    args = ap.parse_args()

    names = sorted({u["first_name"] for u in json.loads(Path(args.users_file).read_text())})
    tokens = TokenSource(args.keycloak, args.realm, args.username, args.password)
    tokens.get()  # fail fast on bad credentials

    forward = None
    agent_url = args.agent_url
    if not agent_url:
        forward = subprocess.Popen(
            ["kubectl", "--context", CTX, "port-forward", "deploy/ai-agent", "18000:8000"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        agent_url = "http://localhost:18000"
        time.sleep(3)

    list_results: list[tuple[float, int]] = []
    find_results: list[tuple[float, int]] = []

    def one_loop(_: int) -> None:
        list_results.append(ask(agent_url, tokens.get(), "list all users", args.timeout))
        name = random.choice(names)
        find_results.append(ask(agent_url, tokens.get(), f"find the user with first name {name}", args.timeout))

    start = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            list(pool.map(one_loop, range(args.loops)))
    finally:
        if forward:
            forward.terminate()
    elapsed = time.perf_counter() - start

    print(f"\n{args.loops} loops, concurrency {args.concurrency}, {elapsed:.1f}s total")
    report("list all", list_results)
    report("find by name", find_results)


if __name__ == "__main__":
    main()
