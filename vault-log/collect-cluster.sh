#!/usr/bin/env bash
# Dump app logs + a mesh/identity snapshot for vault-log (hop viewer).
# Usage: collect-cluster.sh [since]
#   since defaults to 30m (kubectl --since).
set -euo pipefail

SINCE="${1:-30m}"
PROFILE="${PROFILE:-local-minikube-demo}"
export PROFILE
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GEN_DIR="$REPO_ROOT/infra/local-minikube/generated"
export GEN_DIR
OUT="$SCRIPT_DIR/live-dump.txt"
SNAP_JSON="$SCRIPT_DIR/live-snapshot.json"
KC=(kubectl --context "$PROFILE")

WORKLOADS=(
  "default web app"
  "default ai-agent app"
  "default token-exchange app"
  "default litellm-gateway app"
  "default user-mcp app"
  "default ciba-channel app"
  "default keycloak app"
  "opa opa-server app"
  "vault vault vault"
)

SPIFFE_SERVICES=(
  "default/web"
  "default/ai-agent"
  "default/litellm-gateway"
  "default/user-mcp"
  "default/token-exchange"
  "default/ciba-channel"
  "default/keycloak"
  "opa/opa-service"
  "vault/vault"
)

now_iso() { date -u +"%Y-%m-%dT%H:%M:%S.000000+00:00"; }

emit_json() {
  python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1]), separators=(",", ":")))' "$1"
}

{
  echo "### SNAPSHOT"
} >"$OUT"

# --- snapshot: SPIFFE expected IDs, intentions, catalog ---
python3 - "$SNAP_JSON" "$OUT" "${SPIFFE_SERVICES[@]}" <<'PY'
import json, os, pathlib, subprocess, sys, datetime

snap_path, dump_path, *services = sys.argv[1:]
kc = ["kubectl", "--context", os.environ.get("PROFILE", "local-minikube-demo")]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000+00:00")

def run(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)

sys.path.insert(0, os.path.dirname(snap_path))  # vault-log/, for mesh_identity
from mesh_identity import consul_identity, spiffe_id

trust_domain, datacenter = consul_identity(kc, pathlib.Path(os.environ["GEN_DIR"]) / "consul_token")
identities = []
for spec in services:
    ns, svc = spec.split("/", 1)
    spiffe = spiffe_id(trust_domain, datacenter, ns, svc)
    identities.append({
        "service": svc,
        "namespace": ns,
        "spiffe_id": spiffe,
        "mint": "consul-connect SVID (sidecar consul-dataplane)",
    })

code, out, err = run(kc + ["get", "serviceintentions", "-A", "-o", "json"])
intentions = []
if code == 0 and out.strip():
    try:
        doc = json.loads(out)
        for item in doc.get("items") or []:
            spec = item.get("spec") or {}
            dest = spec.get("destination") or {}
            sources = spec.get("sources") or []
            for src in sources:
                intentions.append({
                    "source": f"{src.get('namespace','default')}/{src.get('name')}",
                    "dest": f"{dest.get('namespace','default')}/{dest.get('name')}",
                    "action": src.get("action") or spec.get("action") or "allow",
                })
    except json.JSONDecodeError:
        pass

snapshot = {
    "collected_at": now,
    "trust_domain": trust_domain,
    "identities": identities,
    "intentions": intentions,
    "catalog": None,
    "notes": [
        "SPIFFE IDs are the Consul Connect SVID shape this lab mints (trust domain / namespace / datacenter / service), read from Consul.",
        "pdp_auth.py admits default/web and default/ai-agent from x-mesh-caller-spiffe (Envoy Lua copies uriSanPeerCertificate).",
        "Vault mints Postgres creds (database/creds) and Transform encodings; it does not decide tools/call.",
        "OPA mcp.pep decides catalog + scope + CIBA; LiteLLM pdp_mcp.py enforces and polls Keycloak.",
    ],
}

with open(snap_path, "w", encoding="utf-8") as f:
    json.dump(snapshot, f, indent=2)

with open(dump_path, "a", encoding="utf-8") as f:
    f.write(json.dumps(snapshot, separators=(",", ":")) + "\n")
    f.write("### SOURCE mesh-snapshot\n")
    for ident in identities:
        f.write(json.dumps({
            "event": "mesh_svid",
            "timestamp": now,
            "service": ident["service"],
            "namespace": ident["namespace"],
            "spiffe_id": ident["spiffe_id"],
            "mint": ident["mint"],
            "message": f"Consul Connect SVID for {ident['namespace']}/{ident['service']}",
        }, separators=(",", ":")) + "\n")
    for it in intentions:
        f.write(json.dumps({
            "event": "mesh_intention",
            "timestamp": now,
            "source": it["source"],
            "dest": it["dest"],
            "action": it["action"],
            "message": f"intention {it['action']} {it['source']} -> {it['dest']}",
        }, separators=(",", ":")) + "\n")
PY

# --- optional live catalog from Vault (no secrets) ---
if [[ -f "$GEN_DIR/vault_token" ]]; then
  VAULT_TOKEN="$(cat "$GEN_DIR/vault_token")"
  catalog_raw="$(
    VAULT_ADDR="${VAULT_ADDR:-https://127.0.0.1:8200}" \
    VAULT_SKIP_VERIFY=1 \
    VAULT_TOKEN="$VAULT_TOKEN" \
    vault kv get -format=json opa-policies/mcp-authz/catalog 2>/dev/null || true
  )"
  if [[ -n "$catalog_raw" ]]; then
    python3 - "$OUT" "$SNAP_JSON" "$catalog_raw" <<'PY'
import json, sys, datetime
dump_path, snap_path, raw = sys.argv[1:]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000+00:00")
doc = json.loads(raw)
data = (doc.get("data") or {}).get("data") or doc.get("data") or {}
rules = data.get("rules") or data
with open(dump_path, "a", encoding="utf-8") as f:
    f.write(json.dumps({
        "event": "opa_catalog",
        "timestamp": now,
        "rules": rules,
        "message": "OPA MCP catalog (Vault KV opa-policies/mcp-authz/catalog)",
    }, separators=(",", ":")) + "\n")
try:
    snap = json.loads(open(snap_path, encoding="utf-8").read())
    snap["catalog"] = rules
    open(snap_path, "w", encoding="utf-8").write(json.dumps(snap, indent=2))
except Exception:
    pass
PY
  fi
fi

# --- application logs ---
dump_logs() {
  local ns="$1" deploy="$2" container="$3" source="$4"
  echo "### SOURCE $source" >>"$OUT"
  if "${KC[@]}" -n "$ns" get deploy "$deploy" >/dev/null 2>&1; then
    "${KC[@]}" -n "$ns" logs "deploy/$deploy" -c "$container" --since="$SINCE" --timestamps 2>/dev/null \
      | sed '/^$/d' >>"$OUT" || true
  elif "${KC[@]}" -n "$ns" get sts "$deploy" >/dev/null 2>&1; then
    "${KC[@]}" -n "$ns" logs "sts/$deploy" -c "$container" --since="$SINCE" --timestamps 2>/dev/null \
      | sed '/^$/d' >>"$OUT" || true
  else
    echo "{\"event\":\"collect_skip\",\"source\":\"$source\",\"message\":\"workload $ns/$deploy not found\"}" >>"$OUT"
  fi
}

dump_logs default web web web
dump_logs default ai-agent ai-agent ai-agent
dump_logs default token-exchange token-exchange token-exchange
dump_logs default litellm-gateway litellm-gateway litellm-gateway
dump_logs default user-mcp user-mcp user-mcp
dump_logs default ciba-channel ciba-channel ciba-channel
dump_logs opa opa-server opa opa-server
dump_logs vault vault vault vault

# sidecar SVID / mTLS crumbs (filtered)
echo "### SOURCE consul-dataplane" >>"$OUT"
for spec in "default/web" "default/ai-agent" "default/litellm-gateway" "default/user-mcp"; do
  ns="${spec%%/*}"
  deploy="${spec##*/}"
  echo "# sidecar $spec" >>"$OUT"
  "${KC[@]}" -n "$ns" logs "deploy/$deploy" -c consul-dataplane --since="$SINCE" --timestamps 2>/dev/null \
    | grep -iE 'spiffe://|svid|mtls|uriSan|certificate' \
    | tail -n 80 >>"$OUT" || true
done

echo "Wrote $OUT"
echo "Snapshot $SNAP_JSON"
echo "Open the hop viewer: (cd $SCRIPT_DIR && python3 -m http.server 8753) then http://127.0.0.1:8753/"
