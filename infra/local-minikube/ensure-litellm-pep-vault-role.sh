#!/bin/bash
# Idempotent Vault policy + k8s JWT role so LiteLLM can inject the CIBA
# actor token, and so opa-server can read the MCP catalog + bundle.
# Safe to re-run after bootstrap.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEN_DIR="$SCRIPT_DIR/generated"
PROFILE="local-minikube-demo"
K8S_SA_AUDIENCE="https://kubernetes.default.svc.cluster.local"

[ -f "$GEN_DIR/vault_token" ] || { echo "missing $GEN_DIR/vault_token (run make bootstrap)" >&2; exit 1; }
command -v vault >/dev/null 2>&1 || { echo "missing vault CLI" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "missing jq" >&2; exit 1; }

KC() { kubectl --context "$PROFILE" "$@"; }

mkdir -p "$GEN_DIR"
KC -n vault port-forward svc/vault 18201:8200 >"$GEN_DIR/pf-vault-pep.log" 2>&1 &
PF_PID=$!
cleanup() { kill "$PF_PID" 2>/dev/null || true; }
trap cleanup EXIT

export VAULT_ADDR="https://127.0.0.1:18201"
export VAULT_SKIP_VERIFY=true
export VAULT_TOKEN
VAULT_TOKEN=$(cat "$GEN_DIR/vault_token")

for i in $(seq 1 30); do curl -sk "$VAULT_ADDR/v1/sys/health" >/dev/null 2>&1 && break; sleep 1; done

vault policy write litellm-gateway - <<-EOT
	path "identity/oidc/token/agent-role" {
	  capabilities = ["read"]
	}
	EOT

vault policy write opa - <<-EOT
	path "opa-policies/data/bundle" {
	  capabilities = ["read"]
	}
	path "opa-policies/data/mcp-authz/catalog" {
	  capabilities = ["read"]
	}
	EOT

BUNDLE_DIR="$(cd "$SCRIPT_DIR/../config/opa_policies" && pwd)"
if [ -f "$BUNDLE_DIR/mcp_pep.rego" ]; then
  if ! vault kv patch opa-policies/bundle mcp_pep.rego=@"$BUNDLE_DIR/mcp_pep.rego"; then
    echo "could not patch opa-policies/bundle with mcp_pep.rego (run make configure)" >&2
  fi
fi

jq -n --arg aud "$K8S_SA_AUDIENCE" \
  '{role_type: "jwt", bound_audiences: [$aud], bound_subject: "system:serviceaccount:default:litellm-gateway", claim_mappings: {"/kubernetes.io/namespace": "namespace"}, token_explicit_max_ttl: 0, token_period: 1800, token_policies: ["default", "litellm-gateway"], token_type: "service", user_claim: "sub", user_claim_json_pointer: true}' \
  | vault write auth/k8s_jwt/role/litellm-gateway -

echo "Vault role litellm-gateway ready"
