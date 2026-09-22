# User MCP

This service is a FastMCP-based Model Context Protocol server that exposes user-management tools (`list_all_users`, `search_users_by_first_name`, `create_user`, `delete_user_by_email`, `update_user_by_email`) over **streamable HTTP**. In the mesh lab, [`ai-agent`](../ai-agent/) talks MCP to LiteLLM (`/user_mcp/mcp`); LiteLLM is the PEP (JWT, catalog, scope, LoA) and this process is the **runtime** (`USER_MCP_PEP_MODE=runtime`): it extracts the inbound bearer without JWKS, skips the scope check, and still presents the JWT to Vault as `X-Vault-Token` for `database/creds` and Transform (Vault revalidates a required step-up via the JWT's `acr` claim). Set `USER_MCP_PEP_MODE=local` to run this server as the PEP again (JWKS + `scope_check`).

### Tool → scope contract

Each tool declares the OAuth scopes it needs in its FastMCP `meta` (surfaced
under `_meta.required_scopes` in `tools/list`). The mapping is the single
source of truth in `tools/users.py` (`TOOL_SCOPE_REQUIREMENTS`):

| Tool | Required scope |
| --- | --- |
| `list_all_users`, `search_users_by_first_name` | `users.read` |
| `create_user`, `update_user_by_email`, `delete_user_by_email` | `users.write` |

`_run_tool` chama `require_scopes` **só em `USER_MCP_PEP_MODE=local`**. No lab (`runtime`) o ALLOW/DENY de scope é o LiteLLM + OPA `mcp.pep`; este servidor só declara `_meta.required_scopes` para o agente montar o OBO.

### Unauthenticated discovery

`USER_MCP_ALLOW_UNAUTH_DISCOVERY=true` lets `tools/list` through with no `Authorization` header (anonymous identity). No lab o ALLOW/DENY de `tools/call` é o LiteLLM (`pdp_mcp`); discovery sem Bearer continua necessário para o agente no startup. Leave it `false` if the network is open.

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
│   └── jwt_validator.py      # runtime: decode unverified; local: PyJWT + JWKS
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

High-level request flow (`USER_MCP_PEP_MODE=runtime`, o `make up`):

1. The Starlette ASGI app receives a streamable-HTTP POST (from LiteLLM, not from the agent).
2. `JwtAuthMiddleware` extracts `Authorization: Bearer …` and **decodes without JWKS** (`jwt_identity_bound`). LiteLLM already validated the token and, for `create_user`, already required a step-up.
3. Identity is bound into logging `ContextVar`s so `database/creds` / Transform still see `preferred_username`, `scope`, `groups`.
4. The FastMCP server dispatches the JSON-RPC call. The scope check is skipped.
5. The tool delegates to `UserRepository`. In Postgres + Vault mode the repo presents the inbound JWT as `X-Vault-Token` and mints dynamic Postgres credentials, then closes the connection. If the JWT's `acr` claim requires it, the repo first logs in to Vault's `jwt-keycloak` mount, which independently revalidates the step-up via `bound_claims.acr`.
6. Application errors become FastMCP `ToolError`.

`USER_MCP_PEP_MODE=local` restores JWKS + `require_scopes` in this process (not the lab path). See [`documentation/arquitetura-detalhada.md`](../documentation/arquitetura-detalhada.md).

## Available tools

| Tool | Inputs | Output | Description |
| --- | --- | --- | --- |
| `list_all_users` | — | `UserRecord[]` | All users currently stored. |
| `search_users_by_first_name` | `first_name: str` | `UserRecord[]` | Exact, case-insensitive first-name match. |
| `create_user` | `user: UserRecord` | `UserRecord` | Create a new user. Email must be unique. Errors 400 on duplicate email. Requires a Keycloak step-up (LoA 2) — see below. |
| `update_user_by_email` | `email: str`, `user: UserRecord` | `UserRecord` | Replace the user identified by `email`. Errors 404 if missing, 400 on email collision. |
| `delete_user_by_email` | `email: str` | `UserRecord` | Disabled at the PEP (`disabled_tools` in `mcp_pep.rego`); the agent is never given this tool. |

`UserRecord` accepts `email` (required, RFC-validated) plus optional `first_name`, `last_name`, `ssn`, `phone`, `credit_card_number`, `ip_address`, and arbitrary additional fields (`extra="allow"`).

### Step-up (LoA) gate on `create_user`

**Lab (`USER_MCP_PEP_MODE=runtime`):** LiteLLM + OPA `loa2_tools` (`create_user`) deny with `step_up_required` until the caller's JWT carries `acr=2`; the web app then redirects the human through a Keycloak OTP step-up. This process does not talk to Keycloak.

**Both modes:** on the write path, `user-mcp` logs in to Vault's `jwt-keycloak` mount with the `user-mcp-oidc-write` role, whose `bound_claims` require `acr: "2"` — Vault independently revalidates the step-up rather than trusting the PEP alone. See [`KEYCLOAK_REALM_SETUP.md`](../KEYCLOAK_REALM_SETUP.md).

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

1. **If the tool requires LoA 2** (`create_user`), logs in to Vault's `jwt-keycloak` mount with the inbound JWT to revalidate the step-up (see above). Otherwise skipped.
2. **Reads DB creds** — `GET {VAULT_ADDR}/v1/{<db-creds-path>}` with the inbound JWT as `X-Vault-Token`. Vault OAuth Resource Server validates it, returns unique `username` / `password`. The lease is revoked when the tool call finishes.

The repo then opens `asyncpg.connect(USER_MCP_PG_URL, user=…, password=…)`, runs the SQL, and closes the connection.

### Scope-driven privilege selection

The OBO token's `scope` claim drives both the Vault role and the DB credential path. A read-only OBO can never obtain write-capable DB credentials:

| OBO `scope` contains | JWT role used                          | DB credential path                                      | DB privileges granted   |
| -------------------- | -------------------------------------- | ------------------------------------------------------- | ----------------------- |
| `users.write`        | `USER_MCP_VAULT_JWT_WRITE_ROLE`        | `USER_MCP_VAULT_DB_WRITE_PATH` (defaults to `database/creds/user-mcp-write-role`) | `SELECT, INSERT, UPDATE` on `users` (no `DELETE`) |
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
| `USER_MCP_VAULT_JWT_PATH` | `jwt-keycloak` | Vault JWT auth mount used to revalidate a completed step-up. |
| `USER_MCP_VAULT_JWT_READ_ROLE` | `user-mcp-oidc-read` | JWT auth role name selected when the OBO scope grants only `users.read`. |
| `USER_MCP_VAULT_JWT_WRITE_ROLE` | `user-mcp-oidc-write` | JWT auth role name selected when the OBO scope grants `users.write`; its `bound_claims` require `acr: "2"`. |
| `USER_MCP_VAULT_DB_READ_PATH` | `database/creds/user-mcp-read-role` | Vault path that issues read-only Postgres credentials (read via Vault's OAuth Resource Server, X-Vault-Token = the OBO JWT). |
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
       GRANT SELECT, INSERT, UPDATE ON users TO \"{{name}}\";" \
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
