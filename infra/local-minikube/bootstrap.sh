#!/bin/bash
# Local analog of modules/minikube-allinone (bootstrap.sh.tftpl) + the Vault
# init/unseal step from modules/minikube-allinone/main.tf, run directly on
# this machine instead of on a remote EC2 instance. No AWS resources, no ALB,
# no socat bridges — minikube runs right here, so kubectl/helm reach it
# directly.
#
# Brings up: minikube (docker driver) -> Consul Enterprise + Vault Enterprise
# (Helm, TLS, ACLs) -> Vault init/unseal. Reuses the same Helm values
# templates as the AWS module (rendered with envsubst instead of Terraform).
#
# Does NOT configure the MCP-authz control plane (Vault JWT auth / DB secrets
# engine / Postgres / Consul ACL token) that modules/minikube-resources-config
# layers on top in the AWS flow — that is a separate, not-yet-built step.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
TEMPLATES_DIR="$INFRA_DIR/modules/minikube-allinone/templates"
GEN_DIR="$SCRIPT_DIR/generated"
source "$SCRIPT_DIR/local-config.sh"

PROFILE="local-minikube-demo"
CONSUL_NAMESPACE="consul"
VAULT_NAMESPACE="vault"
DATACENTER="dc1"
K8S_VERSION="v1.30.0"
MINIKUBE_CPUS="4"
MINIKUBE_MEMORY="8g"
CONSUL_CHART_VERSION="1.9.5"
VAULT_CHART_VERSION="0.28.1"
CONSUL_NODE_PORT="31501"
VAULT_NODE_PORT="31200"
WEB_NODE_PORT="30080"
KEYCLOAK_NODE_PORT="30081"
LITELLM_NODE_PORT="30083"
# Kubernetes only allows Service nodePort values in 30000-32767 (the values
# above), so the "original" ports (matching the AWS flow's consul_host_port/
# vault_host_port variables, and the web app's own container port) are
# published as a *different* host-side port mapped onto each NodePort -
# exactly what the AWS module's socat units do (host 8501/8200 -> node
# 31501/31200), just via docker/podman's own port publishing instead.
CONSUL_HOST_PORT="8501"
VAULT_HOST_PORT="8200"
WEB_HOST_PORT="8080"
KEYCLOAK_HOST_PORT="8081"
LITELLM_HOST_PORT="4000"
# Published host<->node ports (docker/podman driver only) so these NodePort
# services are reachable at localhost:<port> from this machine directly, with
# no `kubectl port-forward` needed. Only takes effect at node-creation time -
# `minikube delete -p local-minikube-demo` first if the profile already
# exists without these published.
PUBLISHED_PORTS="${CONSUL_HOST_PORT}:${CONSUL_NODE_PORT},${VAULT_HOST_PORT}:${VAULT_NODE_PORT},${WEB_HOST_PORT}:${WEB_NODE_PORT},${KEYCLOAK_HOST_PORT}:${KEYCLOAK_NODE_PORT},${LITELLM_HOST_PORT}:${LITELLM_NODE_PORT}"

for bin in minikube helm kubectl docker openssl jq envsubst uuidgen; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 1; }
done

CONSUL_LICENSE="$INFRA_DIR/config/consul_license.hclic"
VAULT_LICENSE="$INFRA_DIR/config/vault_license.hclic"
[ -f "$CONSUL_LICENSE" ] || { echo "missing $CONSUL_LICENSE" >&2; exit 1; }
[ -f "$VAULT_LICENSE" ] || { echo "missing $VAULT_LICENSE" >&2; exit 1; }

mkdir -p "$GEN_DIR"
KC() { kubectl --context "$PROFILE" "$@"; }
HELM() { helm --kube-context "$PROFILE" "$@"; }

echo "=== 1. minikube ($PROFILE) ==="
if ! minikube status -p "$PROFILE" >/dev/null 2>&1; then
  if [ -n "${MINIKUBE_DRIVER:-}" ]; then
    # Explicit override (env var or local-config.sh): skip auto-detection.
    DRIVER="$MINIKUBE_DRIVER"
  else
    # Prefer a real Docker engine (Colima / Docker Desktop). Fall back to
    # Podman when `docker` is only a Podman shim (minikube's Docker preflight
    # rejects that).
    DRIVER=docker
    if ! docker info >/dev/null 2>&1; then
      DRIVER=podman
    elif docker version -f '{{.Server.Platform.Name}}' 2>/dev/null | grep -qi podman; then
      DRIVER=podman
    fi
  fi
  echo "minikube driver: $DRIVER"
  minikube start -p "$PROFILE" \
    --driver="$DRIVER" \
    --cpus="$MINIKUBE_CPUS" \
    --memory="$MINIKUBE_MEMORY" \
    --kubernetes-version="$K8S_VERSION" \
    --ports="$PUBLISHED_PORTS"
else
  echo "profile $PROFILE already running, skipping start"
fi

echo "=== 2. Vault self-signed TLS (mirrors modules/minikube-allinone/tls.tf) ==="
if [ ! -f "$GEN_DIR/vault-fullchain.crt" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 1825 -sha256 \
    -keyout "$GEN_DIR/ca.key" -out "$GEN_DIR/ca.crt" \
    -subj "/C=SG/ST=Singapore/L=Singapore/O=Demo Organization/OU=Demo Organization Vault Root Certification Authority/CN=Demo Root CA"

  openssl req -newkey rsa:2048 -nodes \
    -keyout "$GEN_DIR/vault.key" -out "$GEN_DIR/vault.csr" \
    -subj "/C=SG/ST=Singapore/L=Singapore/O=Demo Organization/OU=Development/CN=demo.server.vault"

  openssl x509 -req -in "$GEN_DIR/vault.csr" -CA "$GEN_DIR/ca.crt" -CAkey "$GEN_DIR/ca.key" -CAcreateserial \
    -out "$GEN_DIR/vault.crt" -days 1825 -sha256 \
    -extfile <(printf "subjectAltName=DNS:vault-0.vault-internal,DNS:vault.%s.svc.cluster.local,DNS:demo.server.vault,DNS:localhost,IP:127.0.0.1" "$VAULT_NAMESPACE")

  cat "$GEN_DIR/vault.crt" "$GEN_DIR/ca.crt" > "$GEN_DIR/vault-fullchain.crt"
else
  echo "TLS material already generated, skipping"
fi

echo "=== 3. Render Helm values (reusing modules/minikube-allinone/templates/*.tftpl) ==="
datacenter="$DATACENTER" consul_version="$CONSUL_IMAGE_VERSION" consul_node_port="$CONSUL_NODE_PORT" \
  envsubst '${datacenter} ${consul_version} ${consul_node_port}' \
  < "$TEMPLATES_DIR/consul-server-values.yaml.tftpl" > "$GEN_DIR/consul-values.yaml"

vault_version="$VAULT_IMAGE_VERSION" vault_node_port="$VAULT_NODE_PORT" \
  envsubst '${vault_version} ${vault_node_port}' \
  < "$TEMPLATES_DIR/vault-server-values.yaml.tftpl" > "$GEN_DIR/vault-values.yaml"

echo "=== 4. Consul (Enterprise, TLS + ACLs) ==="
KC create namespace "$CONSUL_NAMESPACE" --dry-run=client -o yaml | KC apply -f -
if [ ! -f "$GEN_DIR/consul_token" ]; then
  uuidgen | tr 'A-Z' 'a-z' > "$GEN_DIR/consul_token"
fi
KC -n "$CONSUL_NAMESPACE" create secret generic consul-ent-license \
  --from-file=key="$CONSUL_LICENSE" --dry-run=client -o yaml | KC apply -f -
KC -n "$CONSUL_NAMESPACE" create secret generic consul-bootstrap-token \
  --from-literal=token="$(cat "$GEN_DIR/consul_token")" --dry-run=client -o yaml | KC apply -f -
KC -n "$CONSUL_NAMESPACE" create secret generic vault-ca \
  --from-file=ca.crt="$GEN_DIR/ca.crt" --dry-run=client -o yaml | KC apply -f -
helm repo add hashicorp https://helm.releases.hashicorp.com >/dev/null 2>&1 || true
helm repo update hashicorp >/dev/null
# prometheus.enabled installs the chart's demo Prometheus, which scrapes the
# Envoy sidecars' latency histograms and backs the Consul UI's service metrics
# (ui.metrics defaults to http://prometheus-server). Set here rather than in
# the shared template so the AWS module is unaffected.
HELM upgrade --install consul hashicorp/consul \
  --namespace "$CONSUL_NAMESPACE" \
  --version "$CONSUL_CHART_VERSION" \
  --values "$GEN_DIR/consul-values.yaml" \
  --set prometheus.enabled=true \
  --wait --timeout 15m

# deploy-k8s manifests (ai-agent, web-app, consul-mcp-authz) all
# address each other via Consul's "<service>.virtual.consul" DNS names, which
# only resolve if cluster DNS forwards the "consul" zone to Consul's own DNS
# service - CoreDNS has no idea about that zone by default. Without this,
# every .virtual.consul lookup NXDOMAINs silently (ai-agent logs it as a
# non-fatal startup warning and just boots with zero MCP tools).
echo "=== 4b. Wire CoreDNS to forward the consul zone to Consul's DNS ==="
CONSUL_DNS_IP=$(KC -n "$CONSUL_NAMESPACE" get svc consul-dns -o jsonpath='{.spec.clusterIP}')
if ! KC -n kube-system get configmap coredns -o jsonpath='{.data.Corefile}' | grep -q '^consul:53'; then
  KC -n kube-system get configmap coredns -o jsonpath='{.data.Corefile}' > "$GEN_DIR/Corefile"
  cat >> "$GEN_DIR/Corefile" <<-EOT
	consul:53 {
	    errors
	    cache 30
	    forward . ${CONSUL_DNS_IP}
	}
	EOT
  KC -n kube-system create configmap coredns --from-file=Corefile="$GEN_DIR/Corefile" \
    --dry-run=client -o yaml | KC apply -f -
  KC -n kube-system rollout restart deployment coredns
  KC -n kube-system rollout status deployment coredns --timeout=60s
else
  echo "CoreDNS already forwards the consul zone, skipping"
fi

echo "=== 5. Vault (Enterprise, raft + TLS) ==="
KC create namespace "$VAULT_NAMESPACE" --dry-run=client -o yaml | KC apply -f -
KC -n "$VAULT_NAMESPACE" create secret generic vault-server-tls \
  --from-file=vault.crt="$GEN_DIR/vault-fullchain.crt" \
  --from-file=vault.key="$GEN_DIR/vault.key" \
  --from-file=vault.ca="$GEN_DIR/ca.crt" \
  --dry-run=client -o yaml | KC apply -f -
KC -n "$VAULT_NAMESPACE" create secret generic vault-ent-license \
  --from-file=license="$VAULT_LICENSE" --dry-run=client -o yaml | KC apply -f -
# Re-running this once vault-0 is up can fail with a field-manager conflict on
# the injector's MutatingWebhookConfiguration caBundle (vault-k8s rewrites that
# field itself at runtime as it rotates its cert) - harmless, the running
# cluster is unaffected; just don't re-run once Vault is already installed.
HELM upgrade --install vault hashicorp/vault \
  --namespace "$VAULT_NAMESPACE" \
  --version "$VAULT_CHART_VERSION" \
  --values "$GEN_DIR/vault-values.yaml" \
  --timeout 15m

echo "waiting for vault-0..."
until KC -n "$VAULT_NAMESPACE" get pod vault-0 >/dev/null 2>&1; do sleep 5; done
KC -n "$VAULT_NAMESPACE" wait --for=jsonpath='{.status.phase}'=Running pod/vault-0 --timeout=600s

echo "=== 6. Vault init/unseal ==="
echo "waiting for vault's HTTP listener to respond..."
# `vault status` exits 2 (not 0) when sealed - which it always is at this
# point, before we've unsealed it - so success here must be judged by
# getting parseable JSON back, not by the exit code.
vault_status=""
for i in $(seq 1 60); do
  candidate=$(KC -n "$VAULT_NAMESPACE" exec -c vault vault-0 -- sh -c 'VAULT_SKIP_VERIFY=true vault status -format=json' 2>/dev/null) || true
  if echo "$candidate" | jq -e . >/dev/null 2>&1; then
    vault_status="$candidate"
    break
  fi
  sleep 3
done
[ -n "$vault_status" ] || { echo "vault never responded to 'vault status'" >&2; exit 1; }

if echo "$vault_status" | jq -e '.initialized == false' >/dev/null; then
  KC -n "$VAULT_NAMESPACE" exec -c vault vault-0 -- sh -c 'VAULT_SKIP_VERIFY=true vault operator init -n 1 -t 1 -format=json' > "$GEN_DIR/vault_init.json"
fi
unseal_key=$(jq -r '.unseal_keys_b64[0]' "$GEN_DIR/vault_init.json")
KC -n "$VAULT_NAMESPACE" exec -c vault vault-0 -- sh -c "VAULT_SKIP_VERIFY=true vault operator unseal $unseal_key" >/dev/null
jq -r '.root_token' "$GEN_DIR/vault_init.json" > "$GEN_DIR/vault_token"

echo
echo "=== done ==="
echo "kube context: $PROFILE"
echo "consul token: $GEN_DIR/consul_token"
echo "vault token:  $GEN_DIR/vault_token"
echo
echo "Consul UI:  kubectl --context $PROFILE port-forward -n consul svc/consul-ui 8501:443"
echo "Vault UI:   kubectl --context $PROFILE port-forward -n vault svc/vault 8200:8200"
echo
echo "NOTE: the MCP-authz control plane (Vault JWT auth, DB secrets engine, Postgres,"
echo "Consul ACL token) from modules/minikube-resources-config is NOT set up yet."
echo "The deploy-k8s app manifests (token-exchange, user-mcp, ai-agent, web-app) depend"
echo "on it and will not work end-to-end until that is ported to run locally too."
