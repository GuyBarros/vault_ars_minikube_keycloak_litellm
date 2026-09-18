# Local minikube (no AWS)

Guia de configuração (env, Keycloak, Vault CIBA, LiteLLM): [`../../documentation/Guia_Configuracao.md`](../../documentation/Guia_Configuracao.md).

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

Uses a dedicated minikube profile (`local-minikube-demo`, podman driver — this
machine's `docker` CLI is a shim in front of Podman) so it doesn't touch any other
local minikube profile. Generated TLS material, tokens, and rendered values land in
`./generated/` (git-ignored).

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
LANGCHAIN_MODEL=ollama:qwen2.5:7b
OLLAMA_BASE_URL=http://host.containers.internal:11434
```

`host.containers.internal` is Podman's name for this Mac from inside the
minikube node — confirmed reachable from a pod with `kubectl --context
local-minikube-demo run nettest --image=busybox --rm -it --restart=Never --
wget -qO- http://host.containers.internal:11434/api/tags`. Pull the model on
the host first (`ollama pull qwen2.5:7b`) — the agent doesn't do this itself.
`langchain-ollama` is a real dependency (`ai-agent/pyproject.toml`), so rebuild
the image (`make images`) after changing the model.

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
