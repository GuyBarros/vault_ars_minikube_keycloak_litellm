#!/bin/bash
# Local analog of modules/minikube-resources-config: layers the MCP-authz
# control plane (Vault JWT auth, OPA bundle, identity OIDC, DB secrets engine,
# Postgres, Consul ACL token) on top of the Consul/Vault that bootstrap.sh
# brings up. Run bootstrap.sh first.
#
# The AWS module does this over SSH (the minikube apiserver isn't routable
# from the Terraform host) and reaches Vault/Consul through the ALB. Here
# minikube runs on this machine, so every step below runs directly:
# kubectl/minikube commands hit the cluster directly, and Vault/Consul are
# driven with the real CLIs against a port-forward.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
RESOURCES_DIR="$INFRA_DIR/modules/minikube-resources-config"
GEN_DIR="$SCRIPT_DIR/generated"
PROFILE="local-minikube-demo"
source "$SCRIPT_DIR/local-config.sh"

for bin in minikube kubectl vault consul psql jq envsubst; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 1; }
done

[ -f "$GEN_DIR/vault_token" ] || { echo "run bootstrap.sh first (missing $GEN_DIR/vault_token)" >&2; exit 1; }
[ -f "$GEN_DIR/consul_token" ] || { echo "run bootstrap.sh first (missing $GEN_DIR/consul_token)" >&2; exit 1; }

# ---- locals (mirrors modules/minikube-resources-config/locals.tf) ----
K8S_SA_ISSUER="https://kubernetes.default.svc.cluster.local"
K8S_SA_AUDIENCE="$K8S_SA_ISSUER"
POSTGRES_NAMESPACE="default"
POSTGRES_APP_LABEL="postgres"
POSTGRES_SERVICE_NAME="postgres"
POSTGRES_STATEFULSET_NAME="postgres"
POSTGRES_SECRET_NAME="postgres-credentials"
POSTGRES_ADMIN_USER="vault_user"
POSTGRES_ADMIN_DB="postgres"
USERS_DB_NAME="users"
POSTGRES_IN_CLUSTER_HOST="${POSTGRES_SERVICE_NAME}.${POSTGRES_NAMESPACE}.svc.cluster.local"
USERS_DB_CONNECTION_URL="postgresql://{{username}}:{{password}}@${POSTGRES_IN_CLUSTER_HOST}:5432/${USERS_DB_NAME}?sslmode=disable"
# No ALB locally; Vault runs in-cluster, so use its own in-cluster DNS as the
# OIDC issuer host (reachable by any pod that needs to validate its tokens).
VAULT_PUBLIC_ADDR="vault.vault.svc.cluster.local:8200"

mkdir -p "$GEN_DIR"
KC() { kubectl --context "$PROFILE" "$@"; }

# ---- port-forwards to drive Vault/Consul with the real CLIs ----
KC -n vault port-forward svc/vault 18200:8200 >"$GEN_DIR/pf-vault.log" 2>&1 &
VAULT_PF_PID=$!
KC -n consul port-forward pod/consul-server-0 18501:8501 >"$GEN_DIR/pf-consul.log" 2>&1 &
CONSUL_PF_PID=$!
cleanup() { kill "$VAULT_PF_PID" "$CONSUL_PF_PID" 2>/dev/null || true; }
trap cleanup EXIT

export VAULT_ADDR="https://127.0.0.1:18200"
export VAULT_SKIP_VERIFY=true
export VAULT_TOKEN
VAULT_TOKEN=$(cat "$GEN_DIR/vault_token")
export CONSUL_HTTP_ADDR="https://127.0.0.1:18501"
export CONSUL_HTTP_SSL_VERIFY=false
export CONSUL_HTTP_TOKEN
CONSUL_HTTP_TOKEN=$(cat "$GEN_DIR/consul_token")

echo "waiting for port-forwards..."
for i in $(seq 1 30); do curl -sk "$VAULT_ADDR/v1/sys/health" >/dev/null 2>&1 && break; sleep 1; done
for i in $(seq 1 30); do curl -sk "$CONSUL_HTTP_ADDR/v1/status/leader" >/dev/null 2>&1 && break; sleep 1; done

echo "=== 1. minikube SA signing key (validates in-cluster workload JWTs) ==="
minikube -p "$PROFILE" ssh -- sudo cat /var/lib/minikube/certs/sa.pub > "$GEN_DIR/minikube_sa_pub.pem"

echo "=== 2. Postgres (StatefulSet + users table, seeded) ==="
namespace="$POSTGRES_NAMESPACE" app_label="$POSTGRES_APP_LABEL" service_name="$POSTGRES_SERVICE_NAME" \
  statefulset_name="$POSTGRES_STATEFULSET_NAME" secret_name="$POSTGRES_SECRET_NAME" \
  admin_user="$POSTGRES_ADMIN_USER" admin_db="$POSTGRES_ADMIN_DB" admin_password="$POSTGRES_ADMIN_PASSWORD" \
  envsubst '${namespace} ${app_label} ${service_name} ${statefulset_name} ${secret_name} ${admin_user} ${admin_db} ${admin_password}' \
  < "$RESOURCES_DIR/templates/postgres.yaml.tftpl" > "$GEN_DIR/postgres.yaml"

KC apply -f "$GEN_DIR/postgres.yaml"
KC -n "$POSTGRES_NAMESPACE" rollout status statefulset/"$POSTGRES_STATEFULSET_NAME" --timeout=300s
KC -n "$POSTGRES_NAMESPACE" wait --for=condition=Ready pod/"${POSTGRES_STATEFULSET_NAME}-0" --timeout=300s

for i in $(seq 1 60); do
  KC -n "$POSTGRES_NAMESPACE" exec -i "${POSTGRES_STATEFULSET_NAME}-0" -- pg_isready -h 127.0.0.1 -p 5432 -q && break
  echo "waiting for postgres to accept TCP connections..."
  sleep 3
done

KC -n "$POSTGRES_NAMESPACE" exec -i "${POSTGRES_STATEFULSET_NAME}-0" -- \
  psql -U "$POSTGRES_ADMIN_USER" -d "$POSTGRES_ADMIN_DB" -tAc "SELECT 1 FROM pg_database WHERE datname='${USERS_DB_NAME}'" \
  | grep -q 1 || KC -n "$POSTGRES_NAMESPACE" exec -i "${POSTGRES_STATEFULSET_NAME}-0" -- \
  psql -U "$POSTGRES_ADMIN_USER" -d "$POSTGRES_ADMIN_DB" -c "CREATE DATABASE ${USERS_DB_NAME}"

{
  cat <<-SQL
	CREATE TABLE IF NOT EXISTS users (
	  id                 SERIAL PRIMARY KEY,
	  first_name         TEXT NOT NULL,
	  last_name          TEXT NOT NULL,
	  ssn                TEXT,
	  phone              TEXT,
	  email              TEXT UNIQUE NOT NULL,
	  credit_card_number TEXT,
	  ip_address         TEXT
	);
	SQL
  jq -r -f "$SCRIPT_DIR/seed-users.jq" "$RESOURCES_DIR/users_seed.json"
} > "$GEN_DIR/users-init.sql"

KC -n "$POSTGRES_NAMESPACE" exec -i "${POSTGRES_STATEFULSET_NAME}-0" -- \
  psql -U "$POSTGRES_ADMIN_USER" -d "$USERS_DB_NAME" -v ON_ERROR_STOP=1 < "$GEN_DIR/users-init.sql"

echo "=== 3. Vault: k8s JWT auth backend + OPA policy bundle ==="
vault auth list -format=json | jq -e 'has("k8s_jwt/")' >/dev/null || \
  vault auth enable -path=k8s_jwt -description="JWT auth method for minikube workloads" jwt

jq -n --arg issuer "$K8S_SA_ISSUER" --rawfile pubkey "$GEN_DIR/minikube_sa_pub.pem" \
  '{bound_issuer: $issuer, jwt_validation_pubkeys: [$pubkey], default_role: "agent-role"}' \
  | vault write auth/k8s_jwt/config -

vault secrets list -format=json | jq -e 'has("opa-policies/")' >/dev/null || \
  vault secrets enable -path=opa-policies -version=2 kv

vault kv put opa-policies/bundle \
  code_safety.rego=@"$INFRA_DIR/config/opa_policies/code_safety.rego" \
  patterns.rego=@"$INFRA_DIR/config/opa_policies/patterns.rego" \
  pii_filter.rego=@"$INFRA_DIR/config/opa_policies/pii_filter.rego" \
  prompt_injection.rego=@"$INFRA_DIR/config/opa_policies/prompt_injection.rego"

vault policy write agent-role-identity-policy - <<-EOT
	path "identity/oidc/token/agent-role" {
	  capabilities = ["read"]
	}
	EOT

vault policy write opa - <<-EOT
	path "opa-policies/data/bundle" {
	  capabilities = ["read"]
	}
	EOT

vault policy write opa-mcp-authz - <<-EOT
	path "opa-policies/data/mcp-authz/catalog" {
	  capabilities = ["read"]
	}
	EOT

vault policy write consul-mcp-authz - <<-EOT
	path "opa-policies/data/mcp-authz/catalog" {
	  capabilities = ["create", "read", "update"]
	}
	path "opa-policies/metadata/mcp-authz/catalog" {
	  capabilities = ["read"]
	}
	path "opa-policies/data/consul/mcp-authz-token" {
	  capabilities = ["read"]
	}
	EOT

jq -n --arg aud "$K8S_SA_AUDIENCE" \
  '{role_type: "jwt", bound_audiences: [$aud], claim_mappings: {"/kubernetes.io/namespace": "namespace"}, token_explicit_max_ttl: 0, token_period: 1800, token_policies: ["default", "agent-role-identity-policy"], token_type: "service", user_claim: "/kubernetes.io/pod/name", user_claim_json_pointer: true}' \
  | vault write auth/k8s_jwt/role/agent-role -

jq -n --arg aud "$K8S_SA_AUDIENCE" \
  '{role_type: "jwt", bound_audiences: [$aud], bound_subject: "system:serviceaccount:opa:opa-service", claim_mappings: {"/kubernetes.io/namespace": "namespace"}, token_explicit_max_ttl: 0, token_period: 1800, token_policies: ["default", "opa"], token_type: "service", user_claim: "sub", user_claim_json_pointer: true}' \
  | vault write auth/k8s_jwt/role/opa-server -

jq -n --arg aud "$K8S_SA_AUDIENCE" \
  '{role_type: "jwt", bound_audiences: [$aud], bound_subject: "system:serviceaccount:default:opa-mcp-authz", claim_mappings: {"/kubernetes.io/namespace": "namespace"}, token_explicit_max_ttl: 0, token_period: 1800, token_policies: ["default", "opa-mcp-authz"], token_type: "service", user_claim: "sub", user_claim_json_pointer: true}' \
  | vault write auth/k8s_jwt/role/opa-mcp-authz -

jq -n --arg aud "$K8S_SA_AUDIENCE" \
  '{role_type: "jwt", bound_audiences: [$aud], bound_subject: "system:serviceaccount:default:consul-mcp-authz", claim_mappings: {"/kubernetes.io/namespace": "namespace"}, token_explicit_max_ttl: 0, token_period: 1800, token_policies: ["default", "consul-mcp-authz"], token_type: "service", user_claim: "sub", user_claim_json_pointer: true}' \
  | vault write auth/k8s_jwt/role/consul-mcp-authz -

echo "=== 4. Vault: identity OIDC issuer + agent-role identity token ==="
vault read identity/oidc/key/default >/dev/null 2>&1 || \
  vault write identity/oidc/key/default rotation_period="730h" verification_ttl="24h" algorithm="RS256"
vault write identity/oidc/config issuer="https://${VAULT_PUBLIC_ADDR}"

# agent_id is a fixed literal ("ai-agent"), not the pod-name-derived entity
# alias — the k8s_jwt "agent-role" login is bound only by audience (any pod
# can authenticate as it), and its entity-alias name is the ephemeral pod
# name, which would churn on every redeploy. Keeping agent_id fixed lets
# keycloak.sh bind one stable Vault Agent Registry entry to it (see
# infra/local-minikube/keycloak.sh) instead of re-registering per pod.
template=$(jq -n -r --arg org "$ID_ORG" --arg bu "$ID_BU" --arg dept "$ID_DEPT" --arg svc "$ID_SVC_GROUP" \
  '"{\"org\": \"" + $org + "\", \"bu\": \"" + $bu + "\", \"department\": \"" + $dept + "\", \"service_group\": \"" + $svc + "\", \"entity_id\": {{identity.entity.id}}, \"agent_id\": \"ai-agent\"}"')
jq -n --arg key "default" --argjson ttl 3600 --arg template "$template" \
  '{key: $key, ttl: $ttl, template: $template}' | vault write identity/oidc/role/agent-role -

echo "=== 5. Vault: user-mcp database secrets engine (dynamic Postgres creds) ==="
vault secrets list -format=json | jq -e 'has("database/")' >/dev/null || \
  vault secrets enable -path=database database

vault write database/config/users-db \
  plugin_name=postgresql-database-plugin \
  allowed_roles="user-mcp-read-role,user-mcp-write-role" \
  connection_url="$USERS_DB_CONNECTION_URL" \
  username="$POSTGRES_ADMIN_USER" \
  password="$POSTGRES_ADMIN_PASSWORD"

read_create=$(cat <<SQL
CREATE ROLE "{{name}}" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}';
GRANT CONNECT ON DATABASE ${USERS_DB_NAME} TO "{{name}}";
GRANT USAGE ON SCHEMA public TO "{{name}}";
GRANT SELECT ON users TO "{{name}}";
SQL
)
write_create=$(cat <<SQL
CREATE ROLE "{{name}}" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}';
GRANT CONNECT ON DATABASE ${USERS_DB_NAME} TO "{{name}}";
GRANT USAGE ON SCHEMA public TO "{{name}}";
GRANT SELECT, INSERT, UPDATE, DELETE ON users TO "{{name}}";
GRANT USAGE, SELECT ON SEQUENCE users_id_seq TO "{{name}}";
SQL
)
db_revoke=$(cat <<SQL
REVOKE ALL PRIVILEGES ON users FROM "{{name}}";
REVOKE ALL PRIVILEGES ON SCHEMA public FROM "{{name}}";
REVOKE CONNECT ON DATABASE ${USERS_DB_NAME} FROM "{{name}}";
DROP ROLE IF EXISTS "{{name}}";
SQL
)

jq -n --arg db "users-db" --arg create "$read_create" --arg revoke "$db_revoke" \
  '{db_name: $db, default_ttl: 3600, max_ttl: 86400, creation_statements: [$create], revocation_statements: [$revoke]}' \
  | vault write database/roles/user-mcp-read-role -

jq -n --arg db "users-db" --arg create "$write_create" --arg revoke "$db_revoke" \
  '{db_name: $db, default_ttl: 3600, max_ttl: 86400, creation_statements: [$create], revocation_statements: [$revoke]}' \
  | vault write database/roles/user-mcp-write-role -

echo "=== 6. Consul: ACL policy + token for consul-mcp-authz discovery ==="
if ! consul acl policy read -name consul-mcp-authz-read >/dev/null 2>&1; then
  consul acl policy create -name consul-mcp-authz-read \
    -description "Read-only catalog access for consul-mcp-authz discovery" \
    -rules - <<-EOT
	service_prefix "" {
	  policy = "read"
	}
	namespace_prefix "" {
	  service_prefix "" {
	    policy = "read"
	  }
	}
	EOT
fi

token_secret_id=$(consul acl token create \
  -description "consul-mcp-authz discovery token (read-only catalog)" \
  -policy-name consul-mcp-authz-read -format=json | jq -r '.SecretID')

printf '%s\n' "$token_secret_id" > "$GEN_DIR/consul_mcp_authz_token"
vault kv put opa-policies/consul/mcp-authz-token token="$token_secret_id"

echo "=== 7. Vault: seed initial OPA MCP authz catalog ==="
# Seeds the rule catalog read by opa-mcp-authz at startup via Vault Agent.
# A missing catalog causes Vault Agent to spin indefinitely and OPA to never
# start, which blocks every request to user-mcp (Envoy ext-authz, fail-closed).
# consul-mcp-authz may update this KV after startup; this is just the initial value.
vault kv put opa-policies/mcp-authz/catalog - <<-EOT
	{"rules": {"default/litellm-gateway": {"default/user-mcp": {"allow": ["list_all_users", "search_users_by_first_name", "update_user_by_email", "create_user", "delete_user_by_email"]}}}}
	EOT

echo
echo "=== done ==="
echo "k8s_jwt auth path:     k8s_jwt"
echo "opa-policies KV mount: opa-policies"
echo "postgres host:         $POSTGRES_IN_CLUSTER_HOST"
echo "postgres users db:     $USERS_DB_NAME"
echo
echo "Run keycloak.sh next (deploys Keycloak, then configures Vault's"
echo "jwt-keycloak auth + OAuth Resource Server + Agent Registry against it)."
