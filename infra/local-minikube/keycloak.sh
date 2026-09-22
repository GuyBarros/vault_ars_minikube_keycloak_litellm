#!/bin/bash
# Deploys Keycloak (with the custom keycloak-providers/ SPI baked in) and
# then configures Vault's Keycloak-facing trust: a jwt-keycloak JWT auth
# mount (used to validate a completed step-up) plus Vault 2.1's native
# OAuth Resource Server + Agent Registry, which is what actually authorizes
# database/creds and Transform calls now (see infra/local-minikube/README.md).
#
# Run after bootstrap.sh and configure.sh (needs the shared postgres-0
# StatefulSet and the Vault/Consul tokens configure.sh's port-forwards use).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$INFRA_DIR")"
DEPLOY_DIR="$REPO_ROOT/deploy-k8s"
GEN_DIR="$SCRIPT_DIR/generated"
PROFILE="local-minikube-demo"
KEYCLOAK_ISSUER="http://localhost:8081/realms/demo"
# Routed through the API Gateway, not keycloak's own Service: Vault has no
# Consul Connect sidecar, so keycloak's mesh-enforced inbound (see
# service-intentions.yaml) silently drops it. The gateway is the one
# mesh-authorized front door built to accept plaintext, non-mesh callers
# (see keycloak-gateway.yaml) — Vault is just another one, same as browsers.
KEYCLOAK_INTERNAL_JWKS="http://keycloak-api-gateway.default.svc.cluster.local/realms/demo/protocol/openid-connect/certs"

for bin in minikube kubectl docker vault jq openssl curl; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 1; }
done

[ -f "$GEN_DIR/vault_token" ] || { echo "run bootstrap.sh first (missing $GEN_DIR/vault_token)" >&2; exit 1; }

mkdir -p "$GEN_DIR"
KC() { kubectl --context "$PROFILE" "$@"; }

# ---- 1. Generate secrets once, reused across reruns ----
gen_secret() {
  local file="$1"
  [ -f "$file" ] || openssl rand -hex 24 > "$file"
  cat "$file"
}
KEYCLOAK_ADMIN_PASSWORD=$(gen_secret "$GEN_DIR/keycloak_admin_password")
KEYCLOAK_DB_PASSWORD=$(gen_secret "$GEN_DIR/keycloak_db_password")
WEB_CLIENT_SECRET=$(gen_secret "$GEN_DIR/keycloak_client_secret_web")
TOKEN_EXCHANGE_CLIENT_SECRET=$(gen_secret "$GEN_DIR/keycloak_client_secret_token_exchange")
USER_MCP_CLIENT_SECRET=$(gen_secret "$GEN_DIR/keycloak_client_secret_user_mcp")
LITELLM_CLIENT_SECRET=$(gen_secret "$GEN_DIR/keycloak_client_secret_litellm")
LITELLM_DB_PASSWORD=$(gen_secret "$GEN_DIR/litellm_db_password")
gen_secret "$GEN_DIR/litellm_sso_state" >/dev/null

# Upserts KEY=value into a .env file, preserving every other line. Creates
# the file if missing. Used to keep deploy-k8s/*.env's Keycloak client
# secrets in sync with what was actually baked into the realm import below —
# those files also carry unrelated per-service config (Postgres, Vault,
# Metabase, ...) that must not be touched.
upsert_env_var() {
  local file="$1" key="$2" value="$3"
  touch "$file"
  # A missing trailing newline on the file's last line would otherwise get
  # the appended KEY=value glued onto the end of it instead of starting a
  # new line, corrupting both values.
  [ -s "$file" ] && [ "$(tail -c1 "$file")" != "" ] && printf '\n' >> "$file"
  if grep -q "^${key}=" "$file"; then
    local esc_value
    esc_value=$(printf '%s' "$value" | sed -e 's/[&/\]/\\&/g')
    sed -i.bak -E "s|^${key}=.*|${key}=${esc_value}|" "$file" && rm -f "$file.bak"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

# ---- 2. Build the custom Keycloak image (bakes in keycloak-providers/) ----
docker build --load -t keycloak-vault-rar:local "$REPO_ROOT/keycloak-providers"
minikube -p "$PROFILE" image load keycloak-vault-rar:local

# ---- 3. Postgres: dedicated role + database for Keycloak ----
KC -n default exec -i -c postgres postgres-0 -- \
  psql -U vault_user -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='keycloak_user'" \
  | grep -q 1 || KC -n default exec -i -c postgres postgres-0 -- \
  psql -U vault_user -d postgres -c "CREATE ROLE keycloak_user WITH LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}'"

KC -n default exec -i -c postgres postgres-0 -- \
  psql -U vault_user -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='keycloak'" \
  | grep -q 1 || KC -n default exec -i -c postgres postgres-0 -- \
  psql -U vault_user -d postgres -c "CREATE DATABASE keycloak OWNER keycloak_user"

# LiteLLM SSO/virtual keys need their own Postgres (Prisma). Same instance
# as Keycloak; Keycloak already reaches it from a Connect-injected pod.
KC -n default exec -i -c postgres postgres-0 -- \
  psql -U vault_user -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='litellm_user'" \
  | grep -q 1 || KC -n default exec -i -c postgres postgres-0 -- \
  psql -U vault_user -d postgres -c "CREATE ROLE litellm_user WITH LOGIN PASSWORD '${LITELLM_DB_PASSWORD}'"

KC -n default exec -i -c postgres postgres-0 -- \
  psql -U vault_user -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='litellm'" \
  | grep -q 1 || KC -n default exec -i -c postgres postgres-0 -- \
  psql -U vault_user -d postgres -c "CREATE DATABASE litellm OWNER litellm_user"

KC create secret generic keycloak-db-credentials \
  --from-literal=username=keycloak_user --from-literal=password="$KEYCLOAK_DB_PASSWORD" \
  --dry-run=client -o yaml | KC apply -f -
KC create secret generic keycloak-admin-credentials \
  --from-literal=username=admin --from-literal=password="$KEYCLOAK_ADMIN_PASSWORD" \
  --dry-run=client -o yaml | KC apply -f -

# ---- 4. Render + import the realm ----
sed -e "s/__WEB_CLIENT_SECRET__/${WEB_CLIENT_SECRET}/" \
    -e "s/__TOKEN_EXCHANGE_CLIENT_SECRET__/${TOKEN_EXCHANGE_CLIENT_SECRET}/" \
    -e "s/__USER_MCP_CLIENT_SECRET__/${USER_MCP_CLIENT_SECRET}/" \
    -e "s/__LITELLM_CLIENT_SECRET__/${LITELLM_CLIENT_SECRET}/" \
  < "$SCRIPT_DIR/templates/keycloak-realm.json" \
  > "$GEN_DIR/keycloak-realm.json"

KC create configmap keycloak-realm \
  --from-file=demo-realm.json="$GEN_DIR/keycloak-realm.json" \
  --dry-run=client -o yaml | KC apply -f -

# ---- 5. Deploy Keycloak + wait for readiness ----
KC apply -f "$DEPLOY_DIR/keycloak.yaml"
KC apply -f "$DEPLOY_DIR/service-defaults-keycloak.yaml"
KC apply -f "$DEPLOY_DIR/keycloak-gateway.yaml"
# service-intentions.yaml is normally applied later by `make deploy`, but the
# keycloak-api-gateway -> keycloak intention it carries is needed right here:
# Vault's JWKS check below (and its later Vault reads) go through the gateway,
# and Consul denies that hop with no intention present. Harmless to apply
# early even though it also carries intentions for services (ai-agent,
# user-mcp, web, ...) not deployed until `make deploy` runs — Consul
# intentions are inert until the named services actually exist.
KC apply -f "$DEPLOY_DIR/service-intentions.yaml" || true

# The Consul API Gateway controller creates the "keycloak-api-gateway"
# Service asynchronously after the Gateway object is applied. Patching the
# NodePort before it exists makes `kubectl apply` create a fresh Service
# instead (defaulting to type: ClusterIP, which rejects nodePort) rather
# than updating the real LoadBalancer-typed one the controller manages.
echo "waiting for the keycloak-api-gateway Service to be created..."
for i in $(seq 1 60); do
  KC get svc keycloak-api-gateway >/dev/null 2>&1 && break
  sleep 2
done
KC apply -f "$SCRIPT_DIR/keycloak-nodeport.yaml"
KC rollout status deployment/keycloak --timeout=300s
KC wait --for=condition=Ready pod -l app=keycloak --timeout=300s

echo "waiting for Keycloak's realm discovery document..."
for i in $(seq 1 60); do
  KC exec deploy/keycloak -- curl -sf "http://localhost:8080/realms/demo/.well-known/openid-configuration" >/dev/null 2>&1 && break
  sleep 3
done

# ---- 5b. Keycloak Level of Authentication: password = LoA 1, OTP = LoA 2 ----
KC port-forward svc/keycloak 18091:8080 >"$GEN_DIR/pf-keycloak-loa.log" 2>&1 &
KC_LOA_PF_PID=$!
for i in $(seq 1 30); do
  curl -s -o /dev/null http://localhost:18091/realms/demo/.well-known/openid-configuration && break; sleep 1
done
python3 "$SCRIPT_DIR/keycloak_loa.py" --url http://localhost:18091 \
  --admin-password-file "$GEN_DIR/keycloak_admin_password"
kill "$KC_LOA_PF_PID" 2>/dev/null || true

# ---- 6. Vault: jwt-keycloak auth (step-up validation) + OAuth Resource
#         Server + Agent Registry ----
KC -n vault port-forward svc/vault 18200:8200 >"$GEN_DIR/pf-vault-keycloak.log" 2>&1 &
VAULT_PF_PID=$!
cleanup() { kill "$VAULT_PF_PID" 2>/dev/null || true; }
trap cleanup EXIT
export VAULT_ADDR="https://127.0.0.1:18200"
export VAULT_SKIP_VERIFY=true
export VAULT_TOKEN
VAULT_TOKEN=$(cat "$GEN_DIR/vault_token")
for i in $(seq 1 30); do curl -sk "$VAULT_ADDR/v1/sys/health" >/dev/null 2>&1 && break; sleep 1; done

vault auth list -format=json | jq -e 'has("jwt-keycloak/")' >/dev/null || \
  vault auth enable -path=jwt-keycloak -description="JWT auth method for user-mcp OBO tokens issued by Keycloak (step-up validation only)" jwt
vault write auth/jwt-keycloak/config \
  jwks_url="$KEYCLOAK_INTERNAL_JWKS" \
  bound_issuer="$KEYCLOAK_ISSUER" \
  jwt_supported_algs="RS256"

jq -n '{role_type: "jwt", user_claim: "preferred_username", bound_audiences: ["user-mcp"], bound_claims_type: "glob", bound_claims: {groups: ["reader", "writer", "admin"], scope: "*users.read*"}, token_ttl: 300, token_max_ttl: 900, token_type: "service"}' \
  | vault write auth/jwt-keycloak/role/user-mcp-oidc-read -
jq -n '{role_type: "jwt", user_claim: "preferred_username", bound_audiences: ["user-mcp"], bound_claims_type: "glob", bound_claims: {groups: ["writer", "admin"], scope: "*users.write*", acr: "2"}, token_ttl: 300, token_max_ttl: 900, token_type: "service"}' \
  | vault write auth/jwt-keycloak/role/user-mcp-oidc-write -
# The write role binds acr=2, which Keycloak only issues after a step-up login
# with OTP. user-mcp logs in with it for elevated actions, so Vault itself
# validates the step-up. The role name is un-HMAC'd in the audit log.
vault auth tune -audit-non-hmac-request-keys=role -audit-non-hmac-response-keys=metadata jwt-keycloak

# Vault's TOTP secrets engine is the users' authenticator: one key per demo
# user, using the secret of the otp credential in the realm (the rendered
# realm file is the single source), so `vault read totp/code/<user>` returns the
# code Keycloak asks for at LoA 2.
vault secrets list -format=json | jq -e 'has("totp/")' >/dev/null || vault secrets enable totp
for user in user writer admin; do
  otp_secret=$(jq -r --arg u "$user" '.users[] | select(.username == $u) | .credentials[] | select(.type == "otp") | .secretData | fromjson | .value' "$GEN_DIR/keycloak-realm.json")
  vault write "totp/keys/$user" key="$otp_secret" issuer=Keycloak account_name="$user" \
    algorithm=SHA1 digits=6 period=30 generate=false
done

echo "=== Vault 2.1 native Agentic IAM (OAuth Resource Server + Agent Registry) ==="
if vault read sys/activation-flags >/dev/null 2>&1 && vault read sys/activation-flags | grep -q "oauth-resource-server"; then
  vault read sys/activation-flags | grep "^activated" | grep -q "oauth-resource-server" \
    || vault write -f sys/activation-flags/oauth-resource-server/activate
else
  echo "oauth-resource-server is GA on this Vault version, no activation needed."
fi

vault write sys/config/oauth-resource-server/keycloak-demo \
  issuer_id="$KEYCLOAK_ISSUER" \
  use_jwks=true \
  jwks_uri="$KEYCLOAK_INTERNAL_JWKS" \
  user_claim="sub" \
  jwt_type="access_token" \
  audiences="user-mcp" \
  optional_authorization_details=false

# Vault Transform: PII masking for user-mcp's list_all_users/
# search_users_by_first_name. Each PII field gets its own template (a regex
# whose capture groups mark what to mask - literal separators pass through
# untouched) and a "masking" transformation (one-way: masking_character
# replaces every captured digit, no decode capability exists or is granted -
# this is display redaction, not reversible encryption/FPE). Patterns match
# users_seed.json's exact generated shape. user-mcp decides per-call whether
# to invoke this at all (admin sees plaintext, everyone else masked) - see
# postgres_repo.py.
vault secrets list -format=json | jq -e 'has("transform/")' >/dev/null || \
  vault secrets enable transform

vault write transform/template/user-mcp-ssn \
  type=regex pattern='(\d{3})-(\d{2})-(\d{4})'
vault write transform/transformation/user-mcp-ssn \
  type=masking masking_character="*" template=user-mcp-ssn allowed_roles=user-mcp-transform

vault write transform/template/user-mcp-credit-card \
  type=regex pattern='(\d{4})-(\d{4})-(\d{4})-(\d{4})'
vault write transform/transformation/user-mcp-credit-card \
  type=masking masking_character="*" template=user-mcp-credit-card allowed_roles=user-mcp-transform

vault write transform/template/user-mcp-phone \
  type=regex pattern='\+1-(\d{3})-(\d{3})-(\d{4})'
vault write transform/transformation/user-mcp-phone \
  type=masking masking_character="*" template=user-mcp-phone allowed_roles=user-mcp-transform

vault write transform/template/user-mcp-ip-address \
  type=regex pattern='(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})'
vault write transform/transformation/user-mcp-ip-address \
  type=masking masking_character="*" template=user-mcp-ip-address allowed_roles=user-mcp-transform

vault write transform/role/user-mcp-transform \
  transformations=user-mcp-ssn,user-mcp-credit-card,user-mcp-phone,user-mcp-ip-address

vault policy write user-mcp-agentic-read - <<-EOT
	path "database/creds/user-mcp-read-role" {
	  capabilities = ["read"]
	}
	path "transform/encode/user-mcp-transform" {
	  capabilities = ["create", "update"]
	}
	path "sys/leases/revoke" {
	  capabilities = ["update"]
	}
	EOT

vault policy write user-mcp-agentic-write - <<-EOT
	path "database/creds/user-mcp-write-role" {
	  capabilities = ["read"]
	}
	path "transform/encode/user-mcp-transform" {
	  capabilities = ["create", "update"]
	}
	path "sys/leases/revoke" {
	  capabilities = ["update"]
	}
	EOT

# One Vault identity per Keycloak login: the realm JSON pins each demo user's
# id to a literal ("user"/"writer"/"admin"), so the entity-alias name can match
# it directly with no Keycloak admin-API lookup needed.
register_user_entity() {
  local username="$1" policies="$2"
  vault write identity/entity name="user-${username}" policies="$policies" >/dev/null 2>&1 || true
  local entity_id
  entity_id=$(vault read -field=id "identity/entity/name/user-${username}")
  vault write identity/entity-alias \
    name="$username" \
    canonical_id="$entity_id" \
    issuer="$KEYCLOAK_ISSUER" \
    external_id="$username" \
    >/dev/null 2>&1 || true
}
register_user_entity "user" "user-mcp-agentic-read"
register_user_entity "writer" "user-mcp-agentic-read,user-mcp-agentic-write"
register_user_entity "admin" "user-mcp-agentic-read,user-mcp-agentic-write"

# Stable agent identity: agent_id is hardcoded to "ai-agent" in
# identity/oidc/role/agent-role's template (configure.sh section 4), so the
# actor claim the keycloak-providers SPI stamps as act.sub is always
# "ai-agent" regardless of which pod is currently running.
vault write identity/entity name=ai-agent-agentic \
  policies="user-mcp-agentic-read,user-mcp-agentic-write" >/dev/null 2>&1 || true
AI_AGENT_ENTITY_ID=$(vault read -field=id identity/entity/name/ai-agent-agentic)
vault write identity/entity-alias \
  name="ai-agent" \
  canonical_id="$AI_AGENT_ENTITY_ID" \
  issuer="$KEYCLOAK_ISSUER" \
  external_id="ai-agent" \
  >/dev/null 2>&1 || true

register_agent() {
  local name="$1" entity="$2"
  shift 2
  local existing
  existing=$(vault read -field=id "agent-registry/registration/display-name/${name}" 2>/dev/null || true)
  if [ -n "$existing" ]; then
    vault write agent-registry/register id="$existing" display_name="$name" entity_id="$entity" "$@"
  else
    vault write agent-registry/register display_name="$name" entity_id="$entity" "$@"
  fi
}
register_agent "ai-agent" "$AI_AGENT_ENTITY_ID" \
  owner=admin \
  description="ai-iam-guardrails demo agent" \
  ceiling_policies=user-mcp-agentic-read \
  ceiling_policies=user-mcp-agentic-write

# ---- 7. Keep deploy-k8s/*.env's Keycloak client secrets in sync ----
upsert_env_var "$DEPLOY_DIR/token-exchange.env" "IDENTITY_BROKER_OBO_CLIENT_SECRET" "$TOKEN_EXCHANGE_CLIENT_SECRET"
upsert_env_var "$DEPLOY_DIR/web-app.env" "KEYCLOAK_CLIENT_SECRET" "$WEB_CLIENT_SECRET"

echo
echo "=== done ==="
echo "keycloak realm:        demo (http://localhost:8081/realms/demo)"
echo "keycloak admin:        admin / $(cat "$GEN_DIR/keycloak_admin_password")"
echo "demo logins:           user/user (reader), writer/writer (writer), admin/admin (admin)"
echo "jwt-keycloak auth path: jwt-keycloak (step-up validation only)"
echo "oauth-resource-server:  keycloak-demo"
echo
echo "Verify: curl -s http://localhost:8081/realms/demo/.well-known/openid-configuration | jq .issuer"
