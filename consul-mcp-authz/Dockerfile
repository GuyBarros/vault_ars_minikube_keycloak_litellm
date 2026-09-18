# syntax=docker/dockerfile:1
#
# consul-mcp-authz — combined operator workload.
#
#   /app/.venv        Python 3.12 venv (FastAPI + hvac, etc.)
#   /app/api/         FastAPI package  (uvicorn api.main:app, port 8080)
#   /app/ui/          Next.js standalone server (node server.js, port 8502)
#   /app/policy/      Snapshot of opa-mcp-auth/policy for the validator
#   /usr/local/bin/opa  Validator dependency
#
# supervisord runs both processes inside the one container and forwards
# SIGTERM, so Kubernetes can drain the pod cleanly.
#
# Build (multi-arch):
#   cd consul-mcp-authz
#   rm -rf api/policy && cp -r ../opa-mcp-auth/policy api/policy
#   docker buildx build --platform linux/amd64,linux/arm64 \
#     -t panchalravi/consul-mcp-authz:latest --push .

# ── UI builder ───────────────────────────────────────────────────────────
FROM node:20-bookworm-slim AS ui-deps
WORKDIR /ui
COPY ui/package.json ui/package-lock.json* ./
RUN npm ci --no-audit --no-fund

FROM node:20-bookworm-slim AS ui-builder
WORKDIR /ui
COPY --from=ui-deps /ui/node_modules ./node_modules
COPY ui/ ./
RUN npm run build

# ── API builder ──────────────────────────────────────────────────────────
# WORKDIR mirrors the runtime location so uv's generated console-script
# shebangs (e.g., /app/.venv/bin/uvicorn → #!/app/.venv/bin/python) stay
# correct after the venv is copied into the runtime stage.
FROM python:3.12-slim AS api-builder
ENV UV_LINK_MODE=copy
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY api/pyproject.toml api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ── opa CLI ──────────────────────────────────────────────────────────────
FROM openpolicyagent/opa:latest AS opa

# ── Runtime ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

# Copy the Node 20 binary out of node:20-bookworm-slim. Both base images
# are Debian Bookworm so glibc / libstdc++ are ABI-compatible. We don't
# need npm at runtime — Next.js's standalone output ships a self-contained
# server.js with its own minimal node_modules.
COPY --from=node:20-bookworm-slim /usr/local/bin/node /usr/local/bin/node

# Install supervisord into the Python venv we'll inherit from api-builder.
COPY --from=api-builder /app/.venv /app/.venv
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"
# setuptools is needed by supervisor 4.x (it imports `pkg_resources`),
# and python:3.12-slim no longer ships it by default.
RUN pip install --no-cache-dir setuptools==75.6.0 supervisor==4.2.5

# ── API code ─────────────────────────────────────────────────────────────
COPY --from=opa /opa /usr/local/bin/opa
COPY api/api/         ./api/
COPY api/app_logging/ ./app_logging/
COPY api/catalog/     ./catalog/
COPY api/config/      ./config/
COPY api/discovery/   ./discovery/
COPY api/exceptions/  ./exceptions/
COPY api/models/      ./models/
COPY api/policy/      ./policy/

# ── UI runtime ───────────────────────────────────────────────────────────
# Next.js standalone output: server.js + a pruned node_modules at root,
# plus .next/static for assets.
COPY --from=ui-builder /ui/.next/standalone /app/ui
COPY --from=ui-builder /ui/.next/static     /app/ui/.next/static

# ── Supervisor ───────────────────────────────────────────────────────────
COPY supervisord.conf /etc/supervisord.conf

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    MCP_AUTHZ_API_OPA_BIN=/usr/local/bin/opa \
    MCP_AUTHZ_API_POLICY_DIR=/app/policy \
    MCP_AUTHZ_UI_API_URL=http://127.0.0.1:8080

EXPOSE 8080 8502

CMD ["supervisord", "-c", "/etc/supervisord.conf", "-n"]
