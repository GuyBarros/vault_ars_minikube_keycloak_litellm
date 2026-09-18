#!/bin/bash
# Prints tokens and passwords produced by make up. Safe to re-run; missing
# files print as not generated yet.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEN_DIR="$SCRIPT_DIR/generated"
source "$SCRIPT_DIR/local-config.sh"

show() {
  local file="$1" label="$2"
  if [ -f "$GEN_DIR/$file" ]; then
    printf '%-36s %s\n' "$label" "$(cat "$GEN_DIR/$file")"
  else
    printf '%-36s %s\n' "$label" "(not generated yet)"
  fi
}

echo
echo "=== credentials ==="
echo
echo "demo logins (realm demo):"
printf '%-36s %s\n' "user / user" "group reader"
printf '%-36s %s\n' "writer / writer" "group writer"
printf '%-36s %s\n' "admin / admin" "group admin"
echo
show keycloak_admin_password "keycloak admin (user admin)"
show keycloak_db_password "keycloak db (user keycloak_user)"
show keycloak_client_secret_web "keycloak client secret web"
show keycloak_client_secret_token_exchange "keycloak client secret token-exchange"
show keycloak_client_secret_ciba "keycloak client secret ciba"
show keycloak_client_secret_user_mcp "keycloak client secret user-mcp"
show keycloak_client_secret_litellm "keycloak client secret litellm"
echo
printf '%-36s %s\n' "litellm admin UI" "http://localhost:4000/ui (Keycloak SSO or admin/admin)"
printf '%-36s %s\n' "litellm fallback login" "http://localhost:4000/fallback/login (admin/admin)"
show litellm_master_key "litellm master key"
show litellm_db_password "litellm db (user litellm_user)"
echo
show vault_token "vault root token"
if [ -f "$GEN_DIR/vault_init.json" ]; then
  printf '%-36s %s\n' "vault unseal key" "$(jq -r '.unseal_keys_b64[0]' "$GEN_DIR/vault_init.json")"
else
  printf '%-36s %s\n' "vault unseal key" "(not generated yet)"
fi
show consul_token "consul bootstrap token"
show consul_mcp_authz_token "consul mcp-authz token"
echo
printf '%-36s %s\n' "postgres admin (user vault_user)" "$POSTGRES_ADMIN_PASSWORD"
echo
echo "files: $GEN_DIR"
echo
