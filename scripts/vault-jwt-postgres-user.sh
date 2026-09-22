#!/usr/bin/env bash
# Login to Vault's jwt-keycloak auth method and mint a dynamic Postgres user
# for both the reader and writer roles.
#
# jwt-keycloak login validates a step-up (user-mcp-oidc-read / -write).
# Postgres users are issued at database/creds/* with the Keycloak JWT as
# X-Vault-Token (OAuth Resource Server), same path user-mcp uses.
#
# JWT_TOKEN must be an OBO access token with aud=user-mcp:
#   reader  → groups reader|writer|admin and scope containing users.read
#   writer  → groups writer|admin and scope containing users.write
#
# Usage:
#   JWT_TOKEN=eyJ... ./scripts/vault-jwt-postgres-user.sh
#   VAULT_ADDR=https://127.0.0.1:18200 JWT_TOKEN=eyJ... ./scripts/vault-jwt-postgres-user.sh
set -eu
: "${JWT_TOKEN:?JWT_TOKEN is required (Keycloak OBO JWT, aud=user-mcp)}"
VAULT_ADDR="${VAULT_ADDR:-https://127.0.0.1:18200}"
VAULT_SKIP_VERIFY="${VAULT_SKIP_VERIFY:-true}"
VAULT_NAMESPACE="${VAULT_NAMESPACE:-}"
VAULT_JWT_PATH="${VAULT_JWT_PATH:-jwt-keycloak}"
VAULT_JWT_READ_ROLE="${VAULT_JWT_READ_ROLE:-user-mcp-oidc-read}"
VAULT_JWT_WRITE_ROLE="${VAULT_JWT_WRITE_ROLE:-user-mcp-oidc-write}"
VAULT_DB_READ_PATH="${VAULT_DB_READ_PATH:-database/creds/user-mcp-read-role}"
VAULT_DB_WRITE_PATH="${VAULT_DB_WRITE_PATH:-database/creds/user-mcp-write-role}"
if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 1
fi
vault_curl() {
  set -- -sS -w '\n%{http_code}' "$@"
  if [ "$VAULT_SKIP_VERIFY" = "true" ] || [ "$VAULT_SKIP_VERIFY" = "1" ]; then
    set -- -k "$@"
  fi
  if [ -n "$VAULT_NAMESPACE" ]; then
    set -- -H "X-Vault-Namespace: ${VAULT_NAMESPACE}" "$@"
  fi
  curl "$@"
}
vault_error() {
  jq -r '
    if .errors then (.errors | join("; "))
    elif .error then .error
    else (tostring | .[0:500])
    end
  ' <<<"$1" 2>/dev/null || echo "$1"
}
split_status() {
  _http_status=$(printf '%s\n' "$1" | tail -n 1)
  _http_body=$(printf '%s\n' "$1" | sed '$d')
}
login_jwt() {
  local role=$1
  local url="${VAULT_ADDR%/}/v1/auth/${VAULT_JWT_PATH}/login"
  local payload raw
  payload=$(jq -n --arg role "$role" --arg jwt "$JWT_TOKEN" '{role:$role, jwt:$jwt}')
  raw=$(
    vault_curl \
      -H "Content-Type: application/json" \
      -X POST "$url" \
      -d "$payload"
  )
  split_status "$raw"
  if [ "$_http_status" -ge 400 ]; then
    echo "jwt-keycloak login failed role=${role} status=${_http_status}: $(vault_error "$_http_body")" >&2
    return 1
  fi
  if [ -z "$(jq -r '.auth.client_token // empty' <<<"$_http_body")" ]; then
    echo "jwt-keycloak login role=${role} returned no auth.client_token" >&2
    return 1
  fi
  jq -nc \
    --arg role "$role" \
    --argjson ttl "$(jq '.auth.lease_duration // 0' <<<"$_http_body")" \
    --argjson policies "$(jq '.auth.policies // []' <<<"$_http_body")" \
    --arg accessor "$(jq -r '.auth.accessor // empty' <<<"$_http_body")" \
    '{ok:true, step:"jwt_login", role:$role, lease_duration:$ttl, policies:$policies, accessor:$accessor}'
}
mint_postgres() {
  local label=$1
  local creds_path=$2
  local url="${VAULT_ADDR%/}/v1/${creds_path#/}"
  local raw
  raw=$(vault_curl -H "X-Vault-Token: ${JWT_TOKEN}" "$url")
  split_status "$raw"
  if [ "$_http_status" -ge 400 ]; then
    echo "database/creds failed path=${creds_path} status=${_http_status}: $(vault_error "$_http_body")" >&2
    return 1
  fi
  local username password lease_id
  username=$(jq -r '.data.username // empty' <<<"$_http_body")
  password=$(jq -r '.data.password // empty' <<<"$_http_body")
  lease_id=$(jq -r '.lease_id // empty' <<<"$_http_body")
  if [ -z "$username" ] || [ -z "$password" ]; then
    echo "database/creds path=${creds_path} missing username/password" >&2
    return 1
  fi
  jq -nc \
    --arg label "$label" \
    --arg path "$creds_path" \
    --arg username "$username" \
    --arg password "$password" \
    --arg lease_id "$lease_id" \
    --argjson ttl "$(jq '.lease_duration // 0' <<<"$_http_body")" \
    '{ok:true, step:"postgres_user", role:$label, path:$path, username:$username, password:$password, lease_id:$lease_id, lease_duration:$ttl}'
}
echo "VAULT_ADDR=${VAULT_ADDR} jwt_path=${VAULT_JWT_PATH}" >&2
read_ok=0
write_ok=0
if login_jwt "$VAULT_JWT_READ_ROLE"; then
  if mint_postgres "reader" "$VAULT_DB_READ_PATH"; then
    read_ok=1
  fi
fi
if login_jwt "$VAULT_JWT_WRITE_ROLE"; then
  if mint_postgres "writer" "$VAULT_DB_WRITE_PATH"; then
    write_ok=1
  fi
fi
if [ "$read_ok" -eq 0 ] && [ "$write_ok" -eq 0 ]; then
  echo "neither reader nor writer succeeded" >&2
  exit 1
fi
if [ "$read_ok" -eq 0 ]; then
  echo "reader role did not succeed (token may lack users.read / reader|writer|admin group)" >&2
fi
if [ "$write_ok" -eq 0 ]; then
  echo "writer role did not succeed (token may lack users.write / writer|admin group)" >&2
fi