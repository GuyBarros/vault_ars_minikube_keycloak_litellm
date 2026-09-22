# MCP Authorization Console — specification

Specification for the per-tool authorization controls that decide which AI
agents may invoke which tools on which MCP servers. Tracks what is built
today (P0 runtime backbone + P1 API + P2 UI pilot: Rules list, View/Edit,
New Rule, History + rollback, Agents / MCP Servers browse, MCP Server
detail) and what remains (P1.5 OIDC + `dryRun`, P2 OIDC login flow
blocked on P1.5).

Companion to the implementations at `opa-mcp-auth/`, `consul-mcp-authz/api/`,
and `consul-mcp-authz/ui/`. Original plan at
`~/.claude/plans/magical-baking-mccarthy.md`.

## 1. Goal

Operators need to manage `(source-agent → destination-MCP → tool)`
authorization rules without hand-editing Rego or restarting OPA pods.

Pre-pilot state (`deploy-k8s/agent-mcp-authz/`):

- Tool catalog was hardcoded inside the `.rego` file.
- Changing a rule = `kubectl create configmap … --dry-run | apply` then
  `kubectl rollout restart deploy/opa-mcp-authz`.
- No audit trail beyond `git log`, no UI, cold restart per change.

Target state:

- Operators select `(src-ns, agent) → (dst-ns, mcp-server, tool)` in a UI
  or hit a REST API.
- The change is persisted durably, audited (versioned), and propagated to
  every OPA replica within seconds — **no pod restart**.
- Default-deny everywhere; allowlist is the only rule kind.

## 2. Architecture (target end state)

```
Operator (kubectl port-forward svc/consul-mcp-authz 8502)
   │
   ▼  HTTP :8502
┌────────────────────── consul-mcp-authz Pod (one container) ────────────────┐
│  supervisord                                                               │
│    ├─ node    /app/ui/server.js   → Next.js operator console  :8502        │
│    │                                  │ (loopback proxy on /api/v1/*)      │
│    │                                  ▼                                    │
│    └─ uvicorn api.main:app        → FastAPI catalog/discovery :8080        │
│         │                       │                       │                  │
└─────────┼───────────────────────┼───────────────────────┼──────────────────┘
          ▼                       ▼                       ▼
   Vault KV v2            Consul catalog API       MCP servers' tools/list
 (write/version/audit)  (services filtered by      (live, over Consul mesh)
                          ServiceMeta
                          agent-tool-authz-role)
   │
   │   vault-agent sidecar (JWT auth, KV template)
   ▼
opa-mcp-authz Pod
  ├─ vault-agent  → /vault/secrets/data.json      (emptyDir, shared)
  └─ opa --watch  → data.policy.mcp_authz         (fsnotify atomic reload)
                  │
                  ▼  gRPC ext_authz CheckRequest
            user-mcp Envoy sidecar (failureModeAllow: false)
```

Rule lifecycle: operator click → API validates the candidate with
`opa eval` against the live policy → API `vault kv put` (new version,
CAS-checked) → Vault Agent re-renders the file → OPA `--watch` reloads
in-memory data → next ext_authz decision uses the new rules. End to
end: a few seconds.

## 3. Data model (used by every phase)

Vault KV v2 path: `opa-policies/data/mcp-authz/catalog`

```json
{
  "rules": {
    "<source-ns>/<source-svc>": {
      "<dest-ns>/<dest-svc>": {
        "allow": ["<tool-name>", "..."]
      }
    }
  }
}
```

- **The catalog document is the `rules` map and nothing else.** Version,
  `created_time`, `deletion_time`, `destroyed` are stored on the KV v2
  *metadata* path (`opa-policies/metadata/mcp-authz/catalog`), not inside
  the document. `GET /v1/rules` and `GET /v1/rules/history` surface the
  metadata fields to the caller; the policy itself only reads `rules`.
- **No discovery cache in Vault.** The earlier design persisted an
  `mcp_servers` block with `{tools, discovered_at}` per destination; that
  has been removed. Tool discovery is now a live `tools/list` RPC over
  the mesh with an in-process TTL cache in the API
  (`discovery_cache_ttl_seconds`, default 300 s). Drift between a
  deployed MCP server's tool surface and what operators see is bounded
  by that TTL.
- **Two-level keying**: `<src-ns>/<src-svc>` → `<dst-ns>/<dst-svc>`.
  Day-one design includes destination keying even though every current
  service runs in `default`. Forward-compatible with multi-MCP scale-out.
- **Allowlist only.** Anything not in `allow` is denied by the policy's
  `default decision := {"allowed": false, "http_status": 403, …}`.
- **Identity sources (verified against real ext_authz input JSON):**
  - Source: `input.attributes.metadataContext.filterMetadata.consul.{namespace, service}` — Consul's ext_authz extension parses the peer cert.
  - Destination: `input.attributes.destination.principal` — Envoy populates the local SPIFFE URI; the policy splits on `/ns/` and `/svc/`.
  - Consul does **not** inject destination fields into `filterMetadata.consul`.

## 4. Phase 0 — Runtime-reload backbone (built)

Smallest slice that proves storage + reload. No API, no UI. Operators seed
the catalog with `vault kv put`. Lives at `opa-mcp-auth/` in the repo
root, parallel to (not replacing) `deploy-k8s/agent-mcp-authz/`.

### 4.1 Directory layout

```
opa-mcp-auth/
├── README.md                              # 8-step manual test guide
├── opa-mcp-authz.yaml                     # OPA Deployment + Service + ConfigMap
│                                           with vault-agent sidecar annotations
├── policy/
│   └── mcp/authz/
│       ├── mcp_authz.rego                 # data-driven policy
│       └── mcp_authz_test.rego            # 13 tests, all PASS
└── vault/
    ├── policies.hcl                       # Vault policy + JWT role (manual + TF)
    └── seed-catalog.sh                    # one-shot vault kv put
```

### 4.2 Policy contract

Package `mcp.authz`. Decision document at `data.mcp.authz.decision`,
configured into Envoy ext_authz via OPA's `envoy_ext_authz_grpc` plugin
(`path: mcp/authz/decision`).

Decision shape:

```rego
default decision := {
  "allowed": false,
  "http_status": 403,
  "headers": {"x-authz-reason": "default-deny"},
}

# Allow path (only when `allow` rule fires)
decision := {
  "allowed": true,
  "headers": {
    "x-authz-agent":  agent_service,   # e.g. "default/ai-agent"
    "x-authz-dest":   dest_service,    # e.g. "default/user-mcp"
    "x-authz-method": method,
  },
} if allow

# Explicit deny reason for any not-allowed input
# e.g. "agent=default/ai-agent dest=default/user-mcp method=tools/call tool=create_user not permitted"
```

`allow` rule:

```rego
default catalog := {}
catalog := data.policy.mcp_authz.rules

known_pair if catalog[agent_service][dest_service]

# Handshake + surface enumeration: the mesh has already gated reachability
# via mTLS + ServiceIntention, so no catalog entry is required.
allow if {
  agent_service != ""
  method in discovery_methods
}

# Methods that actually return content reuse the catalog membership check.
allow if {
  known_pair
  method in catalog_gated_methods
}

# tools/call additionally needs the tool in the allowlist for that pair.
allow if {
  known_pair
  method == "tools/call"
  tool_name in catalog[agent_service][dest_service].allow
}
```

Critical detail — **data path is `data.policy.mcp_authz`, not
`data.mcp.authz.catalog`**. The latter collides with the policy's own
package (`mcp.authz`) and OPA flags the catalog rule as recursive. The
vault-agent template wraps the KV payload as
`{"policy":{"mcp_authz":{...}}}` so the data sits outside the package
namespace.

Method classification:
- `discovery_methods` — `initialize`, `notifications/initialized`,
  `ping`, `tools/list`, `resources/list`, `prompts/list`. **Mesh-gated
  only.** A new agent ↔ MCP-server pair starts authorized for discovery
  the moment the `ServiceIntention` is in place; no catalog edit needed.
- `catalog_gated_methods` — `resources/read`, `prompts/get`. These
  return content, so they require a `known_pair`.
- `tools/call` — requires `known_pair` and the tool to be in that
  pair's `allow` list.

Rationale: Envoy verifies the mTLS peer cert and Consul's
`ServiceIntention` decides whether A may reach B at all — that *is* an
authorization gate. Re-asserting it in the catalog adds operational
toil (an `{allow: []}` entry per source × destination just to enumerate
tools) without raising the security bar, because tool names are already
implicit in the deployment manifest. The catalog therefore focuses
where it adds value: which tools each authorized caller may actually
invoke.

### 4.3 Storage & reload

- **Storage**: Vault KV v2 mount `opa-policies` (existing), path
  `opa-policies/data/mcp-authz/catalog`. KV v2 gives versioning, audit,
  and rollback for free.
- **Reload**: Vault Agent sidecar on the `opa-mcp-authz` pod authenticates
  via JWT (`auth/k8s_jwt`, ServiceAccount `opa-mcp-authz` in `default`
  ns) and renders the catalog into `/vault/secrets/data.json` (emptyDir
  shared with the OPA container). OPA started with
  `--watch /policy /vault/secrets` reloads atomically on every fsnotify
  event. No pod restart.

Vault Agent template:

```hcl
{{- with secret "opa-policies/data/mcp-authz/catalog" -}}
{"policy":{"mcp_authz":{{ .Data.data | toJSON }}}}
{{- end -}}
```

**Filename matters.** The rendered file is named `data.json` — not
`catalog.json` — on purpose. OPA's positional file loader has a single
special case: files named `data.json` / `data.yaml` are merged into the
parent directory's data namespace, while any other filename `foo.json`
nests its content under `data.foo.…`. Since `/vault/secrets` is the load
root, `data.json` content lands at `data.policy.mcp_authz.rules`, which
is what the policy reads. `catalog.json` would land at
`data.catalog.policy.mcp_authz.rules`, leave the policy's lookup
undefined, fall through to `default catalog := {}`, and default-deny
everything. Tests don't catch this (they bind data inline) — only the
in-cluster Step 6 baseline test would.

OPA args (relevant flags):

```
--ignore=.*                # skip ConfigMap symlink dirs (..data, ..2026_…)
--watch                    # fsnotify-based atomic reload
/policy /vault/secrets     # watched paths
```

### 4.4 Test coverage (14/14 PASS)

Tests bind the catalog inline via
`with data.policy.mcp_authz as pilot_catalog`:

| # | Test | What it locks in |
|---|------|------------------|
|  1 | `test_allowed_tool_call_list_all_users` | Read tool allowed |
|  2 | `test_allowed_tool_call_update_user`    | Write tool allowed |
|  3 | `test_denied_tool_create_user`          | Unknown tool denied |
|  4 | `test_denied_tool_delete_user`          | Unknown tool denied |
|  5 | `test_allowed_tools_list`               | tools/list allowed for known pair |
|  6 | `test_allowed_initialize`               | initialize allowed for known pair |
|  7 | `test_unknown_source_agent_denied_for_tools_call` | Unknown source can discover but cannot tools/call |
|  8 | `test_source_namespace_isolation_denied`| Source ns is part of identity (tools/call) |
|  9 | `test_unknown_destination_denied`       | Destination keying enforced (tools/call) |
| 10 | `test_destination_namespace_isolation_denied` | Destination ns is part of identity (tools/call) |
| 11 | `test_empty_catalog_default_denies`     | Fail-closed on missing data (tools/call) |
| 12 | `test_missing_consul_metadata_denied`   | Fail-closed on missing source (no mesh identity → no discovery either) |
| 13 | `test_malformed_body_denied`            | Fail-closed on bad JSON |
| 14 | `test_discovery_allowed_without_catalog_entry` | tools/list is mesh-authorized; no catalog entry needed |

### 4.5 Phase 0 — Done definition

All six steps in `opa-mcp-auth/README.md` pass against the cluster:

1. `opa test opa-mcp-auth/policy/ -v` → 14/14 PASS.
2. Vault policy + JWT role created.
3. Seed catalog written; `vault kv metadata get` shows version 1.
4. OPA Deployment + vault-agent sidecar healthy; render log shows
   `rendered "…data.json"` (filename intentionally — see §4.3).
5. Baseline parity: `list_all_users` returns 200, `create_user` returns
   403 with the expected `x-authz-reason` header.
6. Live reload: `vault kv put` adding `create_user` flips the response to
   200 within ~3s with no `kubectl rollout`; rolling back flips it back
   to 403.

P0 is **complete pending operator validation** of steps 2–6 in the
cluster.

## 5. Phase 1 — Backend API (`consul-mcp-authz`) — built

Lives at `consul-mcp-authz/api/`. The umbrella project
`consul-mcp-authz/` houses both this API and the P2 operator UI
(`consul-mcp-authz/ui/`); both workloads share the same Vault catalog
data path and project README.

### 5.1 Stack & layout

- FastAPI on Python 3.12, `uv` for dependency management, mirroring
  `token-exchange/` conventions.
- `hvac` for Vault access; the API's own Vault token is rendered to disk
  by a Vault Agent sidecar (`vault.hashicorp.com/agent-inject-token`)
  and re-read per request so token rotation is transparent.
- Bakes the `opa` binary into its Docker image to validate every
  candidate catalog with `opa eval` against the live policy before
  persisting (a `policy/` snapshot is copied into the image build
  context from `opa-mcp-auth/policy/`).
- Vault policy `consul-mcp-authz`: `create`, `read`, `update` on
  `opa-policies/data/mcp-authz/catalog`; `read` on the corresponding
  `metadata/` path. The Consul ACL read-only token for discovery is
  seeded into Vault KV at `opa-policies/data/consul/mcp-authz-token`
  and rendered onto the API pod by vault-agent. See
  `opa-mcp-auth/vault/policies.hcl` for the `vault policy write` /
  `vault write auth/k8s_jwt/role/...` commands.

### 5.2 Endpoints

| Verb | Path | Status | Behaviour |
|------|------|--------|-----------|
| `GET`  | `/healthz`                               | ✅ built | Liveness/readiness. |
| `GET`  | `/v1/agents`                             | ✅ built | List Consul services with `service-meta-agent-tool-authz-role=agent`. 503 if the rendered Consul token is missing/empty (discovery is opt-in). |
| `GET`  | `/v1/mcp-servers`                        | ✅ built | List Consul services with `service-meta-agent-tool-authz-role=mcp-server`. Same 503 contract as `/v1/agents`. |
| `GET`  | `/v1/mcp-servers/{ns}/{name}/tools`      | ✅ built | Live MCP `tools/list` RPC via the mesh (`initialize` → `notifications/initialized` → `tools/list`). Response shape: `{namespace, name, tools: [{name, description}], source}` — `description` is `null` when the MCP server omits it. In-process cache for `discovery_cache_ttl_seconds` (default 300 s). 404 if no such Consul service has the `mcp-server` role; 502 with a diagnostic `detail` if the `ServiceIntention` is missing or the server is unhealthy. Discovery is mesh-authorized — no per-pair catalog entry needed. |
| `GET`  | `/v1/rules`                              | ✅ built | Return the current catalog + KV v2 `version` and `created_time`. |
| `PUT`  | `/v1/rules`                              | ✅ built | Replace catalog atomically. Pydantic 422 → `opa eval` 400 → CAS write (412 on stale `expected_version`). |
| `PATCH`| `/v1/rules/{src-ns}/{src-svc}/{dst-ns}/{dst-svc}` | ✅ built | Read-modify-write a single pair under the same CAS contract. |
| `GET`  | `/v1/rules/history`                      | ✅ built | List every KV v2 version (current first) with `created_time`, `deletion_time`, `destroyed`. |
| `POST` | `/v1/rules:rollback`                     | ✅ built | Read the catalog at version `N` and re-run Pydantic + `opa eval` against the *current* policy, then write it as a new version. KV v2 never modifies in place. |
| `POST` | `/v1/rules:dryRun`                       | 🟡 P1.5  | Run `opa eval` on a candidate without persisting. Today every PUT/PATCH already validates server-side; this endpoint surfaces the same check for UI pre-save sanity. |

### 5.3 Auth & RBAC (deferred to P1.5)

The pilot has **no operator auth**. The API is reachable only via
`kubectl port-forward` or in-cluster from approved sources (UI workload,
ServiceIntention-gated). Once exposed beyond port-forward, P1.5 will
add:

- Operator auth: Vault OIDC (issuer already configured for `web-app`).
- FastAPI verifies the bearer JWT against Vault's JWKS endpoint.
- Roles derived from OIDC group claims:
  - `viewer` — `GET` only.
  - `editor` — `GET` + `PATCH` + `PUT` + `dryRun`.
  - `admin` — all of editor + `rollback` + future destructive ops.

### 5.4 Write safety

Every persisting endpoint runs the candidate document through the
embedded `opa` binary before writing to Vault. Implementation in
`api/catalog/validator.py`:

1. Wrap the candidate in the same `{"policy":{"mcp_authz":{…}}}`
   envelope that vault-agent renders on the OPA pod.
2. `opa eval --data <wrapped> --data <policy_dir> 'data.policy.mcp_authz.rules'`
   — confirms the data document loads, the live policy still compiles,
   and the rules path resolves to a JSON object.
3. Reject (`400`) if eval errors or the resolved value isn't an object.
   Pydantic catches structural problems before this step and returns
   `422` instead — `400` is reserved for the Pydantic↔policy drift case.
4. Write with `vault kv put -cas=<expected_version>`; map a Vault
   `412 Precondition Failed` back to a `412` on the API for concurrent
   edits.

Rollback (`POST /v1/rules:rollback`) re-runs the same Pydantic + `opa
eval` checks against the *current* policy so an older version cannot
reintroduce a schema or rule shape the current policy rejects.

### 5.5 Discovery

Two facts about each participating workload:

| Question | Source | How |
|----------|--------|-----|
| Which workloads can be a source / destination? | Consul `ServiceMeta` annotation `agent-tool-authz-role` | Pods set `consul.hashicorp.com/service-meta-agent-tool-authz-role: agent` or `mcp-server`. `/v1/agents` and `/v1/mcp-servers` filter the catalog on that key. Instances with a non-empty `ServiceKind` (sidecar proxies, gateways) are dropped — Consul-K8s copies the parent pod's `ServiceMeta` onto the proxy registration, so without this filter `ai-agent-sidecar-proxy` would shadow `ai-agent`. |
| What tools does an MCP server expose? | Live MCP `tools/list` RPC over the mesh | `/v1/mcp-servers/{ns}/{name}/tools` performs the standard handshake against `http://{name}.virtual.consul/mcp` (URL pattern in `config/settings.py`) and parses both `application/json` and `text/event-stream` responses. |

Preconditions for tool discovery against a new MCP server:

- **Mesh authorization** — a Consul `ServiceIntention` allowing
  `consul-mcp-authz → <mcp-server>`. This is the only prerequisite.

No catalog entry is required for the API's own `tools/list` calls. The
OPA policy classifies the MCP handshake + enumeration methods as
mesh-authorized, so the `ServiceIntention` + Envoy mTLS check are the
authorization gate. The catalog is reserved for `tools/call`,
`resources/read`, and `prompts/get`. Failure-mode mapping:

| Outcome | When |
|---------|------|
| `404`   | The named service isn't in Consul or is missing the `mcp-server` role. |
| `502`   | ServiceIntention missing, MCP server down, or transport error. |
| `503`   | The rendered Consul token is missing or empty (affects `/v1/agents`, `/v1/mcp-servers`, and the prerequisite-check inside `/v1/mcp-servers/.../tools`). |

Auth: the API sends no `Authorization` header on the discovery call.
`user-mcp` accepts unauth `tools/list` when
`USER_MCP_ALLOW_UNAUTH_DISCOVERY=true` because the mesh edge already
authenticated the peer via mTLS and OPA already gated the call.
`tools/call` keeps requiring a real JWT.

## 6. Phase 2 — Operator console (`consul-mcp-authz/ui/`) — pilot built

Source lives at `consul-mcp-authz/ui/`. Next.js 15 + React 19, TypeScript
strict, **Tailwind 3.4 with Consul-derived design tokens** (not IBM
Carbon — Carbon would have fought the Consul aesthetic the user wanted
for parity with the live Consul UI).

**Packaged in the same container as the API.** The parent
`consul-mcp-authz/Dockerfile` is a multi-stage build that compiles the
Next.js standalone bundle and the Python venv, then assembles them into
a single runtime image (`panchalravi/consul-mcp-authz`). Inside that
image supervisord runs `uvicorn` on `:8080` and `node /app/ui/server.js`
on `:8502`; the Next server proxies to `127.0.0.1:8080` over loopback,
so there is no separate Pod, Service, Deployment, or ServiceIntention
for the UI. Operators reach the UI via
`kubectl port-forward svc/consul-mcp-authz 8502:8502`.

### 6.1 Design tokens

The chrome is tuned to match Consul's UI conventions, derived from the
live Consul UI screenshots:

- Dark sidebar `#1B1C1F` (`nav-bg`) with `#27292E` surfaces and
  `#33363D` selected-row highlight; sidebar text `#E5E7EB`,
  muted `#8A8F98`.
- White content area, `#0B0C0E` ink, `#6B6F76` muted, `#E5E7EB` borders.
- HashiCorp Electric Blue `#1B49E2` for the primary CTA.
- Allow pill: `#E6F4EA` background, `#1A7240` ink, green arrow glyph —
  exactly the Intention Type column treatment.
- Status pill: `#F1F2F5` background, `#3A3D45` ink — the
  "Managed by CRD" / "<n> tools allowed" treatment.
- Inter typography via `next/font/google`.

Tokens live in `consul-mcp-authz/ui/tailwind.config.ts` so re-skinning
is one file.

### 6.2 Screens (pilot status)

| # | Screen | Status | Notes |
|---|--------|--------|-------|
| 1 | **Rules list** (`/rules`) — flat view of every `(src, dst)` pair, with the Allow pill, namespace/partition chips on both sides, and `<n> tools allowed` status pill | ✅ pilot end-to-end | Server-rendered from `GET /v1/rules` via `src/lib/api.ts`; flattens the two-level catalog map into one row per `(src, dst)`. Rows are clickable into screen #3. Header has a `Create` button linking to screen #2. Page also exposes `Catalog version <n> · written <ts>` from the same response. |
| 2 | **New Rule form** (`/rules/new`) — Source (from `/v1/agents`) + Destination (from `/v1/mcp-servers`) + tool checklist (from `/v1/mcp-servers/{ns}/{name}/tools`); save via `PATCH` | ✅ pilot end-to-end | Server component pre-loads agents, MCP servers, and current rules in parallel. Client component (`new-rule-form.tsx`) does lazy tool discovery on destination change (with `AbortController`-cancelled stale requests) and a manual-add fallback when discovery returns 404/502/503. Pair-already-exists guard renders an amber warning with a link to View/Edit and disables Save — preventing silent overwrites via PATCH. Successful save navigates to the new pair's View/Edit page. |
| 3 | **View / Edit a pair** (`/rules/[srcNs]/[srcSvc]/[dstNs]/[dstSvc]`) — read-only header (source/destination/Allow pill/catalog version) plus an editable tool checklist; tool list is the union of `/v1/mcp-servers/{ns}/{name}/tools` and the pair's current `allow` list; "Save changes" issues a `PATCH` with the read version as `expected_version` | ✅ pilot end-to-end | Server-rendered page (`page.tsx`) + client form (`edit-form.tsx`); browser-facing PATCH at `/api/v1/rules/[srcNs]/[srcSvc]/[dstNs]/[dstSvc]/route.ts`. Discovery 404/502/503 falls back to "live tool discovery unavailable" banner and a free-text "Add tool name" input; CAS 412 surfaces as "catalog was updated — reload to pick up the latest version". `router.refresh()` re-fetches the server component on successful save so the version banner updates without a full reload. |
| 4 | **History + rollback** (`/history`) — `GET /v1/rules/history` versions with status pills, two-phase rollback confirm, `POST /v1/rules:rollback` with CAS | ✅ pilot end-to-end | Server component fetches history; client component (`history-table.tsx`) renders one row per KV v2 version with `Current` / `Available` / `Deleted` / `Destroyed` pills. Rollback is two-phase inline (`Roll back` → `Roll back to vN? [Cancel] [Confirm]`) — no `window.confirm`. POST passes `expected_version = current_version`; a concurrent rollback returns 412 and the row surfaces "catalog has been updated since you loaded this page; reload and retry" inline. The browser-facing proxy `/api/v1/rules/rollback` drops the upstream `:rollback` colon (filesystem routing doesn't love it) and re-attaches it in the upstream call. No admin-only gate yet — pending P1.5 OIDC. |
| 5 | **Agents / MCP Servers browse** (`/agents`, `/mcp-servers`) — read-only listings of services carrying the matching `agent-tool-authz-role` ServiceMeta | ✅ pilot end-to-end | Server-rendered from `GET /v1/agents` and `GET /v1/mcp-servers` via `ServicesTable` (`components/services-table.tsx`). Used as a sanity check that role annotations propagated. Sidecar proxies are filtered out at the API layer (§5.5). On `/mcp-servers`, the table's `linkFor` prop wires the service name to screen #6; `/agents` stays read-only. No "Re-discover tools" button — `tools/list` is lazy and per-destination, surfaced on screens #2, #3, and #6 rather than on the browse pages. |
| 6 | **MCP Server detail** (`/mcp-servers/[ns]/[name]`) — Name + Description table of every tool the destination's live `tools/list` advertises, with back link to `/mcp-servers` | ✅ pilot end-to-end | Server-rendered from `GET /v1/mcp-servers/{ns}/{name}/tools`. 404 from the API (service isn't registered or lacks the `mcp-server` role) renders Next's `notFound()`; 502/503 surface in the same amber banner pattern used elsewhere. Empty `tools/list` shows a "No tools advertised" empty state. Tools whose `description` is `null` render an italic "No description provided." placeholder. Read-only — operators edit allow lists from screens #2 / #3, not here. |

### 6.3 Layout & data flow

- App Router with one server component per page; pages call `api.*`
  helpers in `src/lib/api.ts` directly so the upstream URL stays on the
  server, never in the browser bundle.
- Browser-facing proxy routes under `/api/v1/*` forward client-side
  requests through the same `src/lib/api.ts` helpers — the indirection
  exists so P1.5 auth headers / session cookies can be applied in one
  place. Current set:
  - `GET  /api/v1/rules`
  - `PATCH /api/v1/rules/[srcNs]/[srcSvc]/[dstNs]/[dstSvc]`
  - `GET  /api/v1/rules/history`
  - `POST /api/v1/rules/rollback` *(drops the upstream `:rollback` colon)*
  - `GET  /api/v1/agents`
  - `GET  /api/v1/mcp-servers`
  - `GET  /api/v1/mcp-servers/[ns]/[name]/tools`
- Upstream URL is `MCP_AUTHZ_UI_API_URL` (defaults to
  `http://127.0.0.1:8080`). The default is loopback because the UI and
  API ship in the same container; override it for local dev when the
  Next dev server points at a port-forwarded API.
- Error path: any non-2xx from upstream surfaces in an amber banner on
  the page with the exact upstream `detail` — the page never crashes on
  a missing API. The View/Edit page separately renders a "live tool
  discovery unavailable" banner when only the discovery side fails,
  while the catalog side keeps working.

### 6.4 Interactions

Built today:

- **View/Edit (#3) — PATCH + CAS save.** Tool checkbox toggle stages an
  in-memory change; "Save changes" issues `PATCH /v1/rules/{src-ns}/{src-svc}/{dst-ns}/{dst-svc}`
  with the catalog version that was read into the page as
  `expected_version`. On success `router.refresh()` re-fetches the
  server component so the version banner and pre-checked state move
  forward without a full reload. Concurrent-edit `412` from the API
  renders inline: "Catalog has been updated since you read it;
  re-fetch and retry. (Reload to pick up the latest version.)" P1.5
  OIDC will let the message name the operator who made the conflicting
  edit.
- **New Rule (#2) — same PATCH + CAS, with a pair-exists guard.** PATCH
  is a read-modify-write that creates the pair if absent, so creates
  and edits hit the same endpoint. The form additionally pre-loads the
  current catalog and refuses to save when the picked `(src, dst)`
  already exists — an amber link-to-View/Edit prevents silent overwrites.
- **History (#4) — two-phase rollback + CAS.** The `Roll back` action
  on a row transforms in place into `Roll back to vN? [Cancel] [Confirm]`
  (no `window.confirm`). Confirming posts to `/v1/rules:rollback` with
  `expected_version = current_version`; a successful rollback writes a
  new KV v2 version and the table re-fetches via `router.refresh()`.
  A concurrent rollback in another tab returns 412 and the row surfaces
  the same "reload to pick up the latest version" message inline.
- **Discovery-side failures** (`/v1/mcp-servers/{ns}/{name}/tools`
  returning 404/502/503) are handled separately from catalog-side
  failures on both #2 and #3: the amber "live tool discovery
  unavailable" banner appears and the form falls back to a free-text
  "Add tool name (advanced)" input. On #3 the pair's current `allow`
  list is still rendered as toggleable checkboxes so an operator can
  still narrow the allow list while discovery is down.

Pending:

- Every save will additionally call `POST /v1/rules:dryRun` first as a
  UI-side sanity check (P1.5 dependency); the API runs `opa eval` again
  server-side on the persisting endpoint regardless (defense in depth).
- Admin-only gating on rollback (today rollback is accessible to anyone
  reaching the API — blocked on P1.5 OIDC + role claims).

## 7. Scale & performance

**Topology**: one centralized `opa-mcp-authz` Deployment that every MCP
server's Envoy sidecar consults via gRPC ext_authz. One catalog document
in Vault → one rendered JSON file per OPA pod → all decisions answered
from in-memory data.

**Per-request cost is flat in catalog size.** Rego eval is two map
lookups (`catalog[agent_service][dest_service]`) plus a membership check
on a short `allow` list — O(1) + O(allow-list size, typically <20).
Adding the 1,000th agent or the 100th MCP server does not change
per-request latency. OPA handles 10k+ decisions/sec with maps of millions
of entries on modest hardware.

**Latency budget**: the ext_authz gRPC round-trip dominates (~1–5 ms
in-pod). OPA's eval stays sub-millisecond regardless of catalog size.

**Memory**: ~50 bytes per rule. 1,000 agents × 50 MCP servers × 20 tools
≈ 50 MB, well inside the current 512 Mi OPA container limit.

**Where the design hits limits — and the growth path:**

| Pressure | Mitigation when it arises |
|---|---|
| Many concurrent ext_authz calls | Scale `opa-mcp-authz` replicas. Stateless; each pod renders from Vault independently. |
| Reload payload >> 1 MB | Shard catalog by destination in Vault: one KV path per MCP server (`opa-policies/data/mcp-authz/by-dest/<ns>/<svc>`). Vault Agent renders all paths into separate files; OPA loads them as separate data documents. Each edit re-renders one shard. |
| Per-MCP RBAC (operators of `payments-mcp` shouldn't edit `user-mcp` rules) | Same sharding; per-path Vault ACLs give per-destination edit rights. |
| Full failure isolation per MCP server | Move to OPA-as-sidecar on each MCP server, each loading only its destination's shard. Centralized OPA can stay for legacy MCPs while the sidecar pattern is introduced for new ones. |

**Critical forward-compatibility property**: the P0 data model
(`rules[<src-ns>/<src-svc>][<dst-ns>/<dst-svc>].allow`) already has
destination keying. Sharding by destination later is a refactor of the
Vault layout + Vault-Agent template only — **zero changes** to the
policy contract, the API surface, or the UI. The pilot is forward-
compatible with the scale-out path.

For today's footprint (1 agent, 1 MCP server) and even 10× growth, the
single-catalog design is correct and simplest. Defer sharding until a
concrete signal (catalog >1 MB, observed eval >5 ms, or a real RBAC
requirement) actually appears.

## 8. Failure modes (fail-closed by default)

| Scenario | Behaviour | Why |
|----------|-----------|-----|
| Vault unreachable at OPA boot | vault-agent init blocks → OPA never starts → readiness red → Consul drops the endpoint → user-mcp's `failureModeAllow: false` returns 403 to every request. | Fail-closed. |
| Vault unreachable at steady state | Last-rendered `data.json` stays on disk; OPA keeps deciding against it. New rules don't propagate until Vault is back. | Continuity over momentary correctness. |
| Vault Agent template render error | Agent logs the error and keeps the previous file. | Same as above. |
| Catalog missing entirely (no rule ever written) | `default catalog := {}` → `known_pair` false for every input → default-deny everything. | Fail-closed. |
| Malformed JSON on disk | OPA `--watch` rejects on parse, keeps previous in-memory data, logs the error. | OPA is conservative. |
| Source `consul` metadata missing on the ext_authz input | `default consul_metadata := {}` → `agent_service` undefined → `known_pair` false → deny. | Fail-closed. |
| Destination `principal` missing or malformed | `default dest_service := ""` → `known_pair` false → deny. | Fail-closed. |

## 9. Explicit non-goals

- **No deny rules. No wildcards. No precedence.** Default-deny does that
  work; the only operator-managed construct is the per-pair `allow` list.
- **No automatic `ServiceIntentions` for new (agent → mcp) pairs.** That
  remains a manual `kubectl apply`. Giving the API kubeconfig access
  expands the trust boundary and is a separate decision.
- **No user-JWT validation inside the ext_authz call.** Per prior
  conversation: "Do not include user JWT validation, will implement this
  feature later." The current authz scope is purely workload→workload
  (agent identity, not human user).
- **The console replaces operator hand-editing of the catalog, not the
  Rego policy itself.** Policy code ships in git through normal review.

## 10. Decisions confirmed with the user

1. **Storage**: Vault KV v2 at `opa-policies/data/mcp-authz/catalog`. KV v2
   versioning is the audit trail.
2. **Reload**: Vault Agent sidecar + OPA `--watch`. No webhook, no
   bundle server.
3. **Pilot-first phasing**: build P0 end-to-end and pause for cluster
   validation before producing P1 or P2.
4. **Allowlist only**: existing `default decision := {allowed: false}`
   gives fail-closed for free; deny rules add no expressiveness.
5. **Two-level keying** (`<ns>/<svc>` on both source and destination)
   from day one, even though every current service runs in `default`.
6. **UI is a new app** (`consul-mcp-authz/ui/`, co-located with the
   API), not an extension of `web-app/`. Stack differs from `web-app/`
   on purpose: Tailwind with Consul-derived tokens instead of IBM
   Carbon, because the audience (security operators) already lives in
   the Consul UI day to day and the visual continuity matters more than
   reusing Carbon components.
7. **Identity inputs** (verified against real ext_authz JSON):
   - Source from `metadataContext.filterMetadata.consul.{namespace,
     service}`.
   - Destination from `input.attributes.destination.principal` SPIFFE
     URI (Consul does NOT inject destination fields into filter
     metadata).

## 11. File map

| Path | State | Purpose |
|------|-------|---------|
| `opa-mcp-auth/README.md` | done | 8-step manual test guide |
| `opa-mcp-auth/policy/mcp/authz/mcp_authz.rego` | done | Data-driven policy |
| `opa-mcp-auth/policy/mcp/authz/mcp_authz_test.rego` | done | 13 tests, all PASS |
| `opa-mcp-auth/opa-mcp-authz.yaml` | done | Deployment + Service + ConfigMap with vault-agent sidecar |
| `opa-mcp-auth/vault/policies.hcl` | done | Vault policy + JWT role snippets: `opa-mcp-authz` (read), `consul-mcp-authz` (create/read/update + KV metadata read) |
| `opa-mcp-auth/vault/seed-catalog.sh` | done | One-shot catalog seed |
| `consul-mcp-authz/README.md` | done | Project umbrella README — Step 1-8 (API) + Step 9 (UI) + Done definition for both |
| `consul-mcp-authz/Dockerfile` | done | Multi-stage build: ui-builder + api-builder + opa + node binary → single runtime image |
| `consul-mcp-authz/supervisord.conf` | done | Process supervisor for `uvicorn` + `node /app/ui/server.js` |
| `consul-mcp-authz/api/` | P1 built | FastAPI service: GET/PUT/PATCH `/v1/rules`, history, rollback, agents/mcp-servers, live `tools/list`. Deferred to P1.5: OIDC auth, `POST /v1/rules:dryRun` |
| `consul-mcp-authz/ui/` | P2 pilot built (screens #1–#6) | Next.js console source (no separate Dockerfile). Rules list (`/rules`), New Rule (`/rules/new`), View/Edit (`/rules/[srcNs]/[srcSvc]/[dstNs]/[dstSvc]`), History (`/history`), Agents (`/agents`), MCP Servers (`/mcp-servers`), MCP Server detail (`/mcp-servers/[ns]/[name]`) — all end-to-end. Remaining UI work: OIDC login flow, blocked on P1.5. |
| `deploy-k8s/consul-mcp-authz.yaml` | done | Single Deployment + Service + SA. One container exposes both ports (`8080` API, `8502` UI). Vault-agent token sink + Consul token sink. |
| `deploy-k8s/service-intentions.yaml` | done | Includes `consul-mcp-authz → user-mcp` (discovery). No UI intention needed — UI is in-process with the API. |
| `deploy-k8s/ai-agent.yaml`, `deploy-k8s/user-mcp.yaml` | done | Workloads carry `consul.hashicorp.com/service-meta-agent-tool-authz-role` annotations (`agent` / `mcp-server`) |
| `deploy-k8s/agent-mcp-authz/` | retire after P0 acceptance | Existing ConfigMap-only setup; kept until pilot is validated |
| `~/.claude/plans/magical-baking-mccarthy.md` | reference | Original detailed plan |
