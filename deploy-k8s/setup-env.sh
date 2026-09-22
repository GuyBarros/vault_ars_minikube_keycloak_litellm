#!/bin/bash
# Creates/completes deploy-k8s/*.env from the committed *.env.example
# templates, filling in randomly generated values for secrets this script
# can generate itself. Safe to re-run: merges in only the keys that are
# missing, and never rotates a secret that's already set.
#
# Runs before infra/local-minikube/keycloak.sh in the make up/deploy chain,
# but keycloak.sh's own upsert_env_var can also create these files first
# (with just the one Keycloak secret it manages) — so this always merges
# missing keys in rather than skipping files that already exist.
#
# Client secrets synced from the Keycloak realm (IDENTITY_BROKER_OBO_CLIENT_SECRET,
# KEYCLOAK_CLIENT_SECRET) are left blank here and get filled in by
# infra/local-minikube/keycloak.sh (`make keycloak`).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

command -v openssl >/dev/null 2>&1 || { echo "missing required tool: openssl" >&2; exit 1; }

# Sets KEY=value in $file only if KEY isn't already defined there (blank
# values from the template count as "defined" and are left alone).
set_if_missing() {
  local file="$1" key="$2" value="$3"
  grep -q "^${key}=" "$file" || printf '%s=%s\n' "$key" "$value" >> "$file"
}

# Same, but only treats an empty/blank value as "missing" — for secrets this
# script generates itself, so a real value already present is never rotated.
set_if_empty() {
  local file="$1" key="$2" value="$3"
  local current
  current=$(grep "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2-)
  if [ -z "$current" ]; then
    if grep -q "^${key}=" "$file"; then
      local esc_value
      esc_value=$(printf '%s' "$value" | sed -e 's/[&/\]/\\&/g')
      sed -i.bak -E "s|^${key}=.*|${key}=${esc_value}|" "$file" && rm -f "$file.bak"
    else
      printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
  fi
}

for example in "$SCRIPT_DIR"/*.env.example; do
  target="${example%.example}"
  if [ -f "$target" ]; then
    echo "merging missing keys into: $target"
  else
    touch "$target"
    echo "created: $target"
  fi
  while IFS='=' read -r key value; do
    set_if_missing "$target" "$key" "$value"
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$example")
done

set_if_empty "$SCRIPT_DIR/user-mcp.env" "USER_MCP_DB_PASSWORD" "$(openssl rand -hex 20)"
set_if_empty "$SCRIPT_DIR/web-app.env" "SESSION_PASSWORD" "$(openssl rand -base64 32)"

cat <<'EOF'

deploy-k8s/*.env generated/completed. Remaining values you must supply by hand:
  - Keycloak client secrets (IDENTITY_BROKER_OBO_CLIENT_SECRET, KEYCLOAK_CLIENT_SECRET):
    auto-filled by `make keycloak` in infra/local-minikube/.
  - WATSONX_APIKEY / WATSONX_PROJECT_ID in ai-agent.env: only needed if you want the
    watsonx.ai LLM fallback instead of the default local Ollama model.
  - USER_MCP_METABASE_URL in user-mcp.env: only needed if you want the optional
    Metabase tools; leave blank to disable them.
EOF
