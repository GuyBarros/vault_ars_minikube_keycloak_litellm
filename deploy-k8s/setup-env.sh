#!/bin/bash
# Creates deploy-k8s/*.env from the committed *.env.example templates, filling
# in randomly generated values for secrets this script can generate itself.
# Safe to re-run: never overwrites an .env file that already exists.
#
# Client secrets synced from a real Keycloak realm (IDENTITY_BROKER_OBO_CLIENT_SECRET,
# KEYCLOAK_CLIENT_SECRET) are left blank here and get filled in by
# infra/local-minikube/keycloak.sh (`make keycloak`) for the local minikube
# flow. For a manual/EKS deploy, set them by hand from your Keycloak client
# configuration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

command -v openssl >/dev/null 2>&1 || { echo "missing required tool: openssl" >&2; exit 1; }

upsert() {
  local file="$1" key="$2" value="$3"
  if grep -q "^${key}=" "$file"; then
    local esc_value
    esc_value=$(printf '%s' "$value" | sed -e 's/[&/\]/\\&/g')
    sed -i.bak -E "s|^${key}=.*|${key}=${esc_value}|" "$file" && rm -f "$file.bak"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

for example in "$SCRIPT_DIR"/*.env.example; do
  target="${example%.example}"
  if [ -f "$target" ]; then
    echo "skip: $target already exists"
    continue
  fi
  cp "$example" "$target"
  echo "created: $target"
done

upsert "$SCRIPT_DIR/user-mcp.env" "USER_MCP_DB_PASSWORD" "$(openssl rand -hex 20)"
upsert "$SCRIPT_DIR/web-app.env" "SESSION_PASSWORD" "$(openssl rand -base64 32)"

cat <<'EOF'

deploy-k8s/*.env generated. Remaining values you must supply by hand:
  - Keycloak client secrets (IDENTITY_BROKER_OBO_CLIENT_SECRET, KEYCLOAK_CLIENT_SECRET):
    auto-filled by `make keycloak` in infra/local-minikube/ for the local lab,
    or copy from your Keycloak client config for a manual/EKS deploy.
  - WATSONX_APIKEY / WATSONX_PROJECT_ID in ai-agent.env: only needed if you want the
    watsonx.ai LLM fallback instead of the default local Ollama model.
  - USER_MCP_METABASE_URL in user-mcp.env: only needed if you want the optional
    Metabase tools; leave blank to disable them.
EOF
