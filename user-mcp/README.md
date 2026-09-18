# User MCP

This service is a FastMCP-based Model Context Protocol server that exposes user-management tools (`list_all_users`, `search_users_by_first_name`, `create_user`, `delete_user_by_email`, `update_user_by_email`) over **streamable HTTP**. It is consumed by [`ai-agent`](../ai-agent/) via [`langchain-mcp-adapters`](https://github.com/langchain-ai/langchain-mcp-adapters), which forwards the OBO bearer token issued by `token-exchange`. Every `tools/call` is authenticated by validating the OBO JWT against Keycloak's JWKS — signature, audience, issuer, and expiry are all enforced before any tool runs — *and* re-checked against the tool's declared scope contract. The data layer is pluggable: a `file` backend serves the in-memory JSON list (dev) and a `postgres` backend uses asyncpg against PostgreSQL 16. In the postgres backend, database credentials are issued **per request** by HashiCorp Vault — the OBO token is exchanged at Vault's JWT auth method for short-lived Postgres credentials whose privilege level (read-only vs read/write) is bound to the OBO's scope.

### Tool → scope contract

Each tool declares the OAuth scopes it needs in its FastMCP `meta` (surfaced
under `_meta.required_scopes` in `tools/list`). The mapping is the single
source of truth in `tools/users.py` (`TOOL_SCOPE_REQUIREMENTS`):

| Tool | Required scope |
| --- | --- |
| `list_all_users`, `search_users_by_first_name` | `users.read` |
| `create_user`, `update_user_by_email`, `delete_user_by_email` | `users.write` |

`auth/scope_check.py` consumes the same registry. Inside the dispatcher,
`_run_tool` calls `require_scopes(tool_name)` against the OBO's `scope` claim
(bound to a `ContextVar` by `JwtAuthMiddleware`) before any storage call is
made. A token without the required scope is rejected with `403
insufficient_scope` even if it passes JWT validation. Tools that have not been
registered with `register_tool_scopes` are denied by default.

### Unauthenticated discovery

`USER_MCP_ALLOW_UNAUTH_DISCOVERY=true` lets `tools/list` requests through with
no `Authorization` header. The middleware dispatches them with an anonymous
identity (empty scope), so any `tools/call` still fails the scope check —
discovery is the only thing that succeeds. This is intended for the
service-mesh deployment where `ai-agent` discovers the catalog once at
startup over a channel secured by Consul service-intentions. Leave it `false`
in any environment where the network is open.

## Current project structure

```text
user-mcp/
├── server.py                 # ASGI entrypoint (uvicorn server:app)
├── mcp_app.py                # FastMCP server + lifespan that owns the repo lifecycle
├── config.py                 # pydantic-settings, USER_MCP_* env vars
├── logging_utils.py          # Structured JSON logging + identity context binding
├── errors.py                 # AppError type
├── models.py                 # UserRecord (pydantic, extra=allow)
├── auth/
│   ├── context.py            # ContextVars carrying the active OBO token + scope
│   └── jwt_validator.py      # PyJWT + PyJWKClient + ASGI JwtAuthMiddleware
├── vault_client.py           # httpx-based Vault client (JWT login + DB creds)
├── tools/
│   └── users.py              # @mcp.tool() definitions, async wrappers around repo
├── storage/
│   ├── base.py               # UserRepository ABC + email normalization
│   ├── file_repo.py          # FileUserRepository (asyncio.Lock, in-memory)
│   ├── postgres_repo.py      # PostgresUserRepository (direct pool OR per-request Vault creds)
│   └── factory.py            # build_repository(settings)
├── tests/
│   ├── conftest.py
│   ├── test_jwt_validator.py
│   ├── test_file_repo.py
│   └── test_tools_e2e.py     # FastMCP in-memory Client end-to-end checks
├── pyproject.toml            # uv project definition
├── Dockerfile                # 2-stage uv container (mirrors token-exchange)
├── .env.example
├── users_repository.json     # Seed data for the file backend
└── README.md
```

## Architecture

The application exposes a single MCP endpoint over streamable HTTP:

- `POST {USER_MCP_PATH}` — JSON-RPC over streamable HTTP (default path `/mcp`).

High-level request flow:

1. The Starlette ASGI app receives a streamable-HTTP POST.
2. `JwtAuthMiddleware` extracts the `Authorization: Bearer …` header. The OBO JWT is validated against Keycloak's JWKS (signature, `aud`, `iss`, `exp`, `iat`) using `PyJWKClient` with a configurable cache TTL.
3. Validated identity (`preferred_username`, `agent_id`, `scope`, `sub`) is attached to the request scope and bound into the structured-logging context (so every log line is prefixed with `[user=<preferred_username> agent=<agent_id>]`). The raw OBO token and scope are also bound into request-scoped `ContextVar`s so the storage layer can use them.
4. The FastMCP server dispatches the JSON-RPC call to the appropriate tool.
5. The tool delegates to the configured `UserRepository`. In the Postgres + Vault mode the repo authenticates to Vault with the OBO and obtains scope-appropriate dynamic Postgres credentials before opening a fresh asyncpg connection, then closes it once the query completes.
6. Application errors raised by the repo layer are translated to FastMCP `ToolError` with stable codes (400 `invalid_request`, 404 `invalid_request`, 500 `agent_error`, 502 `agent_error` for Vault/DB transport failures).

If the OBO JWT is missing or invalid the middleware short-circuits with a JSON 401 (`invalid_request`, `invalid_token`, `expired_token`, `invalid_audience`, `invalid_issuer`) — the tool layer is never reached.

## Available tools

| Tool | Inputs | Output | Description |
| --- | --- | --- | --- |
| `list_all_users` | — | `UserRecord[]` | All users currently stored. |
| `search_users_by_first_name` | `first_name: str` | `UserRecord[]` | Exact, case-insensitive first-name match. |
| `create_user` | `user: UserRecord` | `UserRecord` | Create a new user. Email must be unique. Errors 400 on duplicate email. May block on a CIBA approval — see below. |
| `update_user_by_email` | `email: str`, `user: UserRecord` | `UserRecord` | Replace the user identified by `email`. Errors 404 if missing, 400 on email collision. |
| `delete_user_by_email` | `email: str` | `UserRecord` | Delete by email. Errors 404 if missing. May block on a CIBA approval — see below. |

`UserRecord` accepts `email` (required, RFC-validated) plus optional `first_name`, `last_name`, `ssn`, `phone`, `credit_card_number`, `ip_address`, and arbitrary additional fields (`extra="allow"`).

### CIBA gate on writes

Whether a write blocks on human approval is a Vault ACL policy decision, not
a code path: every write first probes `sys/capabilities-self` on
`ciba/<action>/<user>` (see `infra/local-minikube/keycloak.sh`'s
`ciba-list-users`/`ciba-write` policies). If the policy says `deny`, the
write proceeds silently on the caller's own OBO token. If it says `read`,
`user-mcp` starts Keycloak CIBA (`ciba_client.py`) with the caller as
`login_hint` and blocks until the human approves, denies, or the poll window
elapses (`USER_MCP_CIBA_POLL_TIMEOUT_SECONDS`, default 110s) — approval
happens at `USER_MCP_CIBA_APPROVE_URL` (the `ciba-channel` service), not in
the tool call itself. This runs *after* the existing `users.write` scope
check — both must pass. See [`KEYCLOAK_REALM_SETUP.md`](../KEYCLOAK_REALM_SETUP.md)
for setup.

## Storage backends

Selected by `USER_BACKEND`:

- `file` (default) — loads `USER_MCP_USERS_FILE` once at startup into an in-memory list guarded by an `asyncio.Lock`. Mutations are **not** written back to disk and are lost on restart. Intended for local dev.
- `postgres` — connects to PostgreSQL 16 using `USER_MCP_PG_URL` (host, port, database, options — but **no credentials**). Username/password are injected at connect time according to `USER_MCP_DB_AUTH_MODE`.

### Postgres credential modes

Selected by `USER_MCP_DB_AUTH_MODE`:

- `direct` — uses static `USER_MCP_DB_USER` / `USER_MCP_DB_PASSWORD` to create a long-lived asyncpg pool. **Test/dev only**: useful for proving connectivity to the database before introducing Vault. With `USER_MCP_AUTO_MIGRATE=true`, idempotent DDL creates the `users` table and supporting indexes on startup (only effective in this mode).
- `vault` (default, production) — every tool call mints fresh, short-lived Postgres credentials from HashiCorp Vault, opens a single asyncpg connection bound to the validated OBO identity, and closes it once the query completes. No DB credentials live on disk; nothing is shared across users.

### Vault flow (per request)

When `USER_MCP_DB_AUTH_MODE=vault`, the storage layer:

1. **Probes the CIBA switch** — `POST {VAULT_ADDR}/v1/auth/{USER_MCP_VAULT_JWT_PATH}/login` with `{"role": <jwt-role>, "jwt": <OBO>}` to get a Vault client token, then `sys/capabilities-self` on `ciba/<action>/<user>`. `deny` → proceed silently; `read` → start Keycloak CIBA and block for approval (see "CIBA gate on writes" above).
2. **Reads DB creds** — `GET {VAULT_ADDR}/v1/{<db-creds-path>}` with the Keycloak OBO (or CIBA-approved) JWT presented directly as `X-Vault-Token`. Vault 2.1's OAuth Resource Server validates it inline (issuer, signature, audience, plus the human ACL intersected with the `ai-agent` Agent Registry ceiling), calls Postgres as its admin user, runs the role's `creation_statements`, and returns a unique `username` / `password` with a TTL. The lease is revoked when the tool call finishes.

The repo then opens `asyncpg.connect(USER_MCP_PG_URL, user=…, password=…)`, runs the SQL, and closes the connection.

### Scope-driven privilege selection

The OBO token's `scope` claim drives both the Vault role and the DB credential path. A read-only OBO can never obtain write-capable DB credentials:

| OBO `scope` contains | JWT role used                          | DB credential path                                      | DB privileges granted   |
| -------------------- | -------------------------------------- | ------------------------------------------------------- | ----------------------- |
| `users.write`        | `USER_MCP_VAULT_JWT_WRITE_ROLE`        | `USER_MCP_VAULT_DB_WRITE_PATH` (defaults to `database/creds/user-mcp-write-role`) | `SELECT, INSERT, UPDATE, DELETE` on `users` |
| `users.read` (only)  | `USER_MCP_VAULT_JWT_READ_ROLE`         | `USER_MCP_VAULT_DB_READ_PATH` (defaults to `database/creds/user-mcp-read-role`)   | `SELECT` on `users`     |
| neither              | — (request rejected with 403)          | —                                                       | —                       |

If the OBO has both scopes, the write role is selected because it provides a superset of the read role's privileges. The Vault JWT role itself also enforces `bound_claims.scope` (glob match), so an OBO without the required scope is rejected by Vault even if the application tried to use the wrong role.

## Configuration

The service reads environment variables from the process environment and also loads a local `.env` file if present.

| Variable | Default | Purpose |
| --- | --- | --- |
| `USER_MCP_HOST` | `0.0.0.0` | Bind host. |
| `USER_MCP_PORT` | `8090` | Bind port. |
| `USER_MCP_PATH` | `/mcp` | Path the streamable-HTTP transport is mounted at. |
| `USER_MCP_LOG_LEVEL` | `INFO` | Logging level. |
| `USER_MCP_KEYCLOAK_BASE_URL` | (unset) | Keycloak issuer base URL, e.g. `https://keycloak.example.com`. JWKS URL is derived as `{base}/oauth2/jwks` unless overridden. Also used as the default `iss` value. |
| `USER_MCP_VERIFY_JWKS_URL` | (unset) | Explicit JWKS URL override. |
| `USER_MCP_AUDIENCE` | (unset) | Required `aud` claim value. The MCP server's identifier in Keycloak. |
| `USER_MCP_ISSUER` | (unset) | Required `iss` claim value. Defaults to `USER_MCP_KEYCLOAK_BASE_URL`. |
| `USER_MCP_JWKS_CACHE_SECONDS` | `3600` | JWKS cache lifespan for `PyJWKClient`. |
| `USER_MCP_BYPASS_AUTH` | `false` | When `true`, skip JWT validation and accept all requests with placeholder identity. Dev only. |
| `USER_MCP_ALLOW_UNAUTH_DISCOVERY` | `false` | When `true`, requests with no `Authorization` header are dispatched with an anonymous identity. `tools/list` succeeds; `tools/call` still fails the per-tool scope check. Use only when the network channel is secured at the mesh layer (e.g. Consul service-intentions). |
| `USER_BACKEND` | `file` | Storage backend: `file` or `postgres`. |
| `USER_MCP_USERS_FILE` | `./users_repository.json` | Seed file used when `USER_BACKEND=file`. |
| `USER_MCP_PG_URL` | (unset) | Postgres URL **without credentials**, e.g. `postgresql://users-db.users-mcp.svc.cluster.local:5432/users?sslmode=disable`. Required for `USER_BACKEND=postgres`. |
| `USER_MCP_DB_AUTH_MODE` | `vault` | DB credential source: `vault` (production) or `direct` (static creds for connectivity testing). |
| `USER_MCP_AUTO_MIGRATE` | `false` | When `true`, run idempotent CREATE TABLE / INDEX statements at startup. **Only effective in `direct` mode** — Vault-issued roles lack DDL privileges. |
| `USER_MCP_DB_USER` | (unset) | Static Postgres username. Required when `USER_MCP_DB_AUTH_MODE=direct`. |
| `USER_MCP_DB_PASSWORD` | (unset) | Static Postgres password. Required when `USER_MCP_DB_AUTH_MODE=direct`. |
| `USER_MCP_VAULT_ADDR` | (unset) | Vault address, e.g. `http://vault.example.internal:8200`. Required when `USER_MCP_DB_AUTH_MODE=vault`. |
| `USER_MCP_VAULT_NAMESPACE` | (unset) | Vault Enterprise namespace. Leave empty for OSS. |
| `USER_MCP_VAULT_VERIFY_TLS` | `true` | Verify Vault's TLS certificate. |
| `USER_MCP_VAULT_TIMEOUT_SECONDS` | `10` | HTTP timeout for Vault calls. |
| `USER_MCP_CIBA_KEYCLOAK_URL` | (unset) | Keycloak base URL for the CIBA backchannel endpoints. |
| `USER_MCP_CIBA_REALM` | `demo` | Keycloak realm name. |
| `USER_MCP_CIBA_CLIENT_ID` | `ciba-client` | Keycloak CIBA client id. |
| `USER_MCP_CIBA_CLIENT_SECRET` | (unset) | Keycloak CIBA client secret. |
| `USER_MCP_CIBA_POLL_TIMEOUT_SECONDS` | `110` | How long a write blocks waiting for CIBA approval before giving up. |
| `USER_MCP_CIBA_APPROVE_URL` | `http://localhost:8093` | Where a human goes to approve/deny a pending CIBA request (the `ciba-channel` service). |
| `USER_MCP_VAULT_JWT_PATH` | `jwt-keycloak` | Vault JWT auth mount used only to probe the CIBA-required-by-policy switch. |
| `USER_MCP_VAULT_JWT_READ_ROLE` | `user-mcp-oidc-read` | JWT auth role name selected when the OBO scope grants only `users.read`. |
| `USER_MCP_VAULT_JWT_WRITE_ROLE` | `user-mcp-oidc-write` | JWT auth role name selected when the OBO scope grants `users.write`. |
| `USER_MCP_VAULT_DB_READ_PATH` | `database/creds/user-mcp-read-role` | Vault path that issues read-only Postgres credentials (read via Vault's OAuth Resource Server, X-Vault-Token = the OBO/CIBA JWT). |
| `USER_MCP_VAULT_DB_WRITE_PATH` | `database/creds/user-mcp-write-role` | Vault path that issues read/write Postgres credentials. |

Logs are emitted as JSON to stderr with the same field shape as `ai-agent` (`timestamp`, `level`, `logger`, `hostname`, `host_ip`, `process_id`, `module`, `function`, `method_name`, `line_number`, `message`, plus per-request `request_id`, `preferred_username`, `actor_agent_id`, `auth_scope`). Tool entry/failure are logged at INFO; tool success and JWKS cache hits are at DEBUG.

## Local development with uv

Create and sync the environment:

```bash
uv venv .venv
source .venv/bin/activate
uv sync --dev
```

Run tests:

```bash
python -m pytest -q
```

The Postgres tests are marked `@pytest.mark.integration` and skipped by default. To run them, point `USER_MCP_PG_DSN` at a local Postgres and run:

```bash
USER_MCP_PG_DSN=postgresql://user:pass@localhost:5432/users \
  python -m pytest -q -m integration
```

## Start the application directly

1. Copy and edit `.env`:

```bash
cp .env.example .env
# For first-time smoke testing, set USER_MCP_BYPASS_AUTH=true to skip JWT.
```

2. Start the service:

```bash
uv run uvicorn server:app --host 0.0.0.0 --port 8090
```

The streamable-HTTP endpoint is `http://localhost:8090/mcp`.

## Build the container

The Dockerfile works with both regular `docker build` and `docker buildx` multi-architecture builds.

From the `user-mcp` directory:

```bash
docker build -t agentguard-user-mcp .
```

Build a specific architecture and load it into the local Docker image store:

```bash
docker buildx build \
  --platform linux/amd64 \
  -t agentguard-user-mcp:amd64 \
  --load \
  .
```

Build and publish a multi-architecture image:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t your-registry/agentguard-user-mcp:latest \
  --push \
  .
```

## Start the application in a container

```bash
docker run --rm \
  -p 8090:8090 \
  --env-file .env \
  agentguard-user-mcp
```

For the Postgres backend, point `USER_MCP_PG_DSN` at a reachable host (`host.docker.internal` on macOS/Windows, the cluster service name on Kubernetes).

## Deploy on Kubernetes

The container is configured to listen on port `8090`. For Kubernetes, you need:

- an image pushed to a registry that your cluster can pull from
- a Secret generated from an env file containing the runtime configuration (`USER_MCP_*` and `USER_BACKEND`-specific entries)
- a Deployment that mounts that Secret as `.env` inside the container and a Service exposing port `8090`

Once the manifests land in `deploy-k8s/user-mcp.yaml` and `deploy-k8s/user-mcp.env`, deployment follows the same pattern documented in [`deploy-k8s/README.md`](../deploy-k8s/README.md):

```bash
kubectl create namespace user-mcp
kubectl create secret generic user-mcp-env \
  --from-env-file=deploy-k8s/user-mcp.env \
  -n user-mcp
kubectl apply -f deploy-k8s/user-mcp.yaml -n user-mcp
```

## Manual test steps

### 1. Smoke-test with `USER_MCP_BYPASS_AUTH=true`

```bash
# In .env: USER_MCP_BYPASS_AUTH=true, USER_BACKEND=file
uv run uvicorn server:app --port 8090
```

In another terminal, drive the server with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector --transport streamable-http http://localhost:8090/mcp
```

In the inspector, run `tools/list` (you should see all five tools), then call `list_all_users` with `{}`, and verify the seed data comes back.

Or with raw `curl`:

```bash
curl -N -X POST http://localhost:8090/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer dummy' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

### 2. Validate JWT enforcement (BYPASS off)

Disable bypass in `.env`, restart the server, and confirm 401 on a bad token:

```bash
curl -i -X POST http://localhost:8090/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer not-a-jwt' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
# expect: HTTP/1.1 401  {"error":"invalid_token","message":"..."}
```

Then run with a real OBO token issued by `token-exchange` and confirm 200.

### 3. End-to-end via `ai-agent`

```bash
# Terminal 1
cd user-mcp && uv run uvicorn server:app --port 8090
# Terminal 2 — ai-agent/.env: USER_MCP_URL=http://localhost:8090/mcp
cd ai-agent && uv run uvicorn agent_api:app --port 8000
# Terminal 3
curl -N -X POST http://localhost:8000/v1/agent/query \
  -H "Authorization: Bearer ${BEARER_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"List all users"}]}'
```

In `user-mcp` logs you should see a JSON line per tool invocation with `event: tool_invoked`, `tool: list_all_users`, `required_scopes: ["users.read"]`, and the `[user=<preferred_username> agent=<agent_id>]` prefix on `message`. If the OBO doesn't satisfy the tool's scope contract, expect a `tool_failed` line with `error: insufficient_scope` instead.

Exercise each tool with natural-language prompts:

- "List all users" → `list_all_users`
- "Find users named Noah" → `search_users_by_first_name`
- "Create a user with email demo@example.com, first name Demo" → `create_user`
- "Update demo@example.com to last name X" → `update_user_by_email`
- "Delete demo@example.com" → `delete_user_by_email`

### 4. Verify Vault auth and dynamic Postgres credentials with a pre-generated OBO token

If you already have an OBO token in hand (for example, fetched from `ai-agent`'s `GET /v1/agent/tokens` after a prior happy-path call, or minted directly by `token-exchange`), you can drive `user-mcp` directly to confirm Vault authentication and dynamic Postgres credential issuance work end to end. This isolates the Vault/DB path from `ai-agent`, the LLM, and OBO exchange.

Prerequisites:

- `user-mcp` running with `USER_BACKEND=postgres`, `USER_MCP_DB_AUTH_MODE=vault`, `USER_MCP_BYPASS_AUTH=false`, and the `USER_MCP_VAULT_*` variables populated.
- The OBO token's `aud` and `iss` claims match `USER_MCP_AUDIENCE` / `USER_MCP_ISSUER`, and its `scope` claim contains `users.read` and/or `users.write`.
- `USER_MCP_LOG_LEVEL=DEBUG` if you want to see the Vault events in the logs (they are logged at DEBUG).

Export the token and drive the MCP endpoint. The streamable-HTTP transport requires an MCP session handshake (`initialize` → `notifications/initialized`) before `tools/call`; the easiest way is via the MCP Inspector, which handles the handshake automatically:

```bash
export OBO_TOKEN="<paste-obo-token-here>"

npx @modelcontextprotocol/inspector --transport streamable-http http://localhost:8090/mcp
```

In the inspector UI, set the `Authorization` header to `Bearer ${OBO_TOKEN}`, then invoke `list_all_users` (read path) and `create_user` (write path) from the tools panel.

If you prefer raw `curl`, run the full three-step handshake — `tools/call` without a session ID returns `-32600 Bad Request: Missing session ID`:

```bash
export OBO_TOKEN="<paste-obo-token-here>"

# 1. initialize — capture the Mcp-Session-Id response header
curl -i -N -X POST http://localhost:8090/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${OBO_TOKEN}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'

# Copy the Mcp-Session-Id value from the response headers above:
export SID="<value-from-Mcp-Session-Id-header>"

# 2. notify the server that initialization is complete
curl -N -X POST http://localhost:8090/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${OBO_TOKEN}" \
  -H "Mcp-Session-Id: ${SID}" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'

# 3. Read path: exercises USER_MCP_VAULT_JWT_READ_ROLE + USER_MCP_VAULT_DB_READ_PATH
curl -N -X POST http://localhost:8090/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${OBO_TOKEN}" \
  -H "Mcp-Session-Id: ${SID}" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "list_all_users",
      "arguments": {}
    }
  }'
```

Expected outcome: `200` with a JSON-RPC `result` object containing the user records. The `user-mcp` process emits two events that prove the Vault flow ran:

- `vault_login_ok` — Vault accepted the OBO at the JWT auth method and returned a client token. The `role` field shows whether the read or write JWT role was selected.
- `vault_db_creds_issued` — Vault returned dynamic Postgres credentials. The `creds_path` and `lease_duration` fields are included.

To exercise the write path (`USER_MCP_VAULT_JWT_WRITE_ROLE` + `USER_MCP_VAULT_DB_WRITE_PATH`), the OBO must carry `users.write` in `scope`. Reuse the same `${SID}` from the handshake above:

```bash
curl -N -X POST http://localhost:8090/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${OBO_TOKEN}" \
  -H "Mcp-Session-Id: ${SID}" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "create_user",
      "arguments": {
        "user": {
          "email": "vault-test@example.com",
          "first_name": "Vault",
          "last_name": "Test"
        }
      }
    }
  }'
```

Common failure modes:

- `401` with `invalid_token` / `expired_token` / `invalid_audience` / `invalid_issuer`: the OBO failed JWT validation in `JwtAuthMiddleware` before any Vault call was made. Check `aud`, `iss`, and `exp` against the `USER_MCP_*` configuration.
- `200` with a JSON-RPC `error` object whose code maps to `agent_error` and whose message references Vault: JWT validation passed but Vault rejected the login or the DB creds read. `vault_login_ok` will be absent in the logs when login fails; check the Vault audit log to see whether `bound_audiences`/`bound_claims.scope` mismatched.
- `tools/call` for `create_user` returning a 403 `permission_denied` from Vault while `list_all_users` works: the OBO has only `users.read` in `scope`, so the write JWT role rejected it. This is expected behavior.

## Vault integration reference

The Vault and Postgres infrastructure backing this service is provisioned by the Terraform module at [`infra/modules/consul-client-k8s/vault_db.tf`](../infra/modules/consul-client-k8s/vault_db.tf). The defaults compiled into `user-mcp` (JWT auth path, role names, DB credential paths) match that module out of the box. If you stand up Vault by hand, the equivalent steps are:

1. **Enable the JWT auth method** at the configured path and bind it to Keycloak's OIDC discovery URL:

   ```bash
   vault auth enable -path=jwt-user-mcp jwt
   vault write auth/jwt-user-mcp/config \
     oidc_discovery_url="${USER_MCP_KEYCLOAK_BASE_URL%/}/oauth2" \
     bound_issuer="${USER_MCP_KEYCLOAK_BASE_URL%/}/oauth2"
   ```

2. **Create one JWT role per scope.** Each role binds the OBO audience and scope claim so only the matching tokens can mint the matching credentials:

   ```bash
   vault write auth/jwt-user-mcp/role/user-mcp-read \
     role_type=jwt user_claim=preferred_username \
     bound_audiences=user-mcp \
     bound_claims_type=glob bound_claims='{"scope":"*users.read*"}' \
     token_policies=user-mcp-db-read ttl=5m max_ttl=15m

   vault write auth/jwt-user-mcp/role/user-mcp-write \
     role_type=jwt user_claim=preferred_username \
     bound_audiences=user-mcp \
     bound_claims_type=glob bound_claims='{"scope":"*users.write*"}' \
     token_policies=user-mcp-db-write ttl=5m max_ttl=15m
   ```

3. **Enable the database secrets engine** and create read-only and read/write Postgres roles:

   ```bash
   vault secrets enable database
   vault write database/config/users-db \
     plugin_name=postgresql-database-plugin \
     allowed_roles="user-mcp-read-role,user-mcp-write-role" \
     connection_url='postgresql://{{username}}:{{password}}@<host>:5432/users?sslmode=disable' \
     username="vault-admin" password="<rotated-out-of-band>"

   vault write database/roles/user-mcp-read-role db_name=users-db \
     creation_statements="CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}'; \
       GRANT CONNECT ON DATABASE users TO \"{{name}}\"; \
       GRANT USAGE ON SCHEMA public TO \"{{name}}\"; \
       GRANT SELECT ON users TO \"{{name}}\";" \
     default_ttl=1h max_ttl=24h

   vault write database/roles/user-mcp-write-role db_name=users-db \
     creation_statements="CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}'; \
       GRANT CONNECT ON DATABASE users TO \"{{name}}\"; \
       GRANT USAGE ON SCHEMA public TO \"{{name}}\"; \
       GRANT SELECT, INSERT, UPDATE, DELETE ON users TO \"{{name}}\";" \
     default_ttl=1h max_ttl=24h
   ```

4. **Write Vault policies** that allow each JWT role to read only its own DB-creds path:

   ```bash
   vault policy write user-mcp-db-read - <<'HCL'
   path "database/creds/user-mcp-read-role" { capabilities = ["read"] }
   HCL

   vault policy write user-mcp-db-write - <<'HCL'
   path "database/creds/user-mcp-write-role" { capabilities = ["read"] }
   HCL
   ```

5. **The application performs two Vault calls per request.** No application action is required beyond setting the `USER_MCP_VAULT_*` environment variables — `vault_client.py` does the rest:

   ```text
   POST {VAULT_ADDR}/v1/auth/{USER_MCP_VAULT_JWT_PATH}/login   { role, jwt: "<obo>" }
   GET  {VAULT_ADDR}/v1/{USER_MCP_VAULT_DB_(READ|WRITE)_PATH}
   ```

   The fresh `username` / `password` are passed straight to `asyncpg.connect(USER_MCP_PG_URL, user=…, password=…)` and the connection is closed once the tool finishes.

This pattern keeps secrets off disk, bounds DB access to validated identity + scope, and produces short-lived, auditable credentials.

## Verifying connectivity with `direct` mode

Before exercising the full Vault flow, you can prove the service can reach Postgres by setting:

```ini
USER_BACKEND=postgres
USER_MCP_DB_AUTH_MODE=direct
USER_MCP_PG_URL=postgresql://localhost:5432/users?sslmode=disable
USER_MCP_DB_USER=postgres
USER_MCP_DB_PASSWORD=postgres
USER_MCP_AUTO_MIGRATE=true   # optional: lets user-mcp create the table
```

Then run with `USER_MCP_BYPASS_AUTH=true`, call `tools/list` and `list_all_users` via the inspector, and confirm the database is reachable. Once that works, flip `USER_MCP_DB_AUTH_MODE` back to `vault`, populate the `USER_MCP_VAULT_*` variables, and disable bypass.
