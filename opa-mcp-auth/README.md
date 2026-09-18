# opa-mcp-auth — Data-driven MCP authorization pilot

Pilot that moves the `mcp.authz` policy from a hardcoded Rego catalog
loaded by ConfigMap to a **data-driven** policy whose rule set lives in
Vault KV v2 and is hot-reloaded into OPA by a Vault Agent sidecar — no
pod restart on rule changes.

Apply this directory together with `deploy-k8s/opa-mcp-authz.yaml` and
the user-mcp ext_authz wiring (`deploy-k8s/service-defaults-user-mcp.yaml`
plus the `user-mcp` block in `deploy-k8s/service-intentions.yaml`) to
exercise the runtime-reload backbone end-to-end.

## How it works

```
operator                       Vault KV v2 (opa-policies/data/mcp-authz/catalog)
   │                                          │
   │ vault kv put …                            │ vault-agent sidecar (JWT auth, KV template)
   ▼                                          ▼
                                       /vault/secrets/data.json (emptyDir, shared)
                                                │
                                                ▼
                                       OPA --watch reloads
                                       data.policy.mcp_authz
                                                │
                                                ▼
                                       gRPC ext_authz CheckRequest
                                       from user-mcp's Envoy sidecar
                                                │
                                                ▼
                                       data.mcp.authz.decision
```

The Rego policy stays in this repo (it's source code). Only the *catalog
data* — which (source-agent → destination-MCP) pairs may invoke which
tools — moves into Vault.

Catalog shape (one JSON document in KV v2):

```json
{
  "rules": {
    "default/ai-agent": {
      "default/user-mcp": {
        "allow": ["list_all_users", "search_users_by_first_name", "update_user_by_email"]
      }
    }
  }
}
```

Anything not in `allow` is denied by the policy's `default decision := {allowed:false}`.
A missing or empty catalog default-denies everything — fail-closed.

## Files

| Path | Purpose |
|------|---------|
| `policy/mcp/authz/mcp_authz.rego` | Data-driven policy. Reads catalog from `data.policy.mcp_authz.rules`. |
| `policy/mcp/authz/mcp_authz_test.rego` | 14 unit tests covering allow/deny scenarios, namespace isolation, empty/malformed inputs. |
| `../deploy-k8s/opa-mcp-authz.yaml` | OPA Deployment with vault-agent sidecar, `--watch /policy /vault/secrets`. |
| `../deploy-k8s/service-defaults-user-mcp.yaml` | Consul `ServiceDefaults` that wires user-mcp's inbound Envoy `builtin/ext-authz` at the `opa-mcp-authz` gRPC service. |
| `../deploy-k8s/service-intentions.yaml` (`user-mcp` block) | Allows `ai-agent` and `consul-mcp-authz` to reach user-mcp through the mesh. |
| `vault/policies.hcl` | Vault policy + JWT auth role snippets (manual + Terraform forms). |
| `vault/seed-catalog.sh` | One-shot `vault kv put` to seed the initial catalog. |

## Prerequisites

- Existing Consul service mesh on K8s with sidecar injection.
- `ai-agent` and `user-mcp` deployed and meshed (already in `deploy-k8s/`).
- Vault deployed, KV v2 mount `opa-policies` enabled, JWT auth method
  enabled at `auth/k8s_jwt`. The Vault Agent injector mutating webhook
  must be running in-cluster.
- `kubectl` and `vault` CLI available; `VAULT_ADDR` and `VAULT_TOKEN`
  exported on your shell.
- (Optional, for local policy iteration) the `opa` CLI, or use Docker:
  `docker run --rm openpolicyagent/opa:latest ...`.

## Step 1 — Run the unit tests locally

Verifies the policy compiles and the catalog/destination logic is correct
before touching the cluster.

```bash
# With opa CLI installed:
opa test opa-mcp-auth/policy/ -v

# Or via Docker:
docker run --rm \
  -v "$PWD/opa-mcp-auth/policy:/policy:ro" \
  openpolicyagent/opa:latest test /policy/ -v
```

Expect: `PASS: 14/14`.

## Step 2 — Create the Vault policy + JWT role

Pick **one** of the two approaches.

### (a) Manual (fastest for the pilot)

```bash
# Vault policy: read on the KV path.
cat <<'EOF' | vault policy write opa-mcp-authz -
path "opa-policies/data/mcp-authz/catalog" {
  capabilities = ["read"]
}
EOF

# JWT auth role bound to the opa-mcp-authz ServiceAccount in default ns.
vault write auth/k8s_jwt/role/opa-mcp-authz \
  role_type="jwt" \
  bound_audiences="https://kubernetes.default.svc" \
  bound_subject="system:serviceaccount:default:opa-mcp-authz" \
  claim_mappings="/kubernetes.io/namespace=namespace" \
  token_explicit_max_ttl=0 \
  token_period=1800 \
  token_policies="default,opa-mcp-authz" \
  token_type="service" \
  user_claim="sub" \
  user_claim_json_pointer=true
```

### (b) Terraform

Copy the resources from `vault/policies.hcl` (section (b)) into
`infra/modules/consul-client-k8s/vault.tf`, then `terraform apply`.

## Step 3 — Seed the catalog in Vault

Reproduces today's `tool_catalog` exactly, so behaviour after cutover
matches behaviour before.

```bash
./opa-mcp-auth/vault/seed-catalog.sh
```

Or inline (pipe JSON via stdin — the trailing `-` is required so Vault
parses the payload as typed JSON; the `key=value` form would stringify
the whole `rules` object and break the catalog lookup):

```bash
echo '{"rules":{"default/ai-agent":{"default/user-mcp":{"allow":["list_all_users","search_users_by_first_name","update_user_by_email"]}}}}' \
  | vault kv put opa-policies/mcp-authz/catalog -
```

Verify:

```bash
vault kv get opa-policies/mcp-authz/catalog
vault kv metadata get opa-policies/mcp-authz/catalog | grep current_version
```

## Step 4 — Deploy the policy ConfigMap + OPA Deployment

The policy `.rego` is delivered through the ConfigMap, just like today.
Only the *data* moves to Vault.

```bash
# Create / refresh the policy ConfigMap (idempotent).
kubectl create configmap opa-mcp-authz-policy \
  --from-file=mcp_authz.rego=opa-mcp-auth/policy/mcp/authz/mcp_authz.rego \
  --dry-run=client -o yaml | kubectl apply -f -

# Deploy OPA with the vault-agent sidecar.
kubectl apply -f deploy-k8s/opa-mcp-authz.yaml
kubectl rollout status deploy/opa-mcp-authz
```

Confirm both containers are healthy and that vault-agent successfully
rendered the catalog:

```bash
kubectl get pods -l app=opa-mcp-authz
kubectl logs deploy/opa-mcp-authz -c vault-agent --tail=30 | grep -i 'rendered\|error'
kubectl logs deploy/opa-mcp-authz -c opa --tail=30
```

If vault-agent reports auth or template errors, check that step 2's role
exists and binds `system:serviceaccount:default:opa-mcp-authz`.

## Step 5 — Wire user-mcp's Envoy ext_authz at this OPA (if not already)

`deploy-k8s/service-defaults-user-mcp.yaml` installs a Consul
`ServiceDefaults` with the `builtin/ext-authz` Envoy extension on
user-mcp's inbound sidecar, pointing at the `opa-mcp-authz` gRPC
service (`failureModeAllow: false`, `statusOnError: 403`). The
companion `user-mcp` block in `deploy-k8s/service-intentions.yaml`
allows `ai-agent` and `consul-mcp-authz` as sources.

If both are already applied, this step is a no-op. Otherwise:

```bash
kubectl apply -f deploy-k8s/service-defaults-user-mcp.yaml
kubectl apply -f deploy-k8s/service-intentions.yaml
kubectl rollout restart deploy/user-mcp
```

## Step 6 — Manual test: baseline parity with today

Run inside the `ai-agent` pod so the request flows through Consul mTLS
and the user-mcp sidecar's ext_authz filter.

> Two MCP protocol details to know before reading the expected codes:
>
> 1. `Accept: application/json, text/event-stream` is required by the MCP
>    streamable-HTTP transport — the server returns **406** without it.
> 2. A bare `tools/call` from curl will get **400** with `Mcp-Session-Id`
>    set, because MCP requires `initialize` first to establish a session.
>    The 400 comes from `user-mcp` itself *after* ext_authz has allowed
>    the request through — it is **not** an authz failure.
>
> What the pilot is proving is that **OPA's ext_authz filter is the gate**,
> so the success criterion is whether OPA *stopped* the request or *let
> it through to the upstream app*, not the final upstream status code.
> The cleanest signal is the `x-authz-reason` response header: present
> on denials, absent on allows.

```bash
# Allowed tool — OPA lets it through; upstream (user-mcp) replies with
# its own MCP protocol error (typically 400). The key signal is the
# ABSENCE of an x-authz-reason header.
kubectl exec deploy/ai-agent -c ai-agent -- \
  curl -sS -i -X POST http://user-mcp.virtual.consul/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_all_users","arguments":{}}}' \
  | grep -iE '^HTTP/|^x-authz-reason|^mcp-session-id' || true
# Expected: HTTP/1.1 400 (or 200 if you do a full MCP handshake);
#           no x-authz-reason header; mcp-session-id IS set.

# Denied tool — OPA blocks at the sidecar; never reaches user-mcp.
kubectl exec deploy/ai-agent -c ai-agent -- \
  curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST http://user-mcp.virtual.consul/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"create_user","arguments":{"email":"x@y.z","first_name":"X"}}}'
# Expected: 403
```

The 403 response carries an `x-authz-reason` header explaining the
decision. Inspect it with `-i` instead of `-o /dev/null`:

```bash
kubectl exec deploy/ai-agent -c ai-agent -- \
  curl -sS -i -X POST http://user-mcp.virtual.consul/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"create_user","arguments":{}}}' \
  | grep -i x-authz-reason
# Expected:
# x-authz-reason: agent=default/ai-agent dest=default/user-mcp method=tools/call tool=create_user not permitted
```

## Step 7 — Manual test: live reload without pod restart

The headline demo. Flip a denied tool to allowed by writing a new Vault
KV version, then re-curl from the ai-agent pod. **No `kubectl rollout`,
no `kubectl restart`.**

```bash
# 1. Update the catalog: add create_user to the allow list.
#    Pipe JSON via stdin (trailing `-`) so Vault stores `rules` as an
#    object — `vault kv put PATH rules='...'` would store it as a string
#    and the policy's `catalog[agent][dest]` lookup would silently fail.
echo '{"rules":{"default/ai-agent":{"default/user-mcp":{"allow":["list_all_users","search_users_by_first_name","update_user_by_email","create_user"]}}}}' \
  | vault kv put opa-policies/mcp-authz/catalog -

# 2. Watch vault-agent re-render and OPA --watch reload (in a 2nd terminal).
kubectl logs -f deploy/opa-mcp-authz -c vault-agent --tail=5 &
kubectl logs -f deploy/opa-mcp-authz -c opa --tail=5 &

# 3. Give it a few seconds for vault-agent + fsnotify + OPA reload.
sleep 3

# 4. Re-curl create_user — was 403 (with x-authz-reason) before; now
#    OPA lets it through, so the upstream MCP handshake error (400) is
#    what you see and x-authz-reason is GONE. That gone-ness is the
#    live-reload landing — no pod restart involved.
kubectl exec deploy/ai-agent -c ai-agent -- \
  curl -sS -i -X POST http://user-mcp.virtual.consul/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"create_user","arguments":{"email":"new@example.com","first_name":"New"}}}' \
  | grep -iE '^HTTP/|^x-authz-reason|^mcp-session-id' || true
# Expected: HTTP/1.1 400; no x-authz-reason; mcp-session-id IS set.
```

Then roll back:

```bash
echo '{"rules":{"default/ai-agent":{"default/user-mcp":{"allow":["list_all_users","search_users_by_first_name","update_user_by_email"]}}}}' \
  | vault kv put opa-policies/mcp-authz/catalog -

sleep 3

# Same curl as step 6 (denied tool); expect 403 again.
kubectl exec deploy/ai-agent -c ai-agent -- \
  curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST http://user-mcp.virtual.consul/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"create_user","arguments":{}}}'
```

If steps 6 and 7 both behave as described, the pilot is complete.

## Step 8 — Inspect OPA decision logs (optional)

```bash
kubectl logs -f deploy/opa-mcp-authz -c opa | \
  jq 'select(.type=="openpolicyagent.org/decision_logs") | {
    result,
    method:    .input.attributes.request.http.body,
    src:       .input.attributes.metadataContext.filterMetadata.consul,
    dest_uri:  .input.attributes.destination.principal
  }'
```

## Vault KV v2 versioning & rollback

Every `vault kv put` produces a new version with built-in audit.

```bash
# List versions and timestamps.
vault kv metadata get opa-policies/mcp-authz/catalog

# Read a specific historical version.
vault kv get -version=2 opa-policies/mcp-authz/catalog

# Rollback = write that version's payload back as a new current.
vault kv rollback -version=2 opa-policies/mcp-authz/catalog
```

The vault-agent sidecar treats every new version as a re-render, so
rollback also propagates within seconds.

## Failure modes & defaults (deliberate)

| Scenario | Behaviour | Why |
|----------|-----------|-----|
| Vault unreachable at OPA boot | vault-agent init blocks → OPA never starts → readiness red → Consul drops the endpoint → user-mcp's `failureModeAllow: false` returns 403 to every request. | Fail-closed. |
| Vault unreachable at steady state | Last-rendered `data.json` stays on disk. OPA keeps deciding against it. | Continuity over momentary correctness. |
| Vault Agent template render error | Agent logs error and keeps the previous file. | Same as above. |
| `data.json` missing entirely (no rule ever written) | `default catalog := {}` → `known_pair` false for every input → default-deny. | Fail-closed. |
| Malformed JSON on disk | OPA --watch rejects on parse, keeps previous in-memory data, logs the error. | OPA is conservative. |

## Cleanup

```bash
kubectl delete -f deploy-k8s/opa-mcp-authz.yaml
kubectl delete configmap opa-mcp-authz-policy
vault kv metadata delete opa-policies/mcp-authz/catalog
vault delete auth/k8s_jwt/role/opa-mcp-authz
vault policy delete opa-mcp-authz
```

## What's next (post-pilot)

This pilot proves the storage + reload backbone. The follow-on phases
are now built — see [`consul-mcp-authz/`](../consul-mcp-authz):

- **P1** — `consul-mcp-authz/api/`: FastAPI service that CRUDs the
  catalog in Vault, validates with `opa eval` before each write, and
  exposes history/rollback. *Built; OIDC operator auth deferred to
  P1.5.*
- **P2** — `consul-mcp-authz/ui/`: Next.js + IBM Carbon operator
  console with a Rules list, View/Edit, New Rule, History + rollback,
  and live tool discovery against deployed MCP servers' `tools/list`.
  *Pilot built; OIDC login + admin-only rollback gating blocked on P1.5.*

Both reuse the data model and reload mechanism proven here without
changes.
