# consul-mcp-authz

Operator-facing layer for managing the MCP authorization catalog in Vault KV (`opa-policies/mcp-authz/catalog`).

No laboratório o **enforce** do catálogo é LiteLLM → opa-server `mcp.pep`, não ext_authz no `user-mcp`. Gravar uma regra aqui ainda vale: o vault-agent + OPA `--watch` recarregam `data.rules` e o próximo `tools/call` já vê a lista nova.

Arquitetura: [`documentation/arquitetura-detalhada.md`](../documentation/arquitetura-detalhada.md). Companion spec: [`MCP-AUTHZ-SPEC.md`](./MCP-AUTHZ-SPEC.md).

This project houses **two workloads**:

| Subdir | Workload | Phase | Status |
|--------|----------|-------|--------|
| [`api/`](./api) | `consul-mcp-authz` — FastAPI REST service over the Vault KV v2 catalog | P1 | built (no auth yet) |
| [`ui/`](./ui)   | Next.js operator console — runs in the **same container** as the API (one image, one Pod) | P2 | pilot built: Rules list, View/Edit, New Rule, History + rollback, Agents / MCP Servers browse, MCP Server detail |

## Pending implementation

| Phase | Item | Notes |
|-------|------|-------|
| P1.5 | OIDC operator auth on the API | Today the API has no auth; reach it only via `kubectl port-forward`. P1.5 verifies a Vault-issued JWT against Vault JWKS and derives roles from OIDC group claims (`viewer` / `editor` / `admin`). |
| P1.5 | `POST /v1/rules:dryRun` | Sugar over `catalog/validator.py` — run `opa eval` on a candidate without writing to Vault. Useful as a UI-side sanity check before save. |
| P2   | OIDC login flow (UI) | iron-session cookie pattern from `web-app/`, depends on P1.5 API auth. |
| P2   | UI pre-save `dryRun` call | The View/Edit and New Rule save paths will additionally call `POST /v1/rules:dryRun` before persisting, so the form can surface a policy error without touching Vault. Server-side `opa eval` runs regardless on PUT/PATCH (defense in depth). Blocked on the P1.5 endpoint. |
| P2   | Admin-only rollback gating (UI) | The `Roll back` button on `/history` is currently visible to anyone reaching the UI. Once P1.5 OIDC is in place the button will be hidden for non-`admin` roles and the underlying `POST /v1/rules:rollback` enforced server-side. Blocked on P1.5 OIDC + group claims. |

---

## P1 pilot — `api/`

### Goal

Prove the write path end-to-end: an HTTP `PUT /v1/rules` should
propagate through Vault → vault-agent → OPA `--watch` and flip an
ext_authz decision on `user-mcp`, with **no pod restart** and no
manual `vault kv put`.

### In-scope

| Endpoint | Behaviour |
|----------|-----------|
| `GET  /v1/rules` | Read catalog + KV v2 version metadata. |
| `PUT  /v1/rules` | Replace catalog. `opa eval` validates against the live policy, then KV v2 CAS write (412 on stale `expected_version`). |
| `PATCH /v1/rules/{src-ns}/{src-svc}/{dst-ns}/{dst-svc}` | Replace the `allow` list for one (source, destination) pair. Read-modify-write under the same CAS contract as PUT. |
| `GET  /v1/rules/history` | List all KV v2 versions of the catalog (current first), with `created_time`, `deletion_time`, and `destroyed`. |
| `POST /v1/rules:rollback` | Read the catalog at version `N` and write it as a new version. Re-runs Pydantic + `opa eval` against the current policy so rollbacks can't reintroduce a schema that no longer validates. |
| `GET  /v1/agents` | List Consul services with `service-meta-agent-tool-authz-role=agent`. |
| `GET  /v1/mcp-servers` | List Consul services with `service-meta-agent-tool-authz-role=mcp-server`. |
| `GET  /v1/mcp-servers/{ns}/{name}/tools` | Call MCP `tools/list` over the mesh against `{ns}/{name}` and return the live tool surface as `[{name, description}]`. `description` is `null` when the MCP server omits it. Cached for `discovery_cache_ttl_seconds`. |
| `GET  /healthz` | Liveness/readiness. |

### Explicitly deferred (later phases)

- OIDC operator auth. The pilot has no auth — reach the API only through
  `kubectl port-forward` so it is not exposed to the cluster network.
- `POST /v1/rules:dryRun` (sugar over the validator).

---

## Architecture

```
operator (kubectl port-forward + curl)
            │
            ▼
consul-mcp-authz Pod
  ├─ consul-mcp-authz  ← reads /vault/secrets/token; `opa eval` validates
  │                          reads /vault/secrets/consul-token for discovery
  └─ vault-agent           ← JWT auth via SA token; sinks two files:
                              /vault/secrets/token         (this workload's Vault token)
                              /vault/secrets/consul-token  (rendered from KV)
            │ writes                  │ reads                     │ calls (via mesh)
            ▼                         ▼                           ▼
        Vault KV v2          Consul HTTP API           each MCP server's /mcp
        opa-policies/         /v1/catalog/...           POST initialize →
          mcp-authz/catalog    filtered by ServiceMeta:    notifications/initialized →
            │                    agent-tool-authz-role          tools/list
            │                       = agent | mcp-server   (uses session id; cached
            │                                              per (namespace, name))
            │ (existing — built in P0)
            ▼
opa-mcp-authz Pod (vault-agent + opa --watch) → live reload → new ext_authz decision
```

---

## Layout

The API and UI ship as **one Docker image** (`panchalravi/consul-mcp-authz`)
and run in one container; supervisord runs `uvicorn` on `:8080` and the
Next.js standalone server on `:8502` inside the same Pod. The Next
server talks to the API over `127.0.0.1:8080`. See the build/run notes
in [Step 2](#step-2--build-the-docker-image) and
[Step 3](#step-3--apply-the-k8s-manifests).

```
consul-mcp-authz/
├── README.md                          # ← you are here
├── Dockerfile                         # multi-stage build: ui + api → single image
├── supervisord.conf                   # supervises uvicorn + node inside the container
├── .dockerignore
├── api/
│   ├── .env                           # local-dev settings (gitignored)
│   ├── pyproject.toml, uv.lock        # python 3.12 + uv
│   ├── api/{main.py,routes.py}        # FastAPI app + endpoints
│   ├── catalog/vault_client.py        # hvac KV v2 wrapper with CAS
│   ├── catalog/validator.py           # runs `opa eval` against the candidate
│   ├── config/settings.py             # pydantic-settings (MCP_AUTHZ_API_*)
│   ├── discovery/consul_client.py     # Consul catalog client (role filter)
│   ├── discovery/mcp_client.py        # MCP streamable-HTTP client for tools/list
│   ├── models/schemas.py              # pydantic models for the catalog
│   ├── app_logging/logger.py          # structlog JSON output
│   ├── exceptions/errors.py           # typed errors (validation, conflict, ...)
│   ├── tests/test_validator.py        # validator unit tests (skipped without opa)
│   └── policy/                        # snapshot of opa-mcp-auth/policy (gitignored)
└── ui/
    ├── package.json                   # Next.js 15 + React 19, TS strict
    ├── tailwind.config.ts             # Consul-derived design tokens
    ├── next.config.mjs                # output: 'standalone'
    ├── README.md                      # P2 pilot scope + local-dev notes
    └── src/
        ├── app/                       # App Router pages + route handlers
        │   ├── layout.tsx             # root layout, Inter font
        │   ├── page.tsx               # redirects to /rules
        │   ├── rules/page.tsx         # Rules list (Consul-style table)
        │   ├── rules/new/
        │   │   ├── page.tsx           # New Rule form (server component)
        │   │   └── new-rule-form.tsx  # client: source/dest pickers, lazy tool discovery, PATCH+CAS save
        │   ├── rules/[srcNs]/[srcSvc]/[dstNs]/[dstSvc]/
        │   │   ├── page.tsx           # View/Edit a rule pair (server component)
        │   │   └── edit-form.tsx      # client form: tool checklist + PATCH save
        │   ├── history/
        │   │   ├── page.tsx           # History list (server component)
        │   │   └── history-table.tsx  # client: two-phase rollback confirm + CAS POST
        │   ├── agents/page.tsx        # Agents browse (server-rendered)
        │   ├── mcp-servers/
        │   │   ├── page.tsx           # MCP Servers browse (server-rendered)
        │   │   └── [ns]/[name]/page.tsx # MCP Server detail: tools + descriptions
        │   └── api/v1/                # browser-facing proxy routes
        │       ├── agents/route.ts                                 # GET proxy
        │       ├── mcp-servers/route.ts                            # GET proxy
        │       ├── mcp-servers/[ns]/[name]/tools/route.ts          # GET proxy (lazy discovery)
        │       ├── rules/route.ts                                  # GET proxy
        │       ├── rules/[srcNs]/[srcSvc]/[dstNs]/[dstSvc]/route.ts # PATCH proxy
        │       ├── rules/history/route.ts                          # GET proxy
        │       └── rules/rollback/route.ts                         # POST proxy (drops the upstream `:rollback` colon)
        ├── components/                # sidebar, tables, badges, icons
        └── lib/                       # api.ts (server-side fetch), types.ts
```

The Docker build expects a sibling `policy/` directory alongside the
Dockerfile — the build snapshot is a **copy** of
[`opa-mcp-auth/policy/`](../opa-mcp-auth/policy) so the in-image
validator runs against the same `.rego` that's deployed to the OPA pod.
`policy/` is gitignored; the build step (below) re-snapshots it every
time.

---

## Discovery

The operator console needs two things from the data plane:

1. **"Which workloads can be a source / destination?"** — to populate the
   matrix view (rows = agents, columns = MCP servers).
2. **"What tools does this MCP server expose?"** — to fill cell contents.

These come from two different sources, each chosen as the most
drift-resistant option:

| Question | Source | Why |
|----------|--------|-----|
| Which workloads participate? | Consul ServiceMeta annotation `agent-tool-authz-role` | Workloads already register with Consul; a single annotation identifies their role without standing up a separate registry. |
| What tools does an MCP server expose? | Live `tools/list` RPC over the mesh | MCP servers already advertise their tool surface as part of the protocol. Reading it live means zero drift between deployment manifest and running server. |

### 1. Role annotation (participation)

Add to each participating pod's `spec.template.metadata.annotations`:

```yaml
# AI agent (source of rule entries):
consul.hashicorp.com/service-meta-agent-tool-authz-role: "agent"

# MCP server (destination):
consul.hashicorp.com/service-meta-agent-tool-authz-role: "mcp-server"
```

Consul-K8s renames each annotation `service-meta-<key>` to a
`ServiceMeta[<key>]` entry on the registered service. `GET /v1/agents`
and `GET /v1/mcp-servers` filter the catalog on this key.

Sidecar proxies inherit the parent pod's `ServiceMeta`, so both
`ai-agent` and `ai-agent-sidecar-proxy` would otherwise match. The
discovery client drops any instance with a non-empty `ServiceKind`
(`connect-proxy`, `mesh-gateway`, …) so only application services
surface in the two lists and in the New Rule pickers.

Applied here on:
- [`deploy-k8s/ai-agent.yaml`](../deploy-k8s/ai-agent.yaml) → `agent`
- [`deploy-k8s/user-mcp.yaml`](../deploy-k8s/user-mcp.yaml) → `mcp-server`

### 2. Live `tools/list` (tool surface)

`GET /v1/mcp-servers/{ns}/{name}/tools` performs the standard MCP handshake
against the target's `/mcp` endpoint over the Consul mesh:

```
POST /mcp  →  initialize             (gets Mcp-Session-Id)
POST /mcp  →  notifications/initialized
POST /mcp  →  tools/list             (returns the tool array)
```

Results are cached per `(namespace, name)` for `discovery_cache_ttl_seconds`
(default 300 s) so a UI matrix refresh doesn't fan out RPCs every poll.

Auth: the API sends no `Authorization` header. user-mcp accepts unauth
`tools/list` when `USER_MCP_ALLOW_UNAUTH_DISCOVERY=true` because the mesh
edge already authenticated the peer via mTLS and OPA already gated the
call. `tools/call` keeps requiring a real JWT.

### Preconditions for live discovery

For the API's `tools/list` to succeed against an MCP server, **one
prerequisite** must be in place:

- **Mesh authorization** — a Consul `ServiceIntention` allowing
  `consul-mcp-authz` to call the target. Already wired for `user-mcp`
  in [`deploy-k8s/service-intentions.yaml`](../deploy-k8s/service-intentions.yaml);
  replicate for any new MCP server.

No catalog entry is needed. The OPA policy treats MCP handshake and
surface-enumeration methods (`initialize`, `notifications/initialized`,
`ping`, `tools/list`, `resources/list`, `prompts/list`) as
mesh-authorized — Envoy's mTLS check + the `ServiceIntention` already
gate reachability, so the catalog only governs `tools/call` (and
`resources/read` / `prompts/get`). This means **no per-pair operational
burden for discovery**: adding a new agent or MCP server requires only
the role annotation and the `ServiceIntention`.

If the `ServiceIntention` is missing the route returns **502** with the
upstream `x-authz-reason` from OPA.

### Consul access for the API

`GET /v1/agents` and `GET /v1/mcp-servers` need a Consul ACL token with
`service:read` and `namespace:read` (read-only). It's seeded once
manually into Vault KV (see Step 7.1) and rendered onto the API pod
via vault-agent. Missing/empty token → those two endpoints return
**503**; the rest of the API keeps working.

---

## Step 1 — Apply the Vault role + policy

The Vault resources are managed in
[`infra/modules/consul-client-k8s/vault.tf`](../infra/modules/consul-client-k8s/vault.tf):

- `resource "vault_policy" "consul_mcp_authz"` — capabilities
  `create, read, update` on `opa-policies/data/mcp-authz/catalog` and
  `read` on its `metadata` path.
- `resource "vault_jwt_auth_backend_role" "consul_mcp_authz"` —
  binds the `default/consul-mcp-authz` ServiceAccount to that
  policy via the existing `k8s_jwt` JWT auth backend.

Apply:

```bash
cd infra/modules/consul-client-k8s
terraform apply -target=vault_policy.consul_mcp_authz \
                -target=vault_jwt_auth_backend_role.consul_mcp_authz
```

A manual `vault policy write` + `vault write auth/k8s_jwt/role/...`
equivalent (for clusters not using this Terraform module) is documented
in [`../opa-mcp-auth/vault/policies.hcl`](../opa-mcp-auth/vault/policies.hcl)
under "consul-mcp-authz (P1 pilot)".

---

## Step 2 — Build the Docker image

The single image at `consul-mcp-authz/Dockerfile` builds **both** the
FastAPI backend and the Next.js operator console and runs them under
supervisord in one container. It bakes in:

- Python deps from `api/uv.lock` (frozen), `supervisor` + `setuptools`.
- The Node 20 binary copied out of `node:20-bookworm-slim` (Debian
  Bookworm — ABI-compatible with `python:3.12-slim`). No npm at runtime;
  the UI runs from Next.js's standalone build output.
- The `opa` CLI from `openpolicyagent/opa:latest` (validator dependency).
- A snapshot of `opa-mcp-auth/policy/` at `/app/policy` (validator
  dependency).

The Dockerfile cannot reach **outside** its own build context, so the
policy snapshot is copied in before the build. Run from
`consul-mcp-authz/`:

```bash
cd consul-mcp-authz

# (one-time, or whenever api/pyproject.toml changes) lock deps
( cd api && uv lock )

# snapshot the policy from the source of truth (gitignored)
rm -rf api/policy
cp -r ../opa-mcp-auth/policy api/policy

# local single-arch build (loads into the local docker daemon)
docker build -t panchalravi/consul-mcp-authz:latest .

# multi-arch build + push to the registry
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t panchalravi/consul-mcp-authz:latest \
  --push .
```

The `api/policy/` directory is gitignored — never commit a divergent
copy. The source of truth lives at `opa-mcp-auth/policy/`; this is just
the snapshot the image needs.

### Smoke-test the image locally

After the build, you can confirm both processes start cleanly without
deploying:

```bash
docker run --rm -p 18080:8080 -p 18502:8502 \
  --name mcpz-smoke panchalravi/consul-mcp-authz:latest &
sleep 5
curl -sS -o /dev/null -w 'api healthz: %{http_code}\n' http://localhost:18080/healthz
curl -sS -o /dev/null -w 'ui rules : %{http_code}\n'  http://localhost:18502/rules
docker rm -f mcpz-smoke
```

Both calls should return `200`. The Rules page will show the amber
"could not load rules" banner because Vault is unreachable from your
laptop — that's expected; in-cluster it talks to Vault via the
vault-agent sidecar.

---

## Step 3 — Apply the K8s manifests

The Deployment runs **one container** named `consul-mcp-authz` with two
exposed ports (`8080` for the API, `8502` for the UI). The Service,
Vault JWT role, Consul intentions, and the catalog discovery entry all
share the `consul-mcp-authz` name.

```bash
kubectl apply -f deploy-k8s/consul-mcp-authz.yaml
kubectl rollout status deploy/consul-mcp-authz

# Should log a `rendered "…/token"` line from vault-agent and a
# `consul_mcp_authz_api_starting` event from the workload container.
kubectl logs deploy/consul-mcp-authz -c vault-agent | grep -i rendered
kubectl logs deploy/consul-mcp-authz -c consul-mcp-authz | head -8
```

Expect to see both `INFO success: api entered RUNNING state` and
`INFO success: ui entered RUNNING state` from supervisord in those logs.

---

## Step 4 — Manual test: GET, PUT, propagation, CAS, validation

```bash
# Port-forward the API.
kubectl port-forward deploy/consul-mcp-authz 8080:8080 &

# 4.1 — Read current catalog (should reflect the P0 seed).
curl -sS http://localhost:8080/v1/rules | jq

# 4.2 — Write a catalog that allows create_user
#       (equivalent to Step 7.1 of opa-mcp-auth/README.md, via API).
curl -sS -X PUT http://localhost:8080/v1/rules \
  -H 'Content-Type: application/json' \
  -d '{
    "catalog": {
      "rules": {
        "default/ai-agent": {
          "default/user-mcp": {
            "allow": ["list_all_users","search_users_by_first_name","update_user_by_email","create_user"]
          }
        }
      }
    }
  }' | jq
# Expected: {"version": <n+1>, "created_time": "..."}

# 4.3 — Confirm propagation from the ai-agent pod.
sleep 3
kubectl exec deploy/ai-agent -c ai-agent -- \
  curl -sS -i -X POST http://user-mcp.virtual.consul/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"create_user","arguments":{}}}' \
  | grep -iE '^HTTP/|^x-authz-reason|^mcp-session-id' || true
# Expected: NO x-authz-reason header — OPA now allows create_user, the
#           request reaches user-mcp (which returns 400 for the missing
#           MCP session handshake; that 400 is downstream of authz).

# 4.4 — CAS conflict. Read the current version, lie about it.
CUR=$(curl -sS http://localhost:8080/v1/rules | jq -r .version)
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X PUT http://localhost:8080/v1/rules \
  -H 'Content-Type: application/json' \
  -d "{\"catalog\":{\"rules\":{}},\"expected_version\": $((CUR - 1))}"
# Expected: 412

# 4.5 — Validation rejection. Send a structurally invalid catalog.
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X PUT http://localhost:8080/v1/rules \
  -H 'Content-Type: application/json' \
  -d '{"catalog":{"rules":"not an object"}}'
# Expected: 422 — FastAPI rejects on the Pydantic body schema BEFORE the
#           route handler runs (rules must be a dict, not a string).
#
# Note on the two validation layers:
#   - 422 fires when the request body fails the Pydantic models in
#     models/schemas.py (wrong field types, malformed source/dest keys,
#     tool names that don't match the pattern, etc.). Most bad inputs
#     land here.
#   - 400 fires only when a body that passes Pydantic still fails the
#     embedded `opa eval` in catalog/validator.py. This is defense in
#     depth — it would catch a Pydantic↔policy drift, or a candidate
#     that compiles syntactically but breaks the policy contract.
#     Hard to trip in practice because Pydantic is strict; that's by design.
```

---

## Step 5 — PATCH one (source, destination) pair

The PATCH endpoint is a thin sugar over PUT: read the current catalog,
swap one pair's `allow` list, write back under CAS. Useful for the
matrix toggle UI in P2.

```bash
# 5.1 — Toggle create_user ON by patching the single pair, no full catalog.
curl -sS -X PATCH \
  "http://localhost:8080/v1/rules/default/ai-agent/default/user-mcp" \
  -H 'Content-Type: application/json' \
  -d '{
    "allow": ["list_all_users","search_users_by_first_name","update_user_by_email","create_user"]
  }' | jq
# Expected: {"version": <n+1>, "created_time": "..."}

# 5.2 — Propagation check — same as 4.3.
sleep 3
kubectl exec deploy/ai-agent -c ai-agent -- \
  curl -sS -i -X POST http://user-mcp.virtual.consul/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"create_user","arguments":{}}}' \
  | grep -iE '^HTTP/|^x-authz-reason|^mcp-session-id' || true
# Expected: NO x-authz-reason header.

# 5.3 — CAS on PATCH: pass a stale expected_version.
CUR=$(curl -sS http://localhost:8080/v1/rules | jq -r .version)
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X PATCH "http://localhost:8080/v1/rules/default/ai-agent/default/user-mcp" \
  -H 'Content-Type: application/json' \
  -d "{\"allow\": [], \"expected_version\": $((CUR - 1))}"
# Expected: 412
```

---

## Step 6 — History + rollback

```bash
# 6.1 — Inspect every version of the catalog, current first.
curl -sS http://localhost:8080/v1/rules/history | jq
# Expected:
# {
#   "current_version": <n>,
#   "versions": [
#     {"version": n, "created_time": "...", "deletion_time": null, "destroyed": false},
#     {"version": n-1, ...},
#     ...
#   ]
# }

# 6.2 — Roll back two versions (e.g., from <n> to <n-2>) WITHOUT a pod restart.
TARGET=$(( $(curl -sS http://localhost:8080/v1/rules | jq -r .version) - 2 ))
curl -sS -X POST "http://localhost:8080/v1/rules:rollback" \
  -H 'Content-Type: application/json' \
  -d "{\"version\": $TARGET}" | jq
# Expected: {"version": <n+1>, ...}  -- KV v2 always writes a NEW version;
#           the old version stays readable.

# 6.3 — Propagation: confirm OPA now decides against the rolled-back data.
#       (E.g., if version $TARGET denied create_user, you should get 403 again.)
sleep 3
kubectl exec deploy/ai-agent -c ai-agent -- \
  curl -sS -i -X POST http://user-mcp.virtual.consul/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"create_user","arguments":{}}}' \
  | grep -iE '^HTTP/|^x-authz-reason' || true
```

---

## Step 7 — Discovery

### 7.1 — Seed the Consul ACL token into Vault (one-time)

The ACL policy, token, and Vault KV seed are managed by Terraform at
[`infra/modules/consul-client-k8s/consul-mcp-authz.tf`](../infra/modules/consul-client-k8s/consul-mcp-authz.tf):

- `consul_acl_policy.consul_mcp_authz` — read services across
  every Consul namespace.
- `consul_acl_token.consul_mcp_authz` — bound to that policy.
- `data.consul_acl_token_secret_id.consul_mcp_authz` — reads the
  token's secret value (the provider hides it on the resource itself).
- `vault_kv_secret_v2.consul_mcp_authz_token` — writes
  `{"token": "<secret>"}` to `opa-policies/consul/mcp-authz-token`,
  which is the path the API's vault-agent renders to
  `/vault/secrets/consul-token`.

Apply:

```bash
cd infra/modules/consul-client-k8s
terraform apply \
  -target=consul_acl_policy.consul_mcp_authz \
  -target=consul_acl_token.consul_mcp_authz \
  -target=vault_kv_secret_v2.consul_mcp_authz_token
```

Bounce the API pod so vault-agent picks up the new KV path:

```bash
kubectl rollout restart deploy/consul-mcp-authz
kubectl rollout status deploy/consul-mcp-authz
```

<details>
<summary>Manual equivalent (for clusters not using this Terraform module)</summary>

```hcl
# consul-mcp-authz-read.hcl
service_prefix "" { policy = "read" }
namespace_prefix "" {
  service_prefix "" { policy = "read" }
}
```

```bash
consul acl policy create \
  -name consul-mcp-authz-read -rules @consul-mcp-authz-read.hcl
CONSUL_TOKEN=$(consul acl token create \
  -description "consul-mcp-authz discovery" \
  -policy-name consul-mcp-authz-read -format=json | jq -r .SecretID)
vault kv put opa-policies/consul/mcp-authz-token token="$CONSUL_TOKEN"
```
</details>

### 7.2 — Add the role annotation to workloads (one-time, already applied here)

The two workloads in this repo are already annotated; for new ones add:

```yaml
# Source (agent):
consul.hashicorp.com/service-meta-agent-tool-authz-role: "agent"

# Destination (MCP server):
consul.hashicorp.com/service-meta-agent-tool-authz-role: "mcp-server"
```

Then `kubectl rollout restart deploy/<workload>` so Consul-K8s re-registers
with the updated ServiceMeta. **No tools annotation is needed** — the API
reads the live tool surface via MCP `tools/list`.

### 7.3 — Allow the API to call MCP servers (one-time per new MCP server)

A single `ServiceIntention` allowing `consul-mcp-authz → <mcp-server>`
is the only prerequisite. For `user-mcp` it is already in place — see
[`deploy-k8s/service-intentions.yaml`](../deploy-k8s/service-intentions.yaml).
Replicate the pattern for any new MCP server.

No catalog entry is required: the OPA policy classifies `tools/list`
(and the rest of the MCP handshake / enumeration methods) as
mesh-authorized, so the mTLS-authenticated `ServiceIntention` is the
gate. The catalog only governs `tools/call`, `resources/read`, and
`prompts/get`.

### 7.4 — Query the API

```bash
# 7.4.1 — Agents.
curl -sS http://localhost:8080/v1/agents | jq
# Expected: {"agents": [{"namespace":"default","name":"ai-agent"}]}

# 7.4.2 — MCP servers.
curl -sS http://localhost:8080/v1/mcp-servers | jq
# Expected: {"mcp_servers": [{"namespace":"default","name":"user-mcp"}]}

# 7.4.3 — Live tool surface for one MCP server.
curl -sS http://localhost:8080/v1/mcp-servers/default/user-mcp/tools | jq
# Expected:
# {
#   "namespace": "default",
#   "name": "user-mcp",
#   "tools": [
#     {"name": "list_all_users",            "description": "Return every user record."},
#     {"name": "search_users_by_first_name","description": "Find users matching a given first name."},
#     {"name": "update_user_by_email",      "description": "Update fields on the user with this email."},
#     {"name": "create_user",               "description": "Create a new user record."}
#   ],
#   "source": "mcp-tools-list"
# }
# description is whatever the MCP server advertises in tools/list; null when omitted.
# Behind the scenes the API does:
#   POST http://user-mcp.virtual.consul/mcp  initialize → notifications/initialized → tools/list
# Result is cached for 5 min.

# 7.4.4 — Drift check: add a new @mcp.tool to user-mcp, redeploy.
#         The next call after the cache expires (or after a pod restart of
#         consul-mcp-authz) shows the new tool — no manifest edit needed.

# 7.4.5 — Failure modes.
#  - Consul token missing → /v1/agents and /v1/mcp-servers return 503.
#  - ServiceIntention missing → /v1/mcp-servers/.../tools returns 502 with
#    a detail naming the intention to add.
#  - MCP server down or unreachable → 502 with the transport error.
```

---

## Step 8 — Rollback to baseline

```bash
curl -sS -X PUT http://localhost:8080/v1/rules \
  -H 'Content-Type: application/json' \
  -d '{
    "catalog": {
      "rules": {
        "default/ai-agent": {
          "default/user-mcp": {
            "allow": ["list_all_users","search_users_by_first_name","update_user_by_email"]
          }
        }
      }
    }
  }' | jq
```

The API's own `tools/list` calls don't need a catalog entry — discovery
is mesh-authorized by the `consul-mcp-authz → user-mcp` ServiceIntention
alone.

---

## P2 pilot — `ui/`

Operator console matching Consul's UI conventions (dark sidebar, white
content surface, green Allow pill, blue primary CTA, Inter typography).
The pilot now ships **six screens end-to-end**:

1. **Rules list** at `/rules` — renders `GET /v1/rules` as a Consul-style
   table; each row is clickable into screen #3. Header has a `Create`
   button linking to screen #2.
2. **New Rule** at `/rules/new` — Source picker from `GET /v1/agents`,
   Destination picker from `GET /v1/mcp-servers`. When the destination
   changes, the client lazily calls `GET /v1/mcp-servers/{ns}/{name}/tools`
   to render the tool checklist (with manual-add fallback when discovery
   returns 404/502/503). If the chosen `(src, dst)` pair already exists
   in the catalog the form shows an amber warning with a link to the
   View/Edit page and disables Save — preventing silent overwrites.
   Save issues the same `PATCH /v1/rules/{src}/{dst}` + CAS as screen #3.
3. **View/Edit a rule pair** at
   `/rules/[srcNs]/[srcSvc]/[dstNs]/[dstSvc]` — read-only header
   (source/destination/Allow pill/catalog version) plus an editable tool
   checklist. The checklist is the union of `/v1/mcp-servers/{ns}/{name}/tools`
   and the pair's current `allow` list, with a free-text "Add tool name
   (advanced)" input for tools the operator knows about ahead of MCP-server
   deployment. "Save changes" issues `PATCH /v1/rules/{src}/{dst}` with the
   read catalog version as `expected_version` (CAS). Discovery 404/502/503
   and CAS 412 are surfaced inline.
4. **History + rollback** at `/history` — renders `GET /v1/rules/history`
   with one row per KV v2 version (current first), status pills for
   `Current` / `Available` / `Deleted` / `Destroyed`. Rollback uses a
   two-phase inline confirm (`Roll back` → `Roll back to vN? [Cancel]
   [Confirm]`) and posts to `/v1/rules:rollback` with
   `expected_version = current_version` for CAS.
5. **Agents** at `/agents` and **MCP Servers** at `/mcp-servers` —
   read-only listings of Consul services carrying the matching
   `agent-tool-authz-role` ServiceMeta (sidecar proxies filtered out).
   Used as a sanity check that the role annotations propagated. On
   `/mcp-servers`, the service name is a link into screen #6.
6. **MCP Server detail** at `/mcp-servers/[ns]/[name]` — Name +
   Description table sourced from `GET /v1/mcp-servers/{ns}/{name}/tools`.
   Read-only: shows everything the destination's live `tools/list`
   advertises, with the description text the MCP server itself emits.
   404 from the API (service isn't registered or lacks the `mcp-server`
   role) renders Next's `not-found`; 502/503 surface in the same amber
   banner pattern used elsewhere. Tools with no description show an
   italic placeholder.

Pending UI work — see [Pending implementation](#pending-implementation):
OIDC login flow, pre-save `dryRun` call, and admin-only rollback
gating. All three are blocked on P1.5.

The UI is **packaged in the same image** as the API and runs in the same
container; the Next.js server proxies to `http://127.0.0.1:8080` over
loopback. No separate Deployment, no separate ServiceIntention.

### Step 9 — Test the UI

(The image and the Deployment are already built and applied as part of
Steps 2 and 3. Step 9 is purely how to verify the UI in-cluster.)

#### 9.1 Test the Rules list end-to-end

```bash
# Port-forward the UI port on the existing service.
kubectl port-forward svc/consul-mcp-authz 8502:8502 &

# Open the Rules list in a browser.
open http://localhost:8502/rules     # macOS
# or: xdg-open http://localhost:8502/rules
```

Expected:

- Dark Consul-style sidebar with the "C" mark, `dc1` chip, and inert
  admin-partition / namespace selectors.
- Page header `Rules <N total>` and `Catalog version <n> · written <ts>`.
- One row per `(src-ns/src-svc → dst-ns/dst-svc)` pair from
  `GET /v1/rules`, with the green Allow pill, service/namespace chips,
  and a "<n> tools allowed" status pill.
- For the baseline catalog from Step 8 there should be exactly one row:
  `default/ai-agent → default/user-mcp`. The API's own discovery calls
  do not appear here — discovery is mesh-authorized and unrelated to
  catalog entries.

If the API process inside the same pod is unhealthy, the page shows an
amber banner with the exact upstream error — useful for diagnosing
Vault token or policy issues.

#### 9.2 Test and validate rules with the UI

This is the end-to-end equivalent of Step 5 (PATCH one pair) — but
through the UI instead of `curl`, and with a propagation check against
`ai-agent` so you can see a rule change land in OPA.

```bash
# Keep these port-forwards running (one shell each, or as background &).
kubectl port-forward svc/consul-mcp-authz 8502:8502 &
```

**9.2.1 — Click into a rule pair**

1. Open `http://localhost:8502/rules` in a browser.
2. Click any row — e.g., `default/ai-agent → default/user-mcp`.
3. The detail page at
   `http://localhost:8502/rules/default/ai-agent/default/user-mcp` opens.

Expected on the detail page:

- A back-link ("Rules") that returns to the list.
- Header `ai-agent → user-mcp` with `Catalog version <n>.`
- A read-only source/destination card with the green Allow pill between
  the two services.
- An "Allowed tools" section listing every tool the destination's live
  `tools/list` returns (from `/v1/mcp-servers/default/user-mcp/tools`),
  with the currently-allowed tools pre-checked. The selected-count chip
  on the right shows e.g. `3 of 5 selected`.

**9.2.2 — Edit and save**

1. Tick a previously-denied tool (e.g., `create_user`).
2. Click **Save changes**. The status line below the form should turn
   green and say `Saved as version <n+1>.`
3. The header's `Catalog version` updates to the new number without a
   full page reload (the page issues `router.refresh()` after a
   successful save).

Confirm the change actually propagated to OPA — same propagation check
as Step 5.2, just driven from the UI:

```bash
sleep 3
kubectl exec deploy/ai-agent -c ai-agent -- \
  curl -sS -i -X POST http://user-mcp.virtual.consul/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"create_user","arguments":{}}}' \
  | grep -iE '^HTTP/|^x-authz-reason' || true
# Expected: NO x-authz-reason header — OPA admits the tool, the request
#           reaches user-mcp (which returns 400 for the missing MCP
#           session handshake; that 400 is downstream of authz).
```

**9.2.3 — Trigger the CAS conflict path**

This exercises the same `expected_version` semantics that the API enforces
on PATCH (Step 5.3) — but surfaced as a UI message.

1. Reopen the detail page in **two** browser tabs side-by-side.
2. In tab A: untick a tool and click Save. Confirm the green "Saved as
   version <n+1>." status.
3. In tab B (still showing the **old** version in the header): tick a
   different tool and click Save.
4. The status line in tab B should turn red and read approximately
   "Catalog has been updated since you read it; re-fetch and retry.
   (Reload to pick up the latest version.)"
5. Hard-reload tab B. The catalog version in the header advances, and
   re-applying the change saves cleanly.

**9.2.4 — Discovery-down fallback (optional)**

This exercises the View/Edit page's graceful degradation when live
`tools/list` discovery fails — without breaking the API.

1. Stop the Consul ACL token render to force discovery to 503: delete
   the rendered file inside the API container.

   ```bash
   POD=$(kubectl get pod -l app=consul-mcp-authz -o jsonpath='{.items[0].metadata.name}')
   kubectl exec "$POD" -c consul-mcp-authz -- rm -f /vault/secrets/consul-token
   ```

2. Reload the detail page. An amber banner reading **"Live tool discovery
   unavailable — using existing allow list."** appears above the
   checklist. The existing `allow` list is still rendered as toggleable
   checkboxes, each labelled `(not in current tools/list — added
   manually)`. The "Add tool name (advanced)" input remains usable.
3. Restore the token by re-running `kubectl rollout restart
   deploy/consul-mcp-authz` (vault-agent re-renders `/vault/secrets/consul-token`
   on startup). After the rollout the banner disappears.

**9.2.5 — Drive a new tool into the allow list before MCP redeploy**

Use case: the destination MCP server is gaining a new `@mcp.tool` in
the next release, but the operator wants the catalog ready for it.

1. On the detail page, type the new tool name into the **"Add tool name
   (advanced)"** input and press Enter (or click **Add**).
2. The tool appears in the checklist already ticked, labelled
   `(not in current tools/list — added manually)`.
3. Click Save. Verify with `curl http://localhost:8080/v1/rules` (or the
   Rules list) that the new tool is now in the pair's `allow` array.
   `tools/call` for it will start succeeding the moment the MCP server
   adds the tool — no further catalog edit needed.

#### 9.3 Test the New Rule form

This exercises the create path through the UI — the API-side equivalent
is Step 5.1 (PATCH a pair that doesn't exist yet).

1. From `/rules`, click **Create**. The browser navigates to
   `/rules/new`.
2. The Source dropdown lists agents from `GET /v1/agents`; Destination
   lists MCP servers from `GET /v1/mcp-servers`. Neither list should
   contain `*-sidecar-proxy` entries.
3. Pick a Source. Pick a Destination — at that point the page calls
   `/api/v1/mcp-servers/{ns}/{name}/tools` and renders a tool checklist.
4. **Pair-already-exists guard**: pick a `(Source, Destination)` that
   already has an entry in the catalog (e.g.
   `default/ai-agent → default/user-mcp`). An amber banner appears:
   "A rule for this pair already exists" with a link to the View/Edit
   page. **Save is disabled.** Change the destination to a service with
   no existing entry and the banner clears.
5. Tick one or more tools, click **Save**. The page navigates to the
   newly-created pair's View/Edit page at
   `/rules/<srcNs>/<srcSvc>/<dstNs>/<dstSvc>`, with the version banner
   advanced by one and the ticked tools pre-checked.
6. **Discovery-down fallback**: drop the Consul token as in Step 9.2.4,
   refresh `/rules/new`, pick any destination. The tool
   checklist is replaced by the manual-add input only, with an amber
   "Live tool discovery unavailable" banner. You can still save a rule
   by typing tool names in the input.

#### 9.4 Test History and rollback

End-to-end equivalent of Step 6 (history + rollback) through the UI.

1. Click **History** in the sidebar — it navigates to `/history`.
2. The page lists every KV v2 version, current first, with pills:
   `Current` (green) on row 1, `Available` on every other live row,
   `Deleted` / `Destroyed` where applicable.
3. Pick any older `Available` row and click **Roll back**. The button
   transforms in place into `Roll back to vN? [Cancel] [Confirm]`. Click
   **Confirm**. A green banner reads
   `Rolled back to version <N> — wrote new version <n+1>.` The table
   re-fetches and now shows that new version as `Current`.
4. **CAS conflict**: open `/history` in two tabs. In tab A, roll back
   one version. In tab B (still showing the **old** current version),
   try to roll back. The row shows a red error:
   `Catalog has been updated since you loaded this page; reload and retry.`
5. Propagation check — same shape as Step 6.3:

   ```bash
   sleep 3
   kubectl exec deploy/ai-agent -c ai-agent -- \
     curl -sS -i -X POST http://user-mcp.virtual.consul/mcp \
     -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"create_user","arguments":{}}}' \
     | grep -iE '^HTTP/|^x-authz-reason' || true
   ```

   Decisions should reflect the rolled-back catalog within ~3s.

#### 9.5 Test the Agents / MCP Servers browse

Both pages are read-only and serve as a sanity check that role
annotations propagated.

1. Click **Agents** in the sidebar — `/agents` lists Consul services
   carrying `agent-tool-authz-role=agent`. Today that should be exactly
   one row: `ai-agent` in `default`.
2. Click **MCP Servers** — `/mcp-servers` lists services carrying
   `agent-tool-authz-role=mcp-server`. One row: `user-mcp` in `default`.
   The service name is rendered as a link.
3. **No sidecar proxies should appear in either list.** Consul-K8s
   registers Envoy sidecars with `ServiceKind=connect-proxy` and copies
   the parent pod's `ServiceMeta` onto the proxy, so without the
   `ServiceKind` filter both `ai-agent` and `ai-agent-sidecar-proxy`
   would match. The discovery client drops anything with a non-empty
   `ServiceKind`.
4. **Click an MCP server name** — the page navigates to
   `/mcp-servers/default/user-mcp`. The detail view shows a Name +
   Description table populated from the destination's live `tools/list`
   response. The "<n> tools" header matches the row count. Tools whose
   description is missing render an italic "No description provided."
   placeholder. The back-link returns to `/mcp-servers`.
5. **503 fallback**: deleting `/vault/secrets/consul-token` inside the
   pod (as in Step 9.2.4) makes the listing pages render the amber
   "could not load" banner with the upstream 503 message. The detail
   page falls back to the same banner pattern when discovery returns
   502/503. Catalog endpoints stay green.

#### 9.6 Failure-mode checks

```bash
# (a) Tail supervisord output to confirm both processes are running.
kubectl logs deploy/consul-mcp-authz -c consul-mcp-authz \
  | grep -E 'success: (api|ui) entered RUNNING'

# (b) Verify both ports are listening from inside the pod.
POD=$(kubectl get pod -l app=consul-mcp-authz -o jsonpath='{.items[0].metadata.name}')
kubectl exec "$POD" -c consul-mcp-authz -- \
  sh -c 'wget -qO- http://127.0.0.1:8080/healthz && echo ; wget -qO- -S http://127.0.0.1:8502/rules >/dev/null 2>&1 && echo ui_ok || echo ui_fail'
# Expected: {"status":"ok"} then ui_ok.

# (c) Scale to zero to see the deploy ramp back up cleanly (both
#     processes come up together; UI shows the banner only during the
#     ~few-seconds window before the API is ready).
kubectl scale deploy/consul-mcp-authz --replicas=0
kubectl scale deploy/consul-mcp-authz --replicas=1
kubectl rollout status deploy/consul-mcp-authz
```

---

## Local dev (no cluster)

### API

```bash
cd consul-mcp-authz/api
uv sync

# Run unit tests (skipped when opa + policy are not present).
uv run pytest

# Run the API locally against a port-forwarded Vault.
export MCP_AUTHZ_API_VAULT_ADDR=http://localhost:8200
export MCP_AUTHZ_API_VAULT_TOKEN_PATH=/tmp/vault-token
echo "$VAULT_TOKEN" > /tmp/vault-token
export MCP_AUTHZ_API_OPA_BIN=$(which opa)
export MCP_AUTHZ_API_POLICY_DIR="$(pwd)/../../opa-mcp-auth/policy"
uv run uvicorn api.main:app --reload
```

The committed `.env` already wires `MCP_AUTHZ_API_VAULT_ADDR` to the dev
ELB and disables TLS verify; override per-shell as needed.

### UI

The UI is a single `MCP_AUTHZ_UI_API_URL` away from any running API —
in-cluster service DNS, a port-forward, or `uvicorn --reload` all work.

```bash
# In one shell — point at an API.
kubectl port-forward deploy/consul-mcp-authz 8080:8080
# …or run the API locally per the section above.

# In another shell — run the UI dev server.
cd consul-mcp-authz/ui
npm install         # one-time, or after package.json changes
MCP_AUTHZ_UI_API_URL=http://localhost:8080 npm run dev
# → http://localhost:8502/rules, hot reload on save
```

Verification:

```bash
# Typecheck and a clean production build (also runs in CI).
npm run typecheck
npm run build       # finishes with "✓ Compiled successfully", no warnings

# The proxy route mirrors the server-side fetch — useful for sanity
# checks with curl without bringing up the browser.
curl -sS http://localhost:8502/api/v1/rules | jq
```

Tailwind picks up class changes in `src/**/*.{ts,tsx}` automatically; if
a new file isn't being styled, confirm it's matched by the `content`
glob in `tailwind.config.ts`.

---

## Done definition

### API (P1) — accepted when, in-cluster:

1. `kubectl rollout status deploy/consul-mcp-authz` is green.
2. `GET /v1/rules` returns the current Vault catalog.
3. `PUT /v1/rules` adding a tool returns `{"version": n+1, ...}` and the
   ext_authz decision for that tool flips within ~3s — no pod restart.
4. A stale `expected_version` returns `412`.
5. A malformed catalog returns `422` (Pydantic schema) — or `400` if
   the input passes Pydantic but trips the `opa eval` validator — and
   Vault state is unchanged.
6. `PATCH /v1/rules/{src-ns}/{src-svc}/{dst-ns}/{dst-svc}` flips a single
   pair's `allow` list; ext_authz decision for that tool flips within ~3s.
   Stale `expected_version` on PATCH returns `412`.
7. `GET /v1/rules/history` returns every KV v2 version with current first.
8. `POST /v1/rules:rollback` writes the target version as a new version
   (KV v2 never modifies in place); ext_authz decisions reflect the
   rolled-back catalog within ~3s.
9. `GET /v1/agents` and `GET /v1/mcp-servers` return the workloads
   carrying the matching `agent-tool-authz-role` ServiceMeta annotation.
   `GET /v1/mcp-servers/{ns}/{name}/tools` returns the *live* tool list
   from the MCP server (handshake over the mesh, `source: mcp-tools-list`).
   Adding a new `@mcp.tool` to user-mcp and redeploying surfaces it within
   one cache-TTL (5 min) — no annotation edit. Removing the Consul token
   causes only `/v1/agents` and `/v1/mcp-servers` to return `503`; the
   catalog endpoints keep working.

### UI (P2 pilot) — accepted when, in-cluster:

10. Inside the `consul-mcp-authz` container, supervisord shows
    `success: api entered RUNNING state` and
    `success: ui entered RUNNING state`.
11. `kubectl port-forward svc/consul-mcp-authz 8502:8502` →
    `http://localhost:8502/rules` renders every `(src, dst)` pair from
    `GET /v1/rules`, in the Consul-style table, with the Allow pill,
    service/namespace chips on both source and destination, and the
    "<n> tools allowed" status pill driven by each pair's `allow` array
    length.
12. The page header shows `Catalog version <n>` matching the value
    returned by `GET /v1/rules`.
13. Clicking a row navigates to
    `/rules/<srcNs>/<srcSvc>/<dstNs>/<dstSvc>` (the View/Edit page).
    The page shows the source/destination card with the Allow pill,
    `Catalog version <n>`, and a tool checklist whose pre-checked items
    exactly match the pair's `allow` array from `GET /v1/rules`. The
    rest of the checklist is the rest of `tools/list` for that MCP server.
14. Ticking an unchecked tool and clicking **Save changes** flips the
    status line to `Saved as version <n+1>.`; the in-cluster
    `tools/call` propagation check from Step 9.2.2 admits that tool
    within ~3s. Hitting Save with a stale read (two tabs / racing edit)
    produces the red "Catalog has been updated since you read it"
    message and the page reload picks up the new version cleanly.
15. Killing the `api` program inside the container (`supervisorctl stop
    api` via `kubectl exec`) makes both the list and detail pages
    surface the amber error banner; restarting it (`supervisorctl
    start api`) restores them on next refresh.
16. Deleting `/vault/secrets/consul-token` inside the pod makes the
    detail page render the "Live tool discovery unavailable" banner
    while still rendering the pair's existing `allow` list as
    toggleable checkboxes; the catalog endpoints stay 200.
17. `/rules/new` renders Source and Destination pickers populated from
    `/v1/agents` and `/v1/mcp-servers` (no sidecar-proxy rows). Picking
    a destination triggers `GET /v1/mcp-servers/{ns}/{name}/tools` and
    renders the tool checklist. Selecting a `(src, dst)` that already
    exists in the catalog disables Save and shows a link to View/Edit.
    Save creates a new pair via `PATCH` + CAS and navigates to the new
    pair's View/Edit page with the version banner advanced by one.
18. `/history` lists every KV v2 version, current first, with the right
    status pills (`Current`, `Available`, `Deleted`, `Destroyed`).
    Two-phase rollback confirm posts to `/v1/rules:rollback` with
    `expected_version = current_version`; a successful rollback writes
    a new version and the table re-fetches. Concurrent rollback in a
    second tab surfaces the CAS 412 inline.
19. `/agents` and `/mcp-servers` each render exactly the application
    services carrying the matching `agent-tool-authz-role` ServiceMeta —
    `*-sidecar-proxy` entries are filtered out. On `/mcp-servers`, the
    service name is rendered as a link into the detail page. Deleting
    `/vault/secrets/consul-token` makes both pages render the upstream
    503 inline without affecting catalog pages.
20. `/mcp-servers/{ns}/{name}` (detail page) renders a Name + Description
    table sourced from `GET /v1/mcp-servers/{ns}/{name}/tools`. The row
    count in the header matches `tools.length`. Adding a new `@mcp.tool`
    with a docstring to user-mcp and redeploying surfaces the new row +
    its description within one discovery cache TTL. Tools whose
    `description` is omitted by the server render an italic placeholder.
    Hitting `/mcp-servers/default/does-not-exist` returns Next's
    `not-found` (the API replied 404). A 502/503 from discovery shows
    the same amber banner the listing pages use.

When all hold, P1 + P2 pilot are validated. Remaining:
- **P1.5** — OIDC operator auth on the API, `POST /v1/rules:dryRun`.
- **P2 — OIDC login flow (UI)** — iron-session cookie pattern, blocked
  on P1.5.
- **P2 — UI pre-save `dryRun` call** — View/Edit and New Rule call
  `POST /v1/rules:dryRun` before persisting; blocked on the P1.5 endpoint.
- **P2 — Admin-only rollback gating (UI)** — hide the `/history`
  rollback button for non-`admin` roles; blocked on P1.5 OIDC.
