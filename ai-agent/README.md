# AI Agent

This service is a FastAPI-based AI agent runtime. It accepts chat messages, validates a bearer token, loads user-management tools from the LiteLLM MCP gateway (`USER_MCP_URL` → `/user_mcp/mcp`) over streamable HTTP once at startup, then — for each tool the LLM picks — exchanges the user's bearer token for an on-behalf-of (OBO) token carrying *only* the scopes that tool declares and invokes the tool **through LiteLLM**. A local `shell` tool is also available. See [`documentation/arquitetura-detalhada.md`](../documentation/arquitetura-detalhada.md).

### Tool → scope contract (per-call OBO exchange)

OBO exchange used to happen once per request, baking a fixed scope into the
upstream client. Now it follows the tool's own contract:

1. **Startup discovery (one-shot).** In the FastAPI lifespan the agent calls
   `tools/list` against `USER_MCP_URL` with no Authorization header and caches
   the resulting LangChain tool templates in `app.state.mcp_template_tools`.
   This requires `USER_MCP_ALLOW_UNAUTH_DISCOVERY=true` on `user-mcp`. The
   service-to-service channel is presumed secured at the mesh layer (Consul
   service-intentions), so no token is needed for discovery.
2. **Per request.** Validate the bearer; for each cached template, read its
   `_meta.required_scopes` and wrap it (`scoped_tool.make_scoped_tool`) so the
   wrapper closes over `(subject_token, request_id, required_scopes)`.
3. **Per tool call (when the LLM picks a tool).** The wrapper asks
   `OboTokenService.resolve_token(scopes=required_scopes)` for an OBO carrying
   *only* those scopes (cached by `(subject_token, role, frozenset(scopes))`),
   builds a transient MCP client with that OBO, and calls the upstream tool.
4. **Defense in depth no lab:** LiteLLM `pdp_mcp.py` + OPA `mcp.pep` re-check catalog and scope before the call reaches `user-mcp`. Em `USER_MCP_PEP_MODE=runtime` o MCP **não** revalida JWKS/scope/CIBA; em `local` o dispatcher ainda chama `require_scopes`.

A single line traces each call: `event=scoped_tool_invoke tool=<name>
required_scopes=<list>` at INFO. The token-exchange service emits one
`event=verify_obo_token_exchange` line per OBO with `cache_hit`, `scope`,
and identity prefix; `user-mcp` emits one `event=tool_invoked` line per tool
with `required_scopes`.

## Current project structure

```text
ai-agent/
├── agent_api.py           # FastAPI app, middleware, exception handlers, route wiring
├── agent_runtime.py       # LangChain orchestration and streaming response handling
├── config.py              # Environment-driven settings
├── identity.py            # Actor token loading, OBO exchange, in-memory cache
├── security.py            # Bearer token extraction and lightweight JWT validation
├── tools.py               # Local tools exposed to the agent (shell)
├── mcp_client.py          # Streamable-HTTP MCP client for user-mcp tool loading
├── models.py              # Request models
├── logging_utils.py       # Structured logging helpers
├── errors.py              # Application error type
├── test_agent_api.py      # API and token flow tests
├── pyproject.toml         # uv project definition
├── uv.lock                # Locked dependencies for uv
├── requirements.txt       # Legacy dependency list
├── Dockerfile             # Container build
└── SPEC.md                # Product and behavior notes
```

## Architecture

The application has two public endpoints:

- `POST /v1/agent/query`
- `GET /v1/agent/tokens`

It also serves an unauthenticated A2A agent card at `GET /.well-known/agent-card.json` (also at `/.well-known/agent.json` and `/agent.json`, since A2A clients look in different places by spec version). It carries public metadata only and exists for LiteLLM's Agents > Discovery; its `url` is derived from the request's host.

High-level request flow:

1. FastAPI receives the request and assigns a request ID.
2. The `Authorization: Bearer ...` header is validated unless bypass mode is enabled.
3. The actor token is read from `ACTOR_TOKEN_PATH` (used as the `actor_token` in OBO exchanges, not at request entry).
4. Each MCP tool template cached at startup is wrapped with a per-call OBO closure that captures the user's bearer token, the request ID, and the scopes that tool declares (`_meta.required_scopes`). The local `shell` tool is appended unwrapped.
5. LangChain binds the merged tool list and runs the agent flow. Each time the LLM picks an MCP tool, the wrapper exchanges an OBO scoped to that tool only (calling `TOKEN_EXCHANGE_URL`) — unless a matching OBO is already cached. OBOs are cached per `(subject_token, role, scope-set)`.
6. The wrapper builds a transient MCP client carrying the scoped OBO and invokes the upstream tool. `insufficient_scope` (403) responses are returned to the LLM as a `ToolMessage`.
7. The response is streamed back as `text/plain`.

At application startup the lifespan handler discovers MCP tool templates against `USER_MCP_URL` with no Authorization header and caches them in `app.state.mcp_template_tools`. `user-mcp` must run with `USER_MCP_ALLOW_UNAUTH_DISCOVERY=true` for this to succeed — the network channel is presumed secured at the mesh layer (Consul service-intentions). If discovery fails, an `mcp_discovery_failed_at_startup` warning is emitted and the service still boots; queries will run with no MCP tools until a restart succeeds.

The token lookup endpoint validates the incoming `Authorization: Bearer ...` header, derives the same cache key from the access token and configured role name, and returns both the cached OBO token and the agent actor token without triggering a new token exchange.

`GET /v1/agent/tokens` returns JSON in this shape:

```json
{
  "obo_token": "eyJ...",
  "actor_token": "eyJ..."
}
```

## Available tools

The agent exposes a single local tool plus the tools advertised by the upstream `user-mcp` server:

Local tool (defined in `tools.py`):

- `shell`: executes shell commands from the application directory.

Tools discovered from `user-mcp` once at startup and reused across requests (see [`user-mcp/README.md`](../user-mcp/README.md) for full schemas and the per-tool scope contract):

- `list_all_users`
- `search_users_by_first_name`
- `create_user`
- `update_user_by_email`
- `delete_user_by_email`

The agent does not own user data anymore. Storage and validation live in `user-mcp` and are governed by `USER_BACKEND` on that service (`file` or `postgres`). All MCP tool calls from `ai-agent` carry the OBO token in the `Authorization` header so `user-mcp` can enforce JWT validation and per-user authorization.

## Integration with `user-mcp`

`ai-agent` consumes `user-mcp` over the streamable-HTTP MCP transport via `langchain-mcp-adapters`. The integration boils down to:

1. Set `USER_MCP_URL` on the agent to the full upstream URL. Locally that is `user-mcp` (`USER_MCP_PATH`, default `/mcp`, e.g. `http://localhost:8090/mcp`). In cluster it is LiteLLM's MCP gateway (`http://litellm-gateway.virtual.consul:4000/user_mcp/mcp`).
2. Configure `user-mcp` so the OBO tokens minted for `OBO_ROLE_NAME` carry an `aud` claim that matches `USER_MCP_AUDIENCE` and an `iss` claim that matches `USER_MCP_ISSUER` (or `USER_MCP_KEYCLOAK_BASE_URL`).
3. The agent forwards the OBO bearer to `user-mcp` on every tool call. When `BYPASS_AUTH_TOKEN_EXCHANGE=true` on the agent, no bearer is forwarded, so `user-mcp` must run with `USER_MCP_BYPASS_AUTH=true` for the call to succeed.

Quick local bring-up in two terminals:

```bash
# Terminal A — user-mcp (dev mode, no JWT)
cd ../user-mcp
USER_MCP_BYPASS_AUTH=true uv run uvicorn server:app --host 0.0.0.0 --port 8090

# Terminal B — ai-agent (dev mode, no token exchange)
cd ../ai-agent
BYPASS_AUTH_TOKEN_EXCHANGE=true \
USER_MCP_URL=http://localhost:8090/mcp \
uv run uvicorn agent_api:app --host 0.0.0.0 --port 8000
```

When the agent starts it logs an `mcp_discovery_completed_at_startup` event with the tool names discovered on `user-mcp`. If the URL is wrong, the server is unreachable, or `USER_MCP_ALLOW_UNAUTH_DISCOVERY` is not enabled on `user-mcp`, expect `mcp_discovery_failed_at_startup` instead — the agent still starts but `/v1/agent/query` will run with no MCP tools available until a successful restart.

## Configuration

The service reads environment variables from the process environment and also loads a local `.env` file if present.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LANGCHAIN_MODEL` | `openai:gpt-5-mini` | Provider-qualified chat model string passed to LangChain, for example `openai:gpt-5-mini` |
| `OPENAI_API_KEY` | none | OpenAI API key when using an OpenAI-backed `LANGCHAIN_MODEL` |
| `LITELLM_BASE_URL` | none | When set, LLM calls go through LiteLLM's OpenAI-compatible `/v1` instead of directly to a provider. LiteLLM admits the agent by its mesh (SPIFFE) identity, so no LiteLLM API key is configured or sent; the OpenAI client is given a placeholder key only because the SDK requires one. |
| `ACTOR_TOKEN_PATH` | `/vault/secrets/actor-token` | Filesystem path to the actor token |
| `TOKEN_EXCHANGE_URL` | `http://localhost:8080/v1/identity/obo-token` | OBO token exchange endpoint |
| `TOKEN_EXCHANGE_TIMEOUT_SECONDS` | `10` | Token exchange timeout |
| `OBO_ROLE_NAME` | `agent-runtime` | Cache key input for OBO token reuse (combined with subject token + scope set) |
| `BYPASS_AUTH_TOKEN_EXCHANGE` | `false` | When `true`, `/v1/agent/query` does not require `Authorization` and skips OBO token exchange; `/v1/agent/tokens` will not return cached tokens |
| `USER_MCP_URL` | `http://localhost:8090/mcp` | Full URL of the upstream `user-mcp` streamable-HTTP endpoint, including the mount path. Must match `USER_MCP_HOST` / `USER_MCP_PORT` / `USER_MCP_PATH` on the `user-mcp` service. |
| `MCP_TOOL_CALL_TIMEOUT_SECONDS` | `120` | HTTP timeout for every MCP tool call to `user-mcp` (discovery and per-call). Must exceed `user-mcp`'s CIBA poll wait (`USER_MCP_CIBA_POLL_TIMEOUT_SECONDS`, default 110s) — the MCP client library's own default (30s) is too short for `create_user` / `delete_user_by_email`. |
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8000` | Bind port |
| `LOG_LEVEL` | `INFO` | Logging level |

Structured logs are emitted as JSON and now include standard operational metadata for aggregation systems such as Loki, including the timestamp, log level, logger name, hostname, resolved host IP (when available), module, function or method name, line number, and per-request fields such as request ID, HTTP method, path, and client IP when a request context exists.

`BYPASS_AUTH_TOKEN_EXCHANGE` is intended for local or test environments where the Agent API should run without security integration. When set to `true`, `POST /v1/agent/query` accepts requests without an `Authorization: Bearer ...` header, skips bearer-token validation, and does not call the token-exchange service. In the same mode, `GET /v1/agent/tokens` returns `404` because no OBO token is exchanged or cached. The default is `false`, which preserves the normal secure flow.

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

## Start the application directly

1. Create an actor token file:

```bash
mkdir -p .local
printf 'actor-token\n' > .local/actor-token
```

2. Export the required environment variables:

```bash
export OPENAI_API_KEY="your-openai-api-key"
export ACTOR_TOKEN_PATH="$(pwd)/.local/actor-token"
export TOKEN_EXCHANGE_URL="http://localhost:8080/v1/identity/obo-token"
export BYPASS_AUTH_TOKEN_EXCHANGE="false"
export USER_MCP_URL="http://localhost:8090/mcp"
export HOST="0.0.0.0"
export PORT="8000"
```

3. Start the service:

```bash
uv run uvicorn agent_api:app --host 0.0.0.0 --port 8000
```

## Build the container

The Dockerfile works with both regular `docker build` and `docker buildx` multi-architecture builds. Docker selects the requested platform automatically, so the file does not need explicit `--platform=$TARGETPLATFORM` directives.

From the `ai-agent` directory, a normal local build still works:

```bash
docker build -t agentguard-ai-agent .
```

To build a specific architecture locally with Buildx and load it into your local Docker image store:

```bash
docker buildx build \
  --platform linux/amd64 \
  -t agentguard-ai-agent:amd64 \
  --load \
  .
```

To build and publish a multi-architecture image manifest for both `linux/amd64` and `linux/arm64`:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t your-registry/agentguard-ai-agent:latest \
  --push \
  .
```

Use `--push` for true multi-architecture output. Docker cannot `--load` multiple architectures into the local image store in one command.

## Start the application in a container

1. Create an actor token file on the host:

```bash
mkdir -p .local
printf 'actor-token\n' > .local/actor-token
```

2. Run the container:

```bash
docker run --rm \
  -p 8000:8000 \
  -e OPENAI_API_KEY="your-openai-api-key" \
  -e TOKEN_EXCHANGE_URL="http://host.docker.internal:8080/v1/identity/obo-token" \
  -e USER_MCP_URL="http://host.docker.internal:8090/mcp" \
  -e ACTOR_TOKEN_PATH="/run/secrets/actor-token" \
  -e BYPASS_AUTH_TOKEN_EXCHANGE="false" \
  -v "$(pwd)/.local/actor-token:/run/secrets/actor-token:ro" \
  agentguard-ai-agent
```

If `host.docker.internal` is not available on your platform, point `TOKEN_EXCHANGE_URL` at a reachable host or service name instead.

If you built an architecture-specific image with Buildx, you can also select the runtime platform explicitly:

```bash
docker run --rm --platform linux/amd64 -p 8000:8000 agentguard-ai-agent:amd64
```

## Deploy on Kubernetes

The container is already configured to listen on port `8000`. For Kubernetes, you need:

- an image pushed to a registry that your cluster can pull from
- a Secret generated from an env file that contains the runtime configuration
- a Deployment that mounts that Secret as a `.env` file inside the container

### 1. Create the secret data

Create a namespace if needed:

```bash
kubectl create namespace ai-agent
```

Create an env file such as `.env.k8s`:

```bash
cat > .env.k8s <<'EOF'
OPENAI_API_KEY=your-openai-api-key
LANGCHAIN_MODEL=openai:gpt-5-mini
TOKEN_EXCHANGE_URL=http://identity-service.ai-agent.svc.cluster.local:8080/v1/identity/obo-token
TOKEN_EXCHANGE_TIMEOUT_SECONDS=10
OBO_ROLE_NAME=agent-runtime
BYPASS_AUTH_TOKEN_EXCHANGE=false
USER_MCP_URL=http://user-mcp.ai-agent.svc.cluster.local:8090/mcp
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
EOF
```

Create a Secret from that env file:

```bash
kubectl -n ai-agent create secret generic ai-agent-env \
  --from-file=.env=.env.k8s
```

### 2. Apply the Kubernetes manifests

Update `image:` and `TOKEN_EXCHANGE_URL` before applying:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ai-agent
  namespace: ai-agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ai-agent
  template:
    metadata:
      labels:
        app: ai-agent
    spec:
      containers:
        - name: ai-agent
          image: your-registry/ai-agent:latest
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8000
          env:
            - name: TOKEN_EXCHANGE_URL
              value: http://identity-service.ai-agent.svc.cluster.local:8080/v1/identity/obo-token
          volumeMounts:
            - name: app-env
              mountPath: /app/.env
              subPath: .env
              readOnly: true
      volumes:
        - name: app-env
          secret:
            secretName: ai-agent-env
            items:
              - key: .env
                path: .env
---
apiVersion: v1
kind: Service
metadata:
  name: ai-agent
  namespace: ai-agent
spec:
  selector:
    app: ai-agent
  ports:
    - name: http
      port: 8000
      targetPort: 8000
```

Save the manifest to a file such as `ai-agent-k8s.yaml`, then apply it:

```bash
kubectl apply -f ai-agent-k8s.yaml
```

### 3. Verify the deployment

Check that the pod is running:

```bash
kubectl -n ai-agent get pods
kubectl -n ai-agent get svc
```

If you want to test locally through the cluster service:

```bash
kubectl -n ai-agent port-forward svc/ai-agent 8000:8000
```

Notes:

- The application loads `/app/.env` automatically, so mounting the Secret as that file is enough.
- Kubernetes `env` entries override values loaded from `.env`, so `TOKEN_EXCHANGE_URL` can be set per environment without rebuilding the Secret.
- `kubectl create secret generic --from-file=.env=.env.k8s` stores the file as a single Secret entry named `.env`, which maps cleanly to `/app/.env`.
- The `users_repository.json` file is already baked into the image, so it does not need a separate volume mount.
- Set `TOKEN_EXCHANGE_URL` in the Deployment to the in-cluster DNS name or external URL of your real token exchange service.

## Manual test steps

You need three things for a successful end-to-end test:

- a valid-looking bearer token with a future `exp`
- an actor token file
- a token exchange endpoint that returns an OBO token

### Happy-path request

Send a request that should stream a plain-text answer:

```bash
curl -N \
  -X POST http://localhost:8000/v1/agent/query \
  -H "Authorization: Bearer ${TEST_BEARER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Find users with first name henry"
      }
    ]
  }'
```

### Retrieve cached agent tokens

After a successful `POST /v1/agent/query` call for the same bearer token, fetch the cached OBO token together with the actor token:

```bash
curl http://localhost:8000/v1/agent/tokens \
  -H "Authorization: Bearer ${TEST_BEARER_TOKEN}"
```

Expected result: `200` with a JSON body containing both `obo_token` and `actor_token`.

### Manual negative tests

Missing bearer token:

```bash
curl -X POST http://localhost:8000/v1/agent/query \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hello"}]}'
```

Expected result: `401` with `Authorization bearer token is required.`

Cache miss on the token lookup endpoint:

```bash
curl http://localhost:8000/v1/agent/tokens \
  -H "Authorization: Bearer ${TEST_BEARER_TOKEN}"
```

Expected result: `404` with `No cached OBO token found for the provided bearer token.`

Expired bearer token:

```bash
curl -X POST http://localhost:8000/v1/agent/query \
  -H "Authorization: Bearer ${EXPIRED_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hello"}]}'
```

Expected result: `401` with `Bearer token has expired.`
