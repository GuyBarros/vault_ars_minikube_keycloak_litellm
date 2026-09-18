# Identity Broker

A Python-based identity broker that performs an **Keycloak on-behalf-of (OBO) token exchange** — takes a `subject_token` + `actor_token` + `scope` and returns an Keycloak access token on behalf of the subject, carrying *only* the requested scopes.

Exposes the OBO flow as a FastAPI REST service with in-memory TTL caching keyed by `(subject_token, actor_token, normalized_scope)`. Different scope sets for the same caller never share a cache entry.

### Per-tool scoped OBO flow

`ai-agent` no longer exchanges one OBO per request. It calls
`POST /v1/identity/obo-token` once per tool invocation, sending the scope set
that the tool itself declares (e.g. `users.read` for read tools,
`users.write` for create/update/delete). The broker forwards `scope`
verbatim to Keycloak (RFC 8693 `scope` parameter); a token issued for
`users.read` can never authorize a `users.write` tool downstream.

### Pre-exchange authorization gate

Before any outbound call to Keycloak, the broker enforces a local
groups-vs-scope check. It decodes the `subject_token` (without signature
verification), reads the `groups` claim, and verifies that every requested
scope is permitted by at least one of the caller's groups. The check fails
closed: a missing/malformed `groups` claim or an unknown scope token both
deny.

Default policy (hardcoded in `keycloak/authorization.py:SCOPE_REQUIREMENTS`):

| Scope          | Allowed groups                   |
|----------------|----------------------------------|
| `users.read`   | `reader`, `writer`, `admin`      |
| `users.write`  | `writer`, `admin`                |

Denied requests return `403 Forbidden` with a descriptive `detail` and emit
an `obo_token_exchange_authz_denied` log event. Cached tokens are never
returned on a denial — the check runs before the cache lookup.

Each call emits one `event=verify_obo_token_exchange` log line with
`request_id`, `cache_hit`, `scope`, `duration_ms`, and a `[user=<preferred_username>
agent=<agent_id>] OBO token exchange (cache hit|issued by Keycloak) for
scope='<scope>'` message — enough on its own to trace the per-tool flow
across all three services.

## Architecture

```
Client Application
      │
      │ REST API
      ▼
Identity Broker API  (FastAPI + Uvicorn)
      │
      └── POST /v1/identity/obo-token ──► OBOBroker ──────────► Keycloak
```

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- A Keycloak realm (for the OBO flow)
- Docker with [Buildx](https://docs.docker.com/buildx/working-with-buildx/) plugin (for multi-platform builds)

## Installation

```bash
# Install dependencies
uv sync

# Install with dev dependencies (for testing)
uv sync --group dev
```

## Configuration

All settings use the `IDENTITY_BROKER_` prefix and can be set as environment variables:

### Keycloak OBO settings

| Variable | Default | Description |
|---|---|---|
| `IDENTITY_BROKER_KEYCLOAK_URL` | _(unset)_ | Keycloak realm base URL, e.g. `http://keycloak.virtual.consul` |
| `IDENTITY_BROKER_KEYCLOAK_REALM` | `demo` | Keycloak realm name |
| `IDENTITY_BROKER_KEYCLOAK_TOKEN_EXCHANGE_AUDIENCE` | `user-mcp` | `audience` param sent on the token-exchange request |
| `IDENTITY_BROKER_OBO_CLIENT_ID` | _(unset)_ | Client ID for the OBO token exchange (confidential client) |
| `IDENTITY_BROKER_OBO_CLIENT_SECRET` | _(required)_ | Client secret for the confidential token-exchange app. The broker fails to start if this is missing or empty. |

CIBA (human-in-the-loop write approval) is handled entirely by `user-mcp`
against Keycloak directly — see [`../user-mcp/README.md`](../user-mcp/README.md)
and [`KEYCLOAK_REALM_SETUP.md`](../KEYCLOAK_REALM_SETUP.md). token-exchange has
no CIBA-specific settings or endpoints.

### Cache and service settings

| Variable | Default | Description |
|---|---|---|
| `IDENTITY_BROKER_CACHE_TTL` | `3600` | Cache TTL in seconds |
| `IDENTITY_BROKER_CACHE_MAXSIZE` | `1024` | Maximum cached tokens |
| `IDENTITY_BROKER_LOG_LEVEL` | `INFO` | Log level |

Uvicorn server settings:

| Variable | Default | Description |
|---|---|---|
| `UVICORN_HOST` | `0.0.0.0` | Interface to bind |
| `UVICORN_PORT` | `8080` | Port to listen on |

Example:

```bash
export IDENTITY_BROKER_KEYCLOAK_URL="https://keycloak.example.com"
export IDENTITY_BROKER_OBO_CLIENT_ID="<redacted-obo-client-id>"
export IDENTITY_BROKER_OBO_CLIENT_SECRET="<redacted-obo-client-secret>"

export UVICORN_HOST=0.0.0.0
export UVICORN_PORT=9090
```

### Using a `.env` file

You can also keep the service configuration in a `.env` file at the app root. The service loads it automatically through Pydantic settings:

```bash
cat > .env <<'EOF'
IDENTITY_BROKER_KEYCLOAK_URL=https://keycloak.example.com
IDENTITY_BROKER_OBO_CLIENT_ID=<redacted-obo-client-id>
IDENTITY_BROKER_OBO_CLIENT_SECRET=<redacted-obo-client-secret>
UVICORN_HOST=0.0.0.0
UVICORN_PORT=8080
EOF
```

If the same variable is present both in `.env` and in the environment, the environment variable wins.

## Running the Service

### Local (uv)

The server reads `UVICORN_HOST` and `UVICORN_PORT` from the environment (defaults: `0.0.0.0` and `8080`). Do **not** pass `--host`/`--port` flags — they override env vars.

```bash
# Use defaults (0.0.0.0:8080)
uv run uvicorn api.main:app

# Override host/port via env vars
export UVICORN_HOST=127.0.0.1
export UVICORN_PORT=9090
uv run uvicorn api.main:app
```

## Logging

The service emits structured JSON logs for both application loggers and uvicorn loggers (`uvicorn`, `uvicorn.error`, `uvicorn.access`), which makes the default output suitable for Loki ingestion. Each record includes standard fields such as `timestamp`, `level`, `logger`, `service`, `hostname`, `host_ip` (when resolvable), callsite metadata (`module`, `func_name`, `lineno`, `process`, `thread_name`), and per-request HTTP context (`request_id`, `http_method`, `http_path`, `http_scheme`, `client_ip`, `user_agent`) when available. Uvicorn access logs also expose structured `client_addr`, `http_method`, `http_path`, `http_version`, and `status_code` fields.

### Docker

The image sets `UVICORN_HOST=0.0.0.0` and `UVICORN_PORT=8080` as defaults. Override them with `-e` at runtime.

The Docker build installs only third-party dependencies into `/app/.venv` in the builder stage via `uv sync --no-install-project`, then copies that dependency-only virtualenv into the runtime image alongside the source tree. That keeps the runtime image offline-friendly and avoids reinstalling dependencies in the final stage.

#### Single-platform build (local development)

```bash
# Build for the current host platform
docker build -t agentguard-token-exchange .

# Run with OBO exchange enabled
docker run -p 8080:8080 \
  -e IDENTITY_BROKER_KEYCLOAK_URL="https://keycloak.example.com" \
  -e IDENTITY_BROKER_OBO_CLIENT_ID="<redacted-obo-client-id>" \
  -e IDENTITY_BROKER_OBO_CLIENT_SECRET="<redacted-obo-client-secret>" \
  agentguard-token-exchange

# Override host/port
docker run -p 9090:9090 \
  -e UVICORN_HOST=0.0.0.0 \
  -e UVICORN_PORT=9090 \
  -e IDENTITY_BROKER_KEYCLOAK_URL="https://keycloak.example.com" \
  -e IDENTITY_BROKER_OBO_CLIENT_ID="<redacted-obo-client-id>" \
  -e IDENTITY_BROKER_OBO_CLIENT_SECRET="<redacted-obo-client-secret>" \
  agentguard-token-exchange
```

#### Multi-platform build (arm64 + amd64)

Requires [Docker Buildx](https://docs.docker.com/buildx/working-with-buildx/) and a builder that supports multi-platform builds.

```bash
# Create and activate a multi-platform builder (one-time setup)
docker buildx create --name multiarch --use
docker buildx inspect --bootstrap

# Build and push a multi-arch image to a registry
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t <registry>/agentguard-token-exchange:<tag> \
  --push .

# Build locally for a specific target platform (no push)
docker buildx build \
  --platform linux/arm64 \
  -t agentguard-token-exchange:arm64 \
  --load .
```

### Kubernetes

For Kubernetes, store the `.env` file as a Secret and mount it at `/app/.env`. The image already starts with the Dockerfile default command, `CMD ["uvicorn", "api.main:app"]`, and the app reads `/app/.env` automatically. Environment variables defined in the pod override values from the mounted `.env` file.

#### 1. Create the Secret from `.env`

```bash
kubectl create secret generic token-exchange-env \
  --from-file=.env=.env
```

#### 2. Deploy the service

This example mounts the Secret at the application root as `/app/.env`. The optional `IDENTITY_BROKER_KEYCLOAK_URL` environment variable overrides the value from the mounted `.env` file.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: token-exchange
spec:
  replicas: 1
  selector:
    matchLabels:
      app: token-exchange
  template:
    metadata:
      labels:
        app: token-exchange
    spec:
      containers:
        - name: token-exchange
          image: <registry>/agentguard-token-exchange:<tag>
          ports:
            - containerPort: 8080
          env:
            - name: UVICORN_HOST
              value: "0.0.0.0"
            - name: UVICORN_PORT
              value: "8080"
            # Optional: overrides the value from /app/.env.
            - name: IDENTITY_BROKER_KEYCLOAK_URL
              value: "https://keycloak.example.com"
          volumeMounts:
            - name: token-exchange-env
              mountPath: /app/.env
              subPath: .env
              readOnly: true
      volumes:
        - name: token-exchange-env
          secret:
            secretName: token-exchange-env
```

You can expose the deployment with a `Service`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: token-exchange
spec:
  selector:
    app: token-exchange
  ports:
    - name: http
      port: 8080
      targetPort: 8080
```

Apply both resources:

```bash
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

## API Reference

### GET /healthz

Liveness probe — returns `{"status": "ok"}`.

---

### POST /v1/identity/obo-token

Exchange a `subject_token` + `actor_token` for an Keycloak access token on behalf of the subject (RFC 8693 token exchange).

**Request body:**

| Field | Type | Description |
|---|---|---|
| `subject_token` | string | Caller's access token (JWT) — the identity to act on behalf of |
| `actor_token` | string | Token identifying the acting service |
| `scope` | string | Required, non-empty. Space-separated OAuth scopes (RFC 8693 `scope`) to request on the OBO. Forwarded verbatim to Keycloak; also part of the cache key. |

**Response body:**

| Field | Type | Description |
|---|---|---|
| `access_token` | string | Keycloak access token issued for the subject |
| `cached` | boolean | Whether the token was served from cache |

**HTTP status codes:**

| Code | Description |
|---|---|
| `200` | Success |
| `400` | Invalid request body |
| `401` | Keycloak authentication failure |
| `403` | Subject's groups don't entitle the requested scope (local authz gate) |
| `500` | Token exchange error |
| `503` | Keycloak unreachable |

---

## Testing the API

### Using curl

#### Health check

```bash
curl http://localhost:8080/healthz
```

**Expected response:**

```json
{"status": "ok"}
```

#### OBO token exchange

```bash
curl -X POST http://localhost:8080/v1/identity/obo-token \
  -H "Content-Type: application/json" \
  -d '{
    "subject_token": "<redacted-subject-token>",
    "actor_token": "<redacted-actor-token>",
    "scope": "users.read"
  }'
```

**Expected response (200 OK):**

```json
{
  "access_token": "<redacted-access-token>",
  "cached": false
}
```

#### View interactive API docs (Swagger UI)

```bash
open http://localhost:8080/docs
```

---

### Using Postman

#### Step 1 — Create a new request

1. Open Postman and click **New → HTTP Request**.
2. Set the method to **POST**.
3. Enter the URL: `http://localhost:8080/v1/identity/obo-token`

#### Step 2 — Set the request body

1. Click the **Body** tab.
2. Select **raw** and choose **JSON** from the format dropdown.
3. Paste the following:

```json
{
  "subject_token": "<redacted-subject-token>",
  "actor_token": "<redacted-actor-token>",
  "scope": "users.read"
}
```

#### Step 3 — (Optional) Add a correlation header

1. Click the **Headers** tab.
2. Add: `X-Request-ID` → `my-trace-id-123`

#### Step 4 — Send the request

Click **Send**. You should receive a `200 OK` with the exchanged access token in the response body.

#### Step 5 — Health check

1. Create a new **GET** request.
2. URL: `http://localhost:8080/healthz`
3. Click **Send** — expect `{"status": "ok"}`.

---

### Error scenarios

#### Authorization denied (403)

When the `subject_token`'s `groups` claim doesn't entitle the requested
scope (e.g. a `["reader"]` user requesting `users.write`):

```json
{"detail": "user groups ['reader'] are not authorized for scope 'users.write' (requires one of ['admin', 'writer'])"}
```

#### Keycloak authentication failure (401)

```bash
curl -X POST http://localhost:8080/v1/identity/obo-token \
  -H "Content-Type: application/json" \
  -d '{
    "subject_token": "<redacted-subject-token>",
    "actor_token": "<redacted-invalid-actor-token>",
    "scope": "users.read"
  }'
```

```json
{"detail": "Keycloak authentication failure"}
```

#### Missing required field (400)

```bash
curl -X POST http://localhost:8080/v1/identity/obo-token \
  -H "Content-Type: application/json" \
  -d '{
    "subject_token": "<redacted-subject-token>",
    "actor_token": "<redacted-actor-token>"
  }'
```

```json
{"detail": [{"msg": "Field required", "loc": ["body", "scope"]}]}
```

#### Keycloak unreachable (503)

```json
{"detail": "Keycloak is unavailable"}
```

---

## Running Tests

```bash
uv run pytest
```

```bash
# With verbose output
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_routes.py -v
```

## Project Structure

```
.
├── api/
│   ├── main.py         # FastAPI app, middleware, lifespan
│   └── routes.py       # POST /v1/identity/obo-token, GET /healthz
├── keycloak/
│   ├── keycloak_client.py # KeycloakTokenExchangeClient (HTTP OBO token exchange)
│   ├── authorization.py   # Pre-exchange groups-vs-scope authz gate
│   └── obo_broker.py      # OBOBroker (exchange_obo_token)
├── models/
│   └── schemas.py      # Request/response schemas
├── app_logging/
│   └── logger.py       # structlog structured JSON logger
├── exceptions/
│   └── errors.py       # Service exception hierarchies
└── config/
    └── settings.py     # Pydantic settings (env vars)
```

`app_logging/` is intentionally named to avoid shadowing Python's standard-library `logging` module now that the source packages live at the repository root.

## Security Notes

- Subject tokens, actor tokens, and Keycloak access tokens are **never logged**.
- Cache keys are hashed before storage; raw tokens are never stored as cache keys.
- `IDENTITY_BROKER_KEYCLOAK_URL` and `IDENTITY_BROKER_OBO_CLIENT_ID` must be set before starting the service if the OBO endpoint will be used.
