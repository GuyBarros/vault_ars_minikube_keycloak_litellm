#!/usr/bin/env bash
# Seed the initial MCP authz catalog into Vault KV v2.
#
# Required env: VAULT_ADDR, VAULT_TOKEN (with write on opa-policies/data/mcp-authz/catalog).
#
# After this writes, vault-agent on the opa-mcp-authz pod will render the
# catalog into /vault/secrets/catalog.json and OPA --watch will reload it.

set -euo pipefail

cat > /tmp/mcp-authz-catalog.json <<'JSON'
{
  "rules": {
    "default/litellm-gateway": {
      "default/user-mcp": {
        "allow": [
          "list_all_users",
          "search_users_by_first_name",
          "update_user_by_email"
        ]
      }
    }
  }
}
JSON

vault kv put opa-policies/mcp-authz/catalog @/tmp/mcp-authz-catalog.json

echo
echo "Seeded. Current version:"
vault kv metadata get opa-policies/mcp-authz/catalog | grep -E '^(current_version|updated_time)'
