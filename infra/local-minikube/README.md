# Local minikube (no AWS)

Guia de configuração (env, Keycloak, Vault, LiteLLM): [`../../documentation/Guia_Configuracao.md`](../../documentation/Guia_Configuracao.md). Arquitetura: [`../../documentation/arquitetura-detalhada.md`](../../documentation/arquitetura-detalhada.md).

## Quickest path

```bash
make up      # cluster + control plane + local app images + deploy, then verify
make status  # pod status + direct-access check (localhost:8080/8200/8501)
make down    # destroy the minikube node - wipes everything
```

`make up` runs every stage below in order (`bootstrap`, `configure`, `keycloak`,
`images`, `deploy`, `verify` - each also runnable on its own, e.g. `make bootstrap`).
`make redeploy` re-applies just the app manifests + local images, for iterating on
app source or `deploy-k8s/*.yaml` without redoing the cluster or control plane.
`make hop-logs` sobe o viewer SSE em http://127.0.0.1:8753/.
The rest of this file explains what each stage actually does; read on if something
needs debugging or you're doing it by hand.

`keycloak` (`keycloak.sh`) builds the custom Keycloak image (bakes in the
`keycloak-providers/` SPI jar), deploys it, imports the demo realm, and
configures Vault's `jwt-keycloak` auth mount + OAuth Resource Server + Agent
Registry against it — see [`../../KEYCLOAK_REALM_SETUP.md`](../../KEYCLOAK_REALM_SETUP.md).
Needs a Vault Enterprise license with the Agentic IAM entitlement at
`infra/config/vault_license.hclic`, and must run after `configure.sh` (needs the
shared `postgres-0` StatefulSet for Keycloak's own database).

---

`bootstrap.sh` brings up minikube + Consul Enterprise + Vault Enterprise directly on
this machine — the local equivalent of `modules/minikube-allinone` (see
`../README-minikube.md`), minus the EC2 instance, ALB, and socat bridges: kubectl/helm
just talk to minikube directly.

Reuses the same license files (`infra/config/{consul,vault}_license.hclic`) and Helm
values templates (`modules/minikube-allinone/templates/*.tftpl`, rendered with
`envsubst` instead of Terraform) as the AWS flow, so the two stay in sync.

```bash
./bootstrap.sh
```

Uses a dedicated minikube profile (`local-minikube-demo`, **docker** driver — Colima neste Mac). Não use `host.containers.internal` (só Podman). O alias do host para pods é `host.minikube.internal` / `host.docker.internal`. Generated TLS material, tokens, and rendered values land in `./generated/` (git-ignored).

```bash
kubectl --context local-minikube-demo get pods -A
```

**No port-forward needed for Vault/Consul.** `minikube start --ports` (docker/podman
driver only) publishes their NodePorts straight to this machine, so once `bootstrap.sh`
has run they're just there:

```bash
curl -sk https://localhost:31200/v1/sys/health   # Vault
curl -sk https://localhost:31501/v1/status/leader   # Consul
```

This only takes effect when the node is *created* — if `local-minikube-demo` already
exists from before this was added, `minikube delete -p local-minikube-demo` first and
re-run `bootstrap.sh` (you'll need to redo `configure.sh` and the app deploy below too).

## MCP-authz control plane

`configure.sh` is the local analog of `modules/minikube-resources-config`: Postgres
(seeded `users` table), the `k8s_jwt` Vault auth backend (validated against minikube's
own SA signing key instead of an EKS OIDC endpoint), the OPA policy bundle, Vault's
identity OIDC issuer/role, the `database` secrets engine (dynamic Postgres creds), and
the Consul ACL token for consul-mcp-authz. The Keycloak-facing Vault config (jwt-keycloak,
OAuth Resource Server, Agent Registry) is a separate stage — see `keycloak.sh` above.
Run `configure.sh` after `bootstrap.sh`:

```bash
./configure.sh
```

Drives the real `vault`/`consul` CLIs against a port-forward instead of going over SSH
through an ALB. Re-running is safe (each step checks before creating).

## Service mesh: Vault as the CA, Vault and Postgres as mesh services

Three things in `bootstrap.sh` / `configure.sh` shape the mesh beyond a stock Consul install.

**Vault is the mesh (Connect) CA** (`configure.sh` step 8). Consul is installed before Vault
exists, so it starts on its built-in CA; once Vault is unsealed, `configure.sh` writes a
`consul-connect-ca` Vault policy, creates a periodic token for it, and runs
`consul connect ca set-config` with the `vault` provider. Consul creates and populates the
`connect_root` / `connect_inter` PKI mounts itself and cross-signs the new root, so running
proxies keep working. Consul verifies Vault's TLS with the CA that `bootstrap.sh` puts in the
`vault-ca` secret and the Helm values mount at `/consul/userconfig/vault-ca/ca.crt`. Check with
`consul connect ca get-config | jq -r .Provider` (should print `vault`). Consul's own
server-RPC TLS CA is separate and unaffected.

**Vault and Postgres are mesh services** (`configure.sh` step 1b, before Postgres is deployed,
because Vault's database secrets engine connects to Postgres through the mesh during
`configure.sh`). `mesh-vault-postgres.yaml` holds their `ServiceDefaults` and
`ServiceIntentions`; `deploy-k8s/mesh.yaml` carries `allowEnablingPermissiveMutualTLS`.

- Both are `protocol: tcp`. The mesh-wide default is `http`, which would make Envoy try to
  parse Postgres's wire protocol and Vault's TLS stream.
- **Postgres** is strict mTLS. Allowed sources: `vault` (vault namespace), `keycloak`,
  `user-mcp`, `litellm-gateway`.
- **Vault** is `mutualTLSMode: permissive`, because the Consul servers (CA operations), the host
  NodePort/port-forward and kubelet probes all reach it from outside the mesh. Mesh clients still
  use mTLS and need an intention: `ai-agent`, `user-mcp`, `consul-mcp-authz`, `opa-service`,
  `opa-mcp-authz`. A new Vault client with a sidecar needs adding there.
- Consul requires the ServiceAccount name to equal the service name (ACLs on); the Vault chart
  also creates `vault-internal` / `-active` / `-standby` / `-ui` Services on the same pod, so the
  pod carries `consul.hashicorp.com/kubernetes-service: "vault"` to register only `vault`.
- **`kubectl exec` needs `-c`.** Consul puts `consul-dataplane` first in each meshed pod, so an
  exec without `-c` lands in a container with no shell. The scripts use `-c vault` /
  `-c postgres`; do the same by hand.
- `vault-0` shows `1/2` Ready until Vault is initialised and unsealed (the chart's readiness probe is
  `vault status`); that is expected.
- **Sealed Vault:** because Vault is also the CA, a restarted (sealed) Vault cannot get new leaf
  certs issued until it is unsealed by hand. Leaf certs already issued keep working for their TTL.

**LiteLLM identifies its callers by mesh identity, not a shared key.** The `litellm-gateway`
`ServiceDefaults` in `mesh-timeouts.yaml` carries a `builtin/lua` Envoy extension that copies the
verified mTLS peer's SPIFFE ID into `x-mesh-caller-spiffe` (removing any client-supplied copy first).
`litellm-gateway/pdp_auth.py` admits only `default/ai-agent` and `default/web` on that basis;
everything else (including `litellm-api-gateway`) falls through to LiteLLM's own auth. The header is
only trustworthy while that extension is applied. See `../../documentation/Guia_Configuracao.md`.

`make deploy` also deploys the OPA content PDP (`opa` namespace, `opa-server`, `opa-gov-api`) and points
LiteLLM's guardrail at `http://opa-gov-api.virtual.consul:8000`; `make images` builds the `opa-gov-api` image
locally because the Docker Hub one is amd64-only. It creates the `opa` namespace *before* applying
`service-intentions.yaml`, which carries `opa-service`'s intention.

`make deploy` waits for the `web-api-gateway` and `litellm-api-gateway`
Services (Consul creates them asynchronously) before patching their NodePorts.

## Deploying the demo apps

Once both scripts above have run, follow `../../deploy-k8s/README.md` — the Consul
config, `token-exchange`, `user-mcp`, `ai-agent`, `web-app` steps all apply as-is
against `--context local-minikube-demo`.

**One thing to check first:** `deploy-k8s/*.env` are per-machine (git-ignored), and
`token-exchange.env`'s `VAULT_ADDR` in particular needs to point at this local Vault
(`https://vault.vault.svc.cluster.local:8200`) rather than whatever host the AWS flow
used.

**Apple Silicon / arm64:** `panchalravi/agentguard-{user-mcp,ai-agent,web-app}:latest`
on Docker Hub are amd64-only (only `agentguard-token-exchange` publishes an arm64
build), so they won't run on an arm64 minikube node. Build local images from the
source in this repo instead and point the live deployments at them (no need to edit
the tracked manifests):

```bash
docker build --load -t agentguard-user-mcp:local ./user-mcp
docker build --load -t agentguard-ai-agent:local ./ai-agent
docker build --load -t agentguard-web-app:local ./web-app
for img in agentguard-user-mcp:local agentguard-ai-agent:local agentguard-web-app:local; do
  minikube -p local-minikube-demo image load "$img"
done
kubectl --context local-minikube-demo set image deployment/user-mcp user-mcp=agentguard-user-mcp:local
kubectl --context local-minikube-demo set image deployment/ai-agent ai-agent=agentguard-ai-agent:local
kubectl --context local-minikube-demo set image deployment/web web=agentguard-web-app:local
# the manifests hard-code imagePullPolicy: Always, which would otherwise try (and
# fail) to pull these from Docker Hub instead of using the loaded local image:
for d in user-mcp ai-agent web; do
  kubectl --context local-minikube-demo patch deployment "$d" -n default --type=json \
    -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Never"}]'
done
```

`user-mcp/Dockerfile` also expects a `users_repository.json` seed file (git-ignored,
only read by the unused `file` storage backend — this deploy uses
`USER_BACKEND=postgres`); copy any placeholder there before building, e.g.
`infra/modules/minikube-resources-config/users_seed.json`.

## Using a local model (Ollama) for ai-agent

`ai-agent` reads `LANGCHAIN_MODEL` and routes it through LangChain's
`init_chat_model`, so pointing it at a model already running in
[Ollama](https://ollama.com) on this machine needs no code change — just
`deploy-k8s/ai-agent.env` (git-ignored, per-machine):

```bash
LANGCHAIN_MODEL=openai:qwen-local
LITELLM_BASE_URL=http://litellm-gateway.virtual.consul:4000
OLLAMA_BASE_URL=http://host.minikube.internal:11434
USER_MCP_URL=http://litellm-gateway.virtual.consul:4000/user_mcp/mcp
```

Ollama no host tem de escutar `0.0.0.0:11434` (LaunchAgent `local.ollama`). `brew services start ollama` bind em `127.0.0.1` e os pods não alcançam. `host.containers.internal` é só Podman. Pull: `ollama pull qwen2.5:7b`.

**No port-forward needed for the web app either**, but it takes one more step than
Vault/Consul: `deploy-k8s/service-intentions.yaml` only allows the `web-api-gateway`
service to call `web` — anything else gets an "empty reply from server" from Envoy's
inbound listener (`kubectl port-forward` doesn't hit this, since it connects over
loopback inside the pod, bypassing the mesh entirely). So route through the actual
mesh-authorized front door, the Consul API Gateway, instead of hitting `web` directly:

```bash
kubectl --context local-minikube-demo apply -f ../../deploy-k8s/web-app-gateway.yaml
kubectl --context local-minikube-demo apply -f web-nodeport.yaml   # pins its auto-created Service to :30080
curl -s http://localhost:30080/   # 200, Carbon-styled HTML
```

Verify everything:

```bash
kubectl --context local-minikube-demo get pods -n default   # all Running
ps aux | grep port-forward   # nothing - direct access only
```
