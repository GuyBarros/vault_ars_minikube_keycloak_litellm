# Deploy Kubernetes

Lab local: `make up` / `make deploy` em [`infra/local-minikube/`](../infra/local-minikube/). Papéis e arquivos: [`documentation/arquitetura-detalhada.md`](../documentation/arquitetura-detalhada.md).

Este README é o apply **manual** (AWS/EKS ou debug). No minikube **não** ligue `ext_authz` no `user-mcp`: o catálogo é enforce no LiteLLM → OPA `mcp.pep`. `service-defaults-user-mcp.yaml` só tem timeout HTTP.

## Deploy Consul configurations
```
kubectl apply -f deploy-k8s/proxy-defaults.yaml
kubectl apply -f deploy-k8s/mesh.yaml
```

> `mesh.yaml` sets `allowEnablingPermissiveMutualTLS: true`. That only *allows* a service to opt in to
> `MutualTLSMode: permissive`; nothing here enables it. The local minikube flow uses it for Vault (see
> `infra/local-minikube/mesh-vault-postgres.yaml`, which also puts Vault and Postgres in the mesh).

## Deploy token-exchange service
```
kubectl create secret generic token-exchange-env \
  --from-file=.env=deploy-k8s/token-exchange.env
kubectl apply -f deploy-k8s/token-exchange.yaml
```

## Deploy user-mcp service

> **Minikube:** user-mcp runs *inside* the cluster, so it must reach Vault and
> Postgres over in-cluster service DNS — not the external load balancer (the LB
> security group only admits the Terraform caller's /32, and pod traffic arrives
> from the VM's public IP). Before creating the secret, set these two vars in
> `deploy-k8s/user-mcp.env`:
>
> - `USER_MCP_VAULT_ADDR=https://vault.vault.svc:8200`
> - `USER_MCP_PG_URL="postgresql://postgres.default.svc.cluster.local:5432/users?sslmode=disable"`
>
> Leaving them at the external/EKS hostnames fails with `ConnectError` (Vault) or
> `gaierror: Name or service not known` (Postgres).

```
kubectl create secret generic user-mcp-env \
  --from-file=.env=deploy-k8s/user-mcp.env
kubectl apply -f deploy-k8s/user-mcp.yaml
```

## user-mcp ServiceDefaults (timeouts; sem ext_authz)

No lab o inbound de `user-mcp` **não** usa `builtin/ext-authz`. O YAML só define `protocol: http` e timeout longo (CIBA). Catálogo: LiteLLM `pdp_mcp.py` + opa-server.

```
kubectl apply -f deploy-k8s/service-defaults-user-mcp.yaml
```

O pod `opa-mcp-authz` ainda sobe para o `consul-mcp-authz` editar o KV `opa-policies/mcp-authz/catalog`.

## Deploy ai-agent app
```
kubectl create secret generic ai-agent-env \
  --from-file=.env=deploy-k8s/ai-agent.env
kubectl apply -f deploy-k8s/ai-agent.yaml
kubectl apply -f deploy-k8s/ai-agent-config.yaml
```

## Deploy web-app
```
kubectl create secret generic web-env \
  --from-file=.env=deploy-k8s/web-app.env
kubectl apply -f deploy-k8s/web-app.yaml
```

## Deploy OPA server (if testing OPA)
```
kubectl create ns opa
kubectl apply -n opa -f deploy-k8s/opa-server.yaml

##Test policies are loaded
kubectl port-forward -n opa svc/opa-service 8081:80
curl -s http://localhost:8081/v1/policies\?pretty\=true | jq -r '.result[] | [.id, .raw] | join("\n")'

#kubectl apply -f deploy-k8s/service-defaults-agent-opa.yaml 
```

## Deploy opa-gov-api (if testing OPA)
```
kubectl apply -f deploy-k8s/opa-gov-api.yaml                           
kubectl apply -f deploy-k8s/service-defaults-agent-opa-gov.yaml 
```

## Deploy wx-gov-api (if testing watsonx-governance)
```
kubectl create secret generic wx-gov-api-env --from-env-file=deploy-k8s/wx-gov-api.env
kubectl apply -f deploy-k8s/wx-gov-api.yaml

kubectl apply -f deploy-k8s/service-defaults-agent-wx-gov.yaml 
```

## Deploy opa-mcp-authz pilot (data-driven MCP authz)

Prereqs: Vault KV v2 mount `opa-policies`, JWT auth at `auth/k8s_jwt`,
the `opa-mcp-authz` Vault policy + role, and a seeded catalog at
`opa-policies/mcp-authz/catalog`. See
[`opa-mcp-auth/README.md`](../opa-mcp-auth/README.md) steps 1–3.

```
# Policy ConfigMap (the .rego itself ships from source; only the catalog
# data lives in Vault).
kubectl create configmap opa-mcp-authz-policy \
  --from-file=mcp_authz.rego=opa-mcp-auth/policy/mcp/authz/mcp_authz.rego \
  --dry-run=client -o yaml | kubectl apply -f -

# OPA Deployment + Service with vault-agent sidecar and `--watch`.
kubectl apply -f deploy-k8s/opa-mcp-authz.yaml
kubectl rollout status deploy/opa-mcp-authz
```


## Deploy consul-mcp-authz (catalog API + operator UI)
```
kubectl apply -f deploy-k8s/consul-mcp-authz.yaml
kubectl rollout status deploy/consul-mcp-authz

# UI is not in transparent-proxy scope; reach it via port-forward.
kubectl port-forward svc/consul-mcp-authz 8502:8502
# Then open http://localhost:8502
```

## Configure Consul API Gateway
```
kubectl apply -f deploy-k8s/web-app-gateway.yaml
```

## Configure service-intentions
```
kubectl apply -f deploy-k8s/service-intentions.yaml
```

## Delete resources
```
kubectl delete secret token-exchange-env
kubectl delete -f deploy-k8s/token-exchange.yaml

kubectl delete secret user-mcp-env
kubectl delete -f deploy-k8s/service-defaults-user-mcp.yaml
kubectl delete -f deploy-k8s/user-mcp.yaml

kubectl delete secret ai-agent-env
kubectl delete -f deploy-k8s/ai-agent.yaml

kubectl delete secret web-env 
kubectl delete -f deploy-k8s/web-app.yaml

kubectl delete configmap opa-mcp-authz-policy 
kubectl delete -f deploy-k8s/opa-mcp-authz.yaml

kubectl delete -f deploy-k8s/consul-mcp-authz.yaml

kubectl delete -f deploy-k8s/service-defaults-agent-opa.yaml 
kubectl delete -n opa -f deploy-k8s/opa-server.yaml
kubectl delete ns opa

kubectl delete -f deploy-k8s/opa-gov-api.yaml                                 
kubectl delete -f deploy-k8s/service-defaults-agent-opa-gov.yaml 

kubectl delete secret wx-gov-api-env
kubectl delete -f deploy-k8s/service-defaults-agent-wx-gov.yaml 
kubectl delete -f deploy-k8s/wx-gov-api.yaml

kubectl delete -f deploy-k8s/web-app-gateway.yaml

kubectl delete -f deploy-k8s/service-intentions.yaml
kubectl delete -f deploy-k8s/mesh.yaml
kubectl delete -f deploy-k8s/proxy-defaults.yaml
```