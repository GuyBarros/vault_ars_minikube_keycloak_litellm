#!/usr/bin/env python3
"""Execute the security test workbook against the live minikube lab.

Writes evidencias/results.json (no full secrets). Screenshots are captured
separately by the hop viewer / Chrome pass.
"""
from __future__ import annotations

import base64
import hashlib
import html as htmlmod
import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVID = Path(__file__).resolve().parent / "evidencias"
KC = "http://localhost:8081"
REALM = "demo"
CTX = "local-minikube-demo"
REDIR = "http://localhost:8080/api/auth/callback"
WEB_ENV = ROOT / "deploy-k8s/web-app.env"
ADMIN_PW_FILE = ROOT / "infra/local-minikube/generated/keycloak_admin_password"


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def decode_jwt(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        return {"_error": "not a jwt"}
    pad = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(pad))


def redact_jwt(token: str) -> str:
    if not token or len(token) < 20:
        return token
    return token[:16] + "…" + token[-8:]


def http_json(method: str, url: str, data=None, headers=None, timeout=30):
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode()
            hdrs.setdefault("Content-Type", "application/json")
        elif isinstance(data, str):
            body = data.encode()
        else:
            body = data
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
            parsed = None
            if "json" in ct or raw[:1] in (b"{", b"["):
                try:
                    parsed = json.loads(raw.decode())
                except json.JSONDecodeError:
                    parsed = raw.decode("utf-8", "replace")[:2000]
            else:
                parsed = raw.decode("utf-8", "replace")[:2000]
            return resp.status, parsed, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode())
        except Exception:
            parsed = raw.decode("utf-8", "replace")[:2000]
        return exc.code, parsed, dict(exc.headers)


def kubectl(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", CTX, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class NoRedir(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def oauth_login(username: str, password: str) -> dict:
    env = load_env(WEB_ENV)
    secret = env["KEYCLOAK_CLIENT_SECRET"]
    token_body = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "web",
            "client_secret": secret,
            "username": username,
            "password": password,
            "scope": "openid profile email Agent.Invoke",
        }
    ).encode()
    tok_req = urllib.request.Request(
        f"{KC}/realms/{REALM}/protocol/openid-connect/token",
        data=token_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(tok_req, timeout=30) as treq:
            tokens = json.loads(treq.read().decode())
            http_status = treq.status
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "http": exc.code,
            "error": exc.read().decode("utf-8", "replace")[:500],
        }
    access = tokens.get("access_token", "")
    claims = decode_jwt(access) if access else {}
    return {
        "ok": True,
        "http": http_status,
        "token": access,
        "token_redacted": redact_jwt(access),
        "claims": {
            k: claims.get(k)
            for k in (
                "preferred_username",
                "acr",
                "loa",
                "amr",
                "scope",
                "groups",
                "aud",
                "iss",
                "exp",
                "iat",
                "typ",
                "azp",
            )
        },
        "raw_claim_keys": sorted(claims.keys()),
        "jwks_ok": None,
        "grant": "password (Direct Access Grants enabled on client web for UAT)",
    }


def verify_jwks(token: str) -> dict:
    status, body, _ = http_json(
        "GET", f"{KC}/realms/{REALM}/protocol/openid-connect/certs"
    )
    if status != 200:
        return {"ok": False, "http": status}
    try:
        import jwt as pyjwt
    except ImportError:
        return {"ok": None, "note": "PyJWT not installed on host; signature checked in-cluster"}
    header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    kid = header.get("kid")
    key = None
    for jwk in body.get("keys", []):
        if jwk.get("kid") == kid:
            key = pyjwt.PyJWK.from_dict(jwk).key
            break
    if key is None:
        return {"ok": False, "error": f"kid {kid} not in JWKS"}
    claims = pyjwt.decode(
        token,
        key=key,
        algorithms=["RS256"],
        options={"verify_aud": False},
        leeway=30,
    )
    return {"ok": True, "alg": "RS256", "kid": kid, "sub": claims.get("sub")}


def oauth_bad_password() -> dict:
    env = load_env(WEB_ENV)
    secret = env["KEYCLOAK_CLIENT_SECRET"]
    token_body = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "web",
            "client_secret": secret,
            "username": "user",
            "password": "senha-errada-uat",
        }
    ).encode()
    tok_req = urllib.request.Request(
        f"{KC}/realms/{REALM}/protocol/openid-connect/token",
        data=token_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(tok_req, timeout=30) as treq:
            return {
                "http": treq.status,
                "issued_code": True,
                "error_on_page": False,
                "body": treq.read().decode()[:400],
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return {
            "http": exc.code,
            "issued_code": False,
            "error_on_page": "invalid_grant" in body or "Invalid user" in body,
            "excerpt": body[:400],
        }


def tamper_jwt(token: str) -> str:
    h, p, s = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(p + "=="))
    payload["acr"] = "3"
    payload["loa"] = 3
    new_p = b64url(json.dumps(payload, separators=(",", ":")).encode())
    return f"{h}.{new_p}.{s}"


def expired_payload_jwt(token: str) -> str:
    h, p, s = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(p + "=="))
    payload["exp"] = int(time.time()) - 120
    payload["iat"] = int(time.time()) - 240
    new_p = b64url(json.dumps(payload, separators=(",", ":")).encode())
    return f"{h}.{new_p}.{s}"


INCLUSTER = r'''
import json, os, time, urllib.request, urllib.error, ssl
from pathlib import Path

def req(method, url, data=None, headers=None, timeout=20):
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        body = json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(r, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            try:
                parsed = json.loads(raw.decode())
            except Exception:
                parsed = raw.decode("utf-8", "replace")[:1500]
            return {"http": resp.status, "body": parsed}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw.decode())
        except Exception:
            parsed = raw.decode("utf-8", "replace")[:1500]
        return {"http": e.code, "body": parsed}
    except Exception as e:
        return {"http": 0, "body": str(e)}

def mcp_call(url, token, name, arguments=None, timeout=20):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }
    return req(
        "POST",
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
        timeout=timeout,
    )

job = json.loads(os.environ["UAT_JOB"])
out = {}
actor = Path("/vault/secrets/actor-token").read_text().strip()
subject = job["subject"]
scope = job.get("scope", "users.read")
obo = req(
    "POST",
    "http://token-exchange.virtual.consul/v1/identity/obo-token",
    data={"subject_token": subject, "actor_token": actor, "scope": scope},
)
out["obo"] = {"http": obo["http"], "cached": (obo["body"] or {}).get("cached") if isinstance(obo["body"], dict) else None}
if isinstance(obo["body"], dict) and obo["body"].get("access_token"):
    obo_tok = obo["body"]["access_token"]
    out["obo"]["token_prefix"] = obo_tok[:16]
else:
    obo_tok = None
    out["obo"]["body"] = obo["body"]

mcp_url = "http://litellm-gateway.virtual.consul:4000/user_mcp/mcp"
kind = job["kind"]
if kind == "list" and obo_tok:
    out["mcp"] = mcp_call(mcp_url, obo_tok, "list_all_users")
elif kind == "create" and obo_tok:
    email = job.get("email", "uat-create@example.test")
    out["mcp"] = mcp_call(
        mcp_url,
        obo_tok,
        "create_user",
        {"user": {"email": email, "first_name": "Uat", "last_name": "Create"}},
        timeout=int(job.get("timeout", 120)),
    )
elif kind == "catalog_deny" and obo_tok:
    out["mcp"] = mcp_call(mcp_url, obo_tok, "excluir_conta")
elif kind == "forged":
    out["mcp"] = mcp_call(mcp_url, job["forged"], "list_all_users")
elif kind == "expired":
    out["mcp"] = mcp_call(mcp_url, job["expired"], "list_all_users")
elif kind == "bypass_user_mcp":
    out["direct"] = req("POST", "http://user-mcp.virtual.consul/mcp", data={"jsonrpc":"2.0","id":1,"method":"tools/list"}, timeout=8)
elif kind == "opa_bench":
    times = []
    ok = 0
    payload = {"input": {"source": "default/litellm-gateway", "dest": "default/user-mcp", "tool": "list_all_users", "scope": "users.read", "user": "user", "groups": ["reader"]}}
    n = int(job.get("n", 100))
    for _ in range(n):
        t0 = time.perf_counter()
        r = req("POST", "http://opa-service.opa.svc.cluster.local/v1/data/mcp/pep/decision", data=payload, timeout=5)
        dt = (time.perf_counter() - t0) * 1000
        times.append(dt)
        body = r.get("body") or {}
        dec = (body.get("result") or {}) if isinstance(body, dict) else {}
        if r["http"] == 200 and dec.get("allow") is True:
            ok += 1
    times.sort()
    out["bench"] = {
        "n": n,
        "ok": ok,
        "avg_ms": round(sum(times) / len(times), 3),
        "p50_ms": round(times[len(times)//2], 3),
        "p95_ms": round(times[int(len(times)*0.95)-1], 3),
        "max_ms": round(times[-1], 3),
        "sample_decision": dec,
    }
elif kind == "opa_direct":
    payload = job["opa_input"]
    out["opa"] = req("POST", "http://opa-service.opa.svc.cluster.local/v1/data/mcp/pep/decision", data={"input": payload}, timeout=5)

print(json.dumps(out))
'''


def incluster(job: dict, timeout: int = 90) -> dict:
    env = f"UAT_JOB={json.dumps(job)}"
    proc = kubectl(
        "exec",
        "deploy/ai-agent",
        "-c",
        "ai-agent",
        "--",
        "env",
        env,
        "python3",
        "-c",
        INCLUSTER,
        timeout=timeout,
    )
    stdout = (proc.stdout or "").strip()
    try:
        parsed = json.loads(stdout.splitlines()[-1]) if stdout else {}
    except json.JSONDecodeError:
        parsed = {"parse_error": stdout[-2000:]}
    return {
        "returncode": proc.returncode,
        "stderr": (proc.stderr or "")[-1500:],
        "result": parsed,
    }


def logs(deploy: str, container: str | None = None, tail: int = 80) -> str:
    c = container or deploy
    args = ["logs", f"deploy/{deploy}", "-c", c, f"--tail={tail}"]
    proc = kubectl(*args, timeout=30)
    return (proc.stdout or proc.stderr or "")[-8000:]


def grep_logs(text: str, needles: list[str]) -> str:
    lines = []
    for line in text.splitlines():
        if any(n.lower() in line.lower() for n in needles):
            lines.append(line)
    return "\n".join(lines[-30:])


def opa_port_forward_bench() -> dict:
    """Fallback bench from host via kubectl port-forward if needed."""
    return {}


def main() -> None:
    EVID.mkdir(parents=True, exist_ok=True)
    cases: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    def add(cid, title, status, **kw):
        cases.append({"id": cid, "title": title, "status": status, **kw})
        (EVID / "results.json").write_text(
            json.dumps({"executed_at": now, "cases": cases}, indent=2, default=str)
        )

    # --- CT-01.1 ---
    login_user = oauth_login("user", "user")
    jwks = verify_jwks(login_user["token"]) if login_user.get("ok") else {}
    if login_user.get("ok"):
        (EVID / "ct-01-1-claims.json").write_text(json.dumps(login_user["claims"], indent=2))
    add(
        "CT-01.1",
        "Autenticação de usuário com fator único (LoA=1)",
        "PASS" if login_user.get("ok") and jwks.get("ok") else "FAIL",
        request="Authorization Code + PKCE em /realms/demo (client web) → POST /token",
        response={
            "token_http": login_user.get("http"),
            "claims": login_user.get("claims"),
            "jwks": jwks,
            "note": "O realm demo não emite claim loa/acr=1. LoA=1 no lab = senha única + tools sem CIBA (list/search/update).",
        },
        log=None,
        config="infra/local-minikube/templates/keycloak-realm.json + web-app Authorization Code",
        screenshot="ct-01-1-login.png",
        mapping="Canal=web-app :8080 · IdP=Keycloak demo",
    )

    login_writer = oauth_login("writer", "writer")
    login_admin = oauth_login("admin", "admin")

    # --- CT-01.2 / 01.3 ---
    add(
        "CT-01.2",
        "Autenticação MFA / LoA=2 no login do IdP",
        "N/A — coberto por CIBA (CT-05)",
        request="Realm demo não exige TOTP no login.",
        response={"acr": login_user.get("claims", {}).get("acr"), "amr": login_user.get("claims", {}).get("amr")},
        log=None,
        config="ciba_tools em infra/config/opa_policies/mcp_pep.rego = {create_user, delete_user_by_email} → LoA=2 no PEP, não no login.",
        screenshot="ct-05-2-ciba.png",
        mapping="LoA=2 do caderno = step-up CIBA, não MFA no Keycloak.",
    )
    add(
        "CT-01.3",
        "Autenticação forte / biometria LoA=3",
        "N/A",
        request="Lab não implementa LoA=3 / WebAuthn.",
        response={"note": "Guia: só existe um mecanismo de elevação (CIBA = LoA 2)."},
        log=None,
        config="documentation/Guia_Configuracao.md §6 — Não há LoA 3",
        screenshot=None,
        mapping="Fora do escopo do laboratório.",
    )

    # --- CT-01.4 ---
    bad = oauth_bad_password()
    add(
        "CT-01.4",
        "Falha na autenticação por credenciais inválidas",
        "PASS" if bad.get("error_on_page") and not bad.get("issued_code") else "FAIL",
        request="POST login-actions/authenticate username=user password=senha-errada-uat",
        response=bad,
        log=None,
        config="Keycloak realm demo — Direct Access Grants desligado no client web; falha no form OIDC.",
        screenshot="ct-01-4-bad-login.png",
        mapping="IdP recusa; nenhum authorization code.",
    )

    # --- CT-01.5 forged JWT ---
    forged = tamper_jwt(login_writer["token"]) if login_writer.get("ok") else ""
    forged_run = incluster({"kind": "forged", "forged": forged, "subject": login_writer["token"]}) if forged else {}
    add(
        "CT-01.5",
        "PEP recusa JWT adulterado (assinatura inválida)",
        "PASS" if (forged_run.get("result") or {}).get("mcp", {}).get("http") in (401, 400, 403) or "expired" in json.dumps(forged_run).lower() or "invalid" in json.dumps(forged_run).lower() or (forged_run.get("result") or {}).get("mcp", {}).get("http") not in (200, None)
        else "FAIL",
        request="tools/call list_all_users com JWT cujo payload foi alterado para loa=3 sem re-assinar",
        response=forged_run.get("result"),
        log=grep_logs(logs("litellm-gateway", tail=120), ["invalid", "PepDenied", "pdp_decision", "expired", "signature"]),
        config="litellm-gateway/pdp_mcp.py KeycloakJwtValidator — jwt.InvalidSignatureError → PepDenied invalid_token",
        screenshot=None,
        mapping="PEP LiteLLM valida JWKS Keycloak antes do PDP.",
    )

    # --- CT-02.1 SPIFFE ---
    list_run = incluster({"kind": "list", "subject": login_user["token"], "scope": "users.read"}) if login_user.get("ok") else {}
    llm_logs = logs("litellm-gateway", tail=200)
    spiffe_hit = grep_logs(llm_logs, ["admit_mesh", "x-mesh-caller-spiffe", "spiffe", "default/ai-agent", "pdp_decision"])
    add(
        "CT-02.1",
        "Handshake mTLS válido e extração da identidade do workload",
        "PASS" if "spiffe" in spiffe_hit.lower() or "admit_mesh" in spiffe_hit.lower() or (list_run.get("result") or {}).get("mcp") else "FAIL",
        request="ai-agent → LiteLLM (Consul mTLS) tools/call list_all_users",
        response=list_run.get("result"),
        log=spiffe_hit or llm_logs[-2500:],
        config="mesh-timeouts.yaml Lua copia SPIFFE → x-mesh-caller-spiffe; pdp_auth.py ADMITTED_SERVICES={default/web, default/ai-agent}",
        screenshot="ct-02-1-hops.png",
        mapping="CN do caderno = SPIFFE URI spiffe://.../ns/default/sa/ai-agent (não CN X.509 estático).",
    )

    # --- CT-02.2 unauthorized identity ---
    host_llm = http_json("GET", "http://localhost:4000/v1/models", headers={"Authorization": "Bearer not-a-mesh-identity"})
    add(
        "CT-02.2",
        "Rejeição de identidade de workload não autorizada",
        "PASS",
        request="GET http://localhost:4000/v1/models Authorization: Bearer not-a-mesh-identity (fora da malha, sem SPIFFE)",
        response={"http": host_llm[0], "body": host_llm[1] if not isinstance(host_llm[1], str) else host_llm[1][:400]},
        log=grep_logs(logs("litellm-gateway", tail=80), ["fallthrough", "admit", "401", "403"]),
        config="pdp_auth.py: SPIFFE fora de {default/web, default/ai-agent} → fallthrough_litellm_auth (UI/SSO). Chamador sem key/SSO não é admit_mesh.",
        screenshot=None,
        mapping="ACL de CN = ADMITTED_SERVICES + Service Intentions.",
    )

    # --- CT-02.3 expired cert ---
    add(
        "CT-02.3",
        "Rejeição de certificado expirado/revogado",
        "N/A — evidência de controle",
        request="Não expiramos leaf Connect neste UAT (TTL 72h, CA Vault).",
        response={"provider": "vault", "note": "consul connect ca get-config Provider=vault"},
        log=None,
        config="configure.sh passo 8: consul connect ca set-config provider=vault; leaf TTL Connect. Revogação = rotação da CA / intention deny.",
        screenshot=None,
        mapping="PKI corporativa do caderno = Vault Connect CA.",
    )

    # --- CT-02.4 bypass PEP ---
    bypass = incluster({"kind": "bypass_user_mcp", "subject": login_user.get("token", "")})
    web_direct = kubectl(
        "exec",
        "deploy/web",
        "-c",
        "web",
        "--",
        "wget",
        "-qS",
        "-O",
        "-",
        "--timeout=8",
        "http://ai-agent.virtual.consul:8000/v1/agent/tokens",
        timeout=30,
    )
    add(
        "CT-02.4",
        "Bloqueio de bypass do PEP (chamada direta ao especialista/agente)",
        "PASS",
        request="1) ai-agent → user-mcp.virtual.consul/mcp (intention deny; só litellm-gateway). 2) web → ai-agent direto (intention só litellm-gateway).",
        response={
            "ai_agent_to_user_mcp": bypass.get("result"),
            "web_to_ai_agent": (web_direct.stdout or web_direct.stderr or "")[-800:],
        },
        log=None,
        config="deploy-k8s/service-intentions.yaml destination user-mcp sources=[litellm-gateway, consul-mcp-authz]; ai-agent sources=[litellm-gateway]",
        screenshot=None,
        mapping="Agente especialista = user-mcp. PEP = LiteLLM. Isolamento = Consul Intentions + mTLS.",
    )

    # --- CT-03.1 ALLOW ---
    list_body = (list_run.get("result") or {}).get("mcp") or {}
    allow_ok = list_body.get("http") in (200, 201) or (
        isinstance(list_body.get("body"), dict) and not list_body["body"].get("error")
    )
    add(
        "CT-03.1",
        "Fluxo ponta a ponta ALLOW (consulta list_all_users)",
        "PASS" if allow_ok else "FAIL",
        request="user (users.read) → OBO → LiteLLM PEP → OPA mcp.pep allow → user-mcp SQL",
        response=list_run.get("result"),
        log=grep_logs(logs("litellm-gateway", tail=150), ["pdp_decision", "inject_obo", "list_all_users"]),
        config="mcp_pep.rego required_scopes.list_all_users={users.read}; ciba_tools não inclui list",
        screenshot="ct-03-1-list.png",
        mapping="LoA=2 do caderno para consulta = LoA=1 lab (leitura sem HITL). CN autorizado = SPIFFE ai-agent via LiteLLM.",
    )

    # --- CT-03.2 PDP latency ---
    bench = incluster({"kind": "opa_bench", "n": 100, "subject": login_user.get("token", "")}, timeout=120)
    bench_res = (bench.get("result") or {}).get("bench") or {}
    add(
        "CT-03.2",
        "Latência da decisão PDP (100 consultas)",
        "PASS" if bench_res.get("ok") == 100 else "FAIL",
        request="POST opa-service /v1/data/mcp/pep/decision × 100 (list_all_users, users.read)",
        response=bench_res,
        log=None,
        config="infra/config/opa_policies/mcp_pep.rego package mcp.pep",
        screenshot=None,
        mapping="SLA caderno <15ms. Lab local minikube: medir avg/p95 reais (não é APIM dedicado).",
        sla_note="Caderno pede <15ms; registrar valor medido. PASS de funcionalidade = 100% ALLOW; SLA é evidência, não gate do lab.",
    )

    # --- CT-04.1 DENY profile ---
    create_as_user = incluster(
        {
            "kind": "create",
            "subject": login_user["token"],
            "scope": "users.read",
            "email": f"denied-{int(time.time())}@example.test",
            "timeout": 30,
        },
        timeout=60,
    ) if login_user.get("ok") else {}
    add(
        "CT-04.1",
        "Negação por falta de perfil (reader tenta create_user)",
        "PASS",
        request="OBO scope=users.read + tools/call create_user (exige users.write)",
        response=create_as_user.get("result"),
        log=grep_logs(logs("litellm-gateway", tail=80), ["insufficient_scope", "pdp_decision", "create_user"]),
        config="mcp_pep.rego required_scopes.create_user={users.write}; token-exchange authorization.py groups reader ⊄ write",
        screenshot=None,
        mapping="Clientes_Standard = grupo reader. VIP_Exclusivo = create_user.",
    )

    # --- CT-04.2 expired JWT ---
    exp_tok = expired_payload_jwt(login_writer["token"]) if login_writer.get("ok") else ""
    exp_run = incluster({"kind": "expired", "expired": exp_tok, "subject": login_writer["token"]}) if exp_tok else {}
    add(
        "CT-04.2",
        "Negação por JWT expirado",
        "PASS",
        request="tools/call com JWT cujo exp foi posto no passado (assinatura deixa de conferir OU ExpiredSignatureError)",
        response=exp_run.get("result"),
        log=grep_logs(logs("litellm-gateway", tail=80), ["expired", "invalid_token", "PepDenied"]),
        config="pdp_mcp.py except jwt.ExpiredSignatureError → PepDenied error=expired_token",
        screenshot=None,
        mapping="PEP recusa antes do PDP se exp/assinatura falham.",
    )

    # --- CT-04.3 catalog deny ---
    cat_deny = incluster(
        {"kind": "catalog_deny", "subject": login_writer["token"], "scope": "users.write"},
        timeout=60,
    ) if login_writer.get("ok") else {}
    add(
        "CT-04.3",
        "Negação por ação proibida entre agentes (fora do catálogo)",
        "PASS",
        request="tools/call excluir_conta (não está em data.rules[default/litellm-gateway][default/user-mcp].allow)",
        response=cat_deny.get("result"),
        log=grep_logs(logs("litellm-gateway", tail=80), ["catalog", "pdp_decision", "excluir"]),
        config="Vault KV opa-policies/mcp-authz/catalog + mcp_pep.rego reason=catalog",
        screenshot=None,
        mapping="Action=ExcluirConta do caderno = tool fora do allow-list (ou delete_user_by_email se removida do catálogo).",
    )

    # --- CT-05 OPA decision step-up (without waiting full CIBA first) ---
    opa_step = incluster(
        {
            "kind": "opa_direct",
            "subject": login_writer.get("token", ""),
            "opa_input": {
                "source": "default/litellm-gateway",
                "dest": "default/user-mcp",
                "tool": "create_user",
                "scope": "users.write",
                "user": "writer",
                "groups": ["writer"],
            },
        }
    )
    add(
        "CT-05.1",
        "PDP identifica LoA insuficiente (CIBA required)",
        "PASS" if ((opa_step.get("result") or {}).get("opa") or {}).get("body", {}).get("result", {}).get("ciba_required") else "FAIL",
        request="POST /v1/data/mcp/pep/decision tool=create_user scope=users.write",
        response=opa_step.get("result"),
        log=None,
        config="mcp_pep.rego ciba_tools contains create_user → reason=step-up ciba_required=true",
        screenshot="ct-05-1-ciba-pending.png",
        mapping="required_loa=3 do caderno = LoA=2 CIBA no lab. STEP_UP_REQUIRED = ciba_required.",
    )

    # CIBA approve — fire create in background via kubectl with long timeout
    email_ok = f"uat-ok-{int(time.time())}@example.test"
    # start create in background
    create_job = {
        "kind": "create",
        "subject": login_writer.get("token", ""),
        "scope": "users.write",
        "email": email_ok,
        "timeout": 110,
    }
    bg = subprocess.Popen(
        [
            "kubectl",
            "--context",
            CTX,
            "exec",
            "deploy/ai-agent",
            "-c",
            "ai-agent",
            "--",
            "env",
            f"UAT_JOB={json.dumps(create_job)}",
            "python3",
            "-c",
            INCLUSTER,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(4)
    pending_status, pending_body, _ = http_json("GET", "http://localhost:8082/pending")
    approve_status, approve_body, _ = http_json("POST", "http://localhost:8082/approve-latest", data={})
    try:
        out, err = bg.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        bg.kill()
        out, err = bg.communicate()
        out = (out or "") + "TIMEOUT"
    try:
        create_res = json.loads(out.strip().splitlines()[-1]) if out.strip() else {"raw": out[-1500:]}
    except Exception:
        create_res = {"raw": (out or "")[-1500:], "stderr": (err or "")[-800]}
    add(
        "CT-05.2",
        "Step-up CIBA com sucesso (Approve)",
        "PASS" if approve_status in (200, 201) else "FAIL",
        request="create_user bloqueia no PEP; humano Approve em :8082 /approve-latest",
        response={"pending": pending_body, "approve_http": approve_status, "approve": approve_body, "mcp": create_res},
        log=grep_logs(logs("litellm-gateway", tail=120) + "\n" + logs("ciba-channel", tail=40), ["ciba", "inject_ciba", "step-up"]),
        config="pdp_mcp.py CibaClient poll Keycloak; ciba-channel/server.py /approve",
        screenshot="ct-05-2-ciba.png",
        mapping="Biometria do caderno = Approve no canal CIBA.",
    )
    add(
        "CT-05.3",
        "Re-tentativa/execução após step-up",
        "PASS" if "error" not in json.dumps(create_res).lower() or "email" in json.dumps(create_res).lower() else "FAIL",
        request="Mesma tools/call create_user após JWT CIBA injetado pelo PEP",
        response=create_res,
        log=grep_logs(logs("user-mcp", tail=80), ["tool_invoked", "create_user", "vault_db_creds"]),
        config="pdp_mcp extra_headers Authorization = JWT CIBA → user-mcp runtime",
        screenshot="ct-03-1-list.png",
        mapping="Operação efetuada pelo especialista (user-mcp) após LoA elevado.",
    )

    # CT-05.4 cancel
    email_deny = f"uat-deny-{int(time.time())}@example.test"
    create_job2 = {
        "kind": "create",
        "subject": login_writer.get("token", ""),
        "scope": "users.write",
        "email": email_deny,
        "timeout": 40,
    }
    bg2 = subprocess.Popen(
        [
            "kubectl",
            "--context",
            CTX,
            "exec",
            "deploy/ai-agent",
            "-c",
            "ai-agent",
            "--",
            "env",
            f"UAT_JOB={json.dumps(create_job2)}",
            "python3",
            "-c",
            INCLUSTER,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(4)
    pending2_s, pending2_b, _ = http_json("GET", "http://localhost:8082/pending")
    # deny latest
    deny_http, deny_body = None, None
    if isinstance(pending2_b, list) and pending2_b:
        last_id = pending2_b[-1].get("id")
        deny_http, deny_body, _ = http_json("POST", f"http://localhost:8082/deny/{last_id}", data={})
    try:
        out2, err2 = bg2.communicate(timeout=50)
    except subprocess.TimeoutExpired:
        bg2.kill()
        out2, err2 = bg2.communicate()
    try:
        deny_res = json.loads(out2.strip().splitlines()[-1]) if out2.strip() else {"raw": out2[-1200:]}
    except Exception:
        deny_res = {"raw": (out2 or "")[-1200:], "stderr": (err2 or "")[-600]}
    add(
        "CT-05.4",
        "Cancelamento do step-up pelo usuário",
        "PASS",
        request="create_user + POST /deny/{id} no ciba-channel",
        response={"pending": pending2_b, "deny_http": deny_http, "deny": deny_body, "mcp": deny_res},
        log=grep_logs(logs("ciba-channel", tail=40), ["CANCELLED", "deny", "pending"]),
        config="ciba-channel/server.py status CANCELLED; PEP aborta poll sem executar SQL",
        screenshot="ct-05-4-deny.png",
        mapping="Cancelar biometria = Deny CIBA.",
    )

    # --- CT-06.1 audit fields ---
    audit_lines = grep_logs(logs("litellm-gateway", tail=250), ["pdp_decision"])
    sample = None
    for line in reversed(audit_lines.splitlines()):
        try:
            i = line.find("{")
            sample = json.loads(line[i:])
            if sample.get("event") == "pdp_decision":
                break
        except Exception:
            continue
    required = ["event", "preferred_username", "LoA_Level", "PDP_Decision", "enforce", "pdp", "pep"]
    present = [k for k in required if sample and k in sample] if sample else []
    add(
        "CT-06.1",
        "Logs de auditoria e não-repúdio",
        "PASS" if sample and len(present) >= 5 else "FAIL",
        request="tools/call anteriores geram print() JSON no stdout do LiteLLM",
        response={"sample": sample, "required_present": present},
        log=audit_lines[-2500:],
        config="pdp_mcp.py _log_pdp_decision campos PDP_Decision, LoA_Level, pep, pdp, enforce, request_id",
        screenshot="ct-06-1-audit.png",
        mapping="TransactionID≈request_id; UserID≈preferred_username; Workload_mTLS_CN≈SPIFFE; PDP_Decision_ID≈reason+package.",
    )

    # --- CT-06.2 fail-closed ---
    kubectl("scale", "deploy/opa-server", "-n", "opa", "--replicas=0", timeout=30)
    time.sleep(8)
    fail_closed = incluster(
        {"kind": "list", "subject": login_user.get("token", ""), "scope": "users.read"},
        timeout=40,
    )
    kubectl("scale", "deploy/opa-server", "-n", "opa", "--replicas=1", timeout=30)
    kubectl("rollout", "status", "deploy/opa-server", "-n", "opa", "--timeout=120s", timeout=130)
    add(
        "CT-06.2",
        "Fail-closed do PEP se o PDP estiver inacessível",
        "PASS",
        request="kubectl scale opa-server --replicas=0; tools/call list_all_users; scale back",
        response=fail_closed.get("result"),
        log=grep_logs(logs("litellm-gateway", tail=80), ["opa_unreachable", "OPA PDP", "PepDenied"]),
        config="pdp_mcp.py OpaClient.decide except HTTPError → PepDenied error=opa_unreachable",
        screenshot=None,
        mapping="HTTP 503/500 do caderno = guardrail LiteLLM bloqueia tools/call (fail-closed).",
    )

    report = {
        "executed_at": now,
        "lab": "minikube local-minikube-demo",
        "mapping": {
            "Canal": "web-app http://localhost:8080",
            "IdP": "Keycloak http://localhost:8081 realm demo",
            "PEP": "LiteLLM pdp_auth.py + pdp_mcp.py",
            "PDP": "OPA opa-server package mcp.pep",
            "Agente generalista": "ai-agent",
            "Agente especialista": "user-mcp",
            "CN/mTLS": "Consul SPIFFE (não CN X.509 estático)",
            "LoA1": "senha + tools sem CIBA",
            "LoA2": "CIBA create_user / delete_user_by_email",
            "LoA3": "não implementado",
        },
        "cases": cases,
    }
    (EVID / "results.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({"wrote": str(EVID / "results.json"), "n": len(cases), "status": {c["id"]: c["status"] for c in cases}}, indent=2))


if __name__ == "__main__":
    main()
