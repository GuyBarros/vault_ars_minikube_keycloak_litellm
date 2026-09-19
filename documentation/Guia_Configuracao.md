# Guia de configuração — ai-iam-guardrails

Como configurar o laboratório (minikube local). Os valores abaixo são os que o código e o `make up` realmente usam. Segredos nunca entram no git: `deploy-k8s/*.env` e `infra/local-minikube/generated/` estão no `.gitignore`.

Detalhe por serviço: READMEs em `web-app/`, `ai-agent/`, `user-mcp/`, `token-exchange/`. Realm Keycloak: [`KEYCLOAK_REALM_SETUP.md`](../KEYCLOAK_REALM_SETUP.md). Deploy Kubernetes: [`deploy-k8s/README.md`](../deploy-k8s/README.md).

---

## 1. Pré-requisitos

| Item | Uso |
| --- | --- |
| minikube, kubectl, Helm | cluster `local-minikube-demo` (4 CPU / 8 GB) |
| Docker ou Podman | imagens locais + driver do minikube |
| CLIs `vault` e `consul` | `configure.sh` / `keycloak.sh` |
| `psql`, `jq`, `envsubst`, `openssl` | seed Postgres e render de templates |
| [Ollama](https://ollama.com) (opcional) | modelo local via LiteLLM |
| Licença Consul Enterprise | `infra/config/consul_license.hclic` |
| Licença Vault Enterprise **com Agentic IAM** | `infra/config/vault_license.hclic` |

Sem a entitlement Agentic IAM, `sys/config/oauth-resource-server` falha e o Vault não aceita o JWT OBO/CIBA como `X-Vault-Token` em `database/creds`.

As configurações locais de versão, Postgres e claims OIDC ficam em `infra/local-minikube/local-config.sh` e são carregadas pelos scripts do minikube.

---

## 2. Subir o ambiente

```bash
cd infra/local-minikube
make up       # bootstrap → configure → keycloak → images → deploy → verify
make status   # pods + HTTP nas portas publicadas
make redeploy # só imagens + manifests (iteração de código)
make down     # apaga o profile minikube (tudo)
```

Estágios isolados: `make bootstrap`, `make configure`, `make keycloak`, `make images`, `make deploy`.

`make keycloak` gera os client secrets, importa o realm `demo` e **escreve** os secrets em `deploy-k8s/token-exchange.env`, `user-mcp.env` e `web-app.env`. Não edite esses valores à mão se for reexecutar o script.

---

## 3. URLs e usuários

| Superfície | URL | Quem acessa |
| --- | --- | --- |
| Canal (web-app) | http://localhost:8080 | browser — **só** via Consul API Gateway (`web-api-gateway`) |
| Keycloak (IdP) | http://localhost:8081 | browser + discovery OIDC |
| Canal CIBA | http://localhost:8082 | Approve / Deny de `create_user` / `delete_user_by_email` |
| LiteLLM admin | http://localhost:4000/ui | browser — via Consul API Gateway (`litellm-api-gateway`). SSO with the Keycloak demo logins below, or password `admin`/`admin` |
| Vault | https://localhost:8200 | CLI (`curl -sk`) |
| Consul UI | https://localhost:8501 | intentions / mesh |

Logins do realm `demo`:

| Usuário | Senha | Grupos | O que pode |
| --- | --- | --- | --- |
| `user` | `user` | `reader` | `users.read` (list/search). Sem `users.write`. PII mascarado. |
| `writer` | `writer` | `writer` | `users.read` + `users.write`. Create/delete exigem CIBA. PII mascarado. |
| `admin` | `admin` | `admin` | leitura + escrita, PII em claro. Create/delete exigem CIBA. |

Admin do Keycloak (console): `admin` + senha em `infra/local-minikube/generated/keycloak_admin_password`.

`kubectl port-forward` no pod `web` **não** substitui o gateway: as Service Intentions só autorizam `web-api-gateway → web`. Sem o gateway a resposta é “empty reply from server”.

---

## 4. Onde mora cada configuração

| Camada | Arquivo / objeto | Quem aplica |
| --- | --- | --- |
| Licenças | `infra/config/{consul,vault}_license.hclic` | `bootstrap.sh` |
| Versões, senha Postgres e claims do agente | `infra/local-minikube/local-config.sh` | `bootstrap.sh` / `configure.sh` |
| Realm, clients, users | `infra/local-minikube/templates/keycloak-realm.json` | `keycloak.sh` (`--import-realm`) |
| Secrets de client OIDC | `infra/local-minikube/generated/keycloak_client_secret_*` | `keycloak.sh` → upsert nos `.env` |
| Env dos apps | `deploy-k8s/{web-app,ai-agent,user-mcp,token-exchange}.env` | Secret Kubernetes (`*-env`) |
| Rotas LLM | `litellm-gateway/config.yaml` | ConfigMap `litellm-gateway-config` |
| Intentions (PEP de rede) | `deploy-k8s/service-intentions.yaml` | `make deploy` |
| Timeouts CIBA na malha | `infra/local-minikube/mesh-timeouts.yaml` | aplicado **depois** do HTTPRoute do web |
| Identidade do chamador no LiteLLM | extensão `builtin/lua` no `ServiceDefaults` `litellm-gateway` (`mesh-timeouts.yaml`) | `make deploy` |
| Vault e Postgres na malha | `infra/local-minikube/mesh-vault-postgres.yaml` + `deploy-k8s/mesh.yaml` | `configure.sh` (passo 1b) |
| CA da malha (Connect) | Vault PKI `connect_root` / `connect_inter`; config via `consul connect ca set-config` | `configure.sh` (passo 8) |
| LoA / HITL | policies Vault `ciba-list-users` e `ciba-write` | `keycloak.sh` |
| Catálogo MCP (agente → tool) | Vault KV `opa-policies/mcp-authz/catalog` | `configure.sh` + `opa-mcp-authz` |

Não commitar `*.env`. Depois de mudar um `.env`:

```bash
kubectl --context local-minikube-demo create secret generic user-mcp-env \
  --from-file=.env=deploy-k8s/user-mcp.env --dry-run=client -o yaml | kubectl apply -f -
kubectl --context local-minikube-demo rollout restart deploy/user-mcp
```

O mesmo padrão vale para `web-env`, `ai-agent-env`, `token-exchange-env`.

---

## 5. Keycloak

Clients do realm `demo`:

| Client | Uso |
| --- | --- |
| `web` | Authorization Code + PKCE (browser) |
| `token-exchange` | RFC 8693 OBO (confidential) |
| `ciba-client` | dispara CIBA quando a ACL Vault exige HITL |
| `user-mcp` | audience dos tokens OBO/CIBA |

O mapper SPI `keycloak-providers/` (imagem custom) é obrigatório: remove `typ` do payload, injeta `act` a partir do actor token Vault e sintetiza `authorization_details` (RAR) a partir de `users.read` / `users.write`. Sem isso o OAuth Resource Server do Vault recusa o JWT.

`KEYCLOAK_BASE_URL` (web-app) tem de ser a URL **do browser** (`http://localhost:8081`). `KEYCLOAK_INTERNAL_BASE_URL` é o hop servidor→IdP dentro do cluster (`http://keycloak.virtual.consul` ou o API Gateway do Keycloak). Trocar os dois pelo mesmo hostname interno quebra o redirect no browser.

---

## 6. Vault — interruptor CIBA (LoA)

O LoA **não** vem como claim `acr`/`loa` do IdP. O PDP em `user-mcp/loa.py` calcula:

- **LoA 1** — OBO silencioso (leitura)
- **LoA 2** — JWT CIBA depois do Approve
- **Não há LoA 3** — só existe um mecanismo de elevação

O switch é `sys/capabilities-self` na path `ciba/<ferramenta>/<preferred_username>`:

| Path | Capability na policy | Efeito |
| --- | --- | --- |
| `ciba/list_all_users/{user,admin}` | `deny` | OBO silencioso |
| `ciba/search_users_by_first_name/{user,admin}` | `deny` | OBO silencioso |
| `ciba/create_user/admin` | `read` | CIBA (HITL) |
| `ciba/delete_user_by_email/admin` | `read` | CIBA (HITL) |
| `ciba/update_user_by_email/admin` | `deny` | OBO silencioso |
| `ciba/sensitive/admin` | `read` | CIBA em update “sensível” |

`read` = “esta ação exige Approve”. `deny` = “não exige CIBA”. Para desligar HITL em `create_user`, reescreva a policy `ciba-write` com `deny` nessa path (via `vault policy write`) e faça um novo login JWT; o probe usa o role `user-mcp-oidc-write`.

Roles JWT (`auth/jwt-keycloak`):

| Role | `bound_claims` | Policy |
| --- | --- | --- |
| `user-mcp-oidc-read` | `groups` ∈ user\|admin, `scope` contém `users.read`, `aud=user-mcp` | `ciba-list-users` |
| `user-mcp-oidc-write` | `groups` = admin, `scope` contém `users.write`, `aud=user-mcp` | `ciba-write` |

TTL do token Vault de probe: 300 s (max 900 s). Credenciais Postgres dinâmicas: roles `user-mcp-read-role` / `user-mcp-write-role` (TTL 1 h).

---

## 7. Consul — PEP de rede

O PEP de *IA* é o LiteLLM (`litellm-gateway`): web → LiteLLM → ai-agent, e ai-agent → LiteLLM → LLM / user-mcp. As Service Intentions só autorizam esses hops — qualquer outro origem é deny implícito.

| Origem | Destino |
| --- | --- |
| `web-api-gateway` | `web` |
| `web` | `litellm-gateway` |
| `litellm-gateway` | `ai-agent`, `user-mcp`, `opa-gov-api` |
| `opa-gov-api`, `ai-agent` | `opa-service` (ns `opa`) |
| `ai-agent` | `litellm-gateway`, `token-exchange` |
| `user-mcp` | `opa-mcp-authz` |
| `keycloak-api-gateway` | `keycloak` |
| `vault` (ns `vault`), `keycloak`, `user-mcp`, `litellm-gateway` | `postgres` |
| `ai-agent`, `user-mcp`, `consul-mcp-authz`, `opa-service`, `opa-mcp-authz` | `vault` (ns `vault`) |

`ext_authz` no inbound de `user-mcp` (`service-defaults-user-mcp.yaml`): `failureModeAllow: false`, `statusOnError: 403`. O peer mTLS que o OPA vê agora é `litellm-gateway` (não `ai-agent`); o catálogo Vault tem de ter a regra `default/litellm-gateway → default/user-mcp`.

O PDP de admissão do gateway é `litellm-gateway/pdp_auth.py`: `ai-agent` e `web` são admitidos pela identidade mTLS da malha (SPIFFE), sem chave compartilhada. O PDP de conteúdo (prompt injection) é o guardrail `OpaPdpGuardrail` contra `opa-gov-api` quando `OPA_GOV_API_URL` está setado; senão o guardrail permite. LoA/CIBA continua em `user-mcp/loa.py`.

No laboratório local o `make deploy` sobe o PDP de conteúdo inteiro: namespace `opa`, `opa-server` (Service `opa-service`, políticas do Vault KV `opa-policies/bundle` via Vault Agent) e `opa-gov-api` (imagem `agentguard-opa-gov-api:local`, construída no `make images` porque a do Docker Hub é só amd64), e seta `OPA_GOV_API_URL=http://opa-gov-api.virtual.consul:8000` no `litellm-gateway` (endereço Consul; a porta é a do Service). Fica no Makefile e não no `litellm-gateway.yaml` porque esse manifest é compartilhado e o guardrail é fail-closed: com a variável setada e o OPA fora do ar, todo prompt é negado. O `opa-gov-api` sobe com `OPA_FAIL_MODE=open` (falha de OPA = permite); mude para `closed` se quiser um gate de verdade.

Para mudar as políticas: edite `infra/config/opa_policies/*.rego`, rode de novo `vault kv put opa-policies/bundle ...` (como no passo 3 do `configure.sh`) e reinicie o pod `opa-server` — ele roda sem `--watch`. Teste direto:

```bash
kubectl --context local-minikube-demo -n opa port-forward svc/opa-service 8181:80 &
curl -s localhost:8181/v1/policies | jq -r '.result[].id'
```

### Vault como CA da malha

O Consul sobe antes do Vault, então começa com a CA embutida. Depois do unseal, `configure.sh` (passo 8) cria a policy `consul-connect-ca`, um token periódico e roda `consul connect ca set-config` com o provider `vault`. O próprio Consul cria e popula os mounts PKI `connect_root` / `connect_inter` e faz o cross-sign da nova raiz, então os proxies em execução continuam funcionando. O Consul valida o TLS do Vault com a CA do secret `vault-ca` (criado pelo `bootstrap.sh`, montado em `/consul/userconfig/vault-ca/ca.crt`). A CA do TLS de RPC dos servidores Consul é outra e não muda.

```bash
consul connect ca get-config | jq -r .Provider   # vault
```

Como o Vault é a CA, um Vault reiniciado (selado) não consegue emitir novos certificados de folha até ser aberto manualmente; os já emitidos continuam válidos até o TTL (72 h por padrão).

### Vault e Postgres na malha

Ambos têm sidecar e `protocol: tcp` (o default global é `http`, que quebraria o protocolo do Postgres e o TLS do Vault). Config em `infra/local-minikube/mesh-vault-postgres.yaml`, aplicada no passo 1b do `configure.sh` — antes do Postgres, porque o secrets engine `database` do Vault conecta ao Postgres pela malha durante o próprio `configure.sh`.

| Serviço | mTLS | Por quê |
| --- | --- | --- |
| `postgres` | estrito | todos os clientes (Vault, Keycloak, user-mcp, LiteLLM) estão na malha |
| `vault` | `permissive` | servidores Consul (operações da CA), NodePort do host e probes do kubelet chegam de fora da malha; clientes da malha continuam em mTLS e precisam de Intention |

`permissive` exige `allowEnablingPermissiveMutualTLS: true` em `deploy-k8s/mesh.yaml`. O Consul exige que o nome da ServiceAccount seja igual ao nome do serviço (ACLs ligadas), e o chart do Vault cria vários Services (`vault-internal`, `-active`, `-standby`, `-ui`) sobre o mesmo pod, por isso o pod tem `consul.hashicorp.com/kubernetes-service: "vault"`. Novo cliente do Vault com sidecar precisa entrar nas Intentions de `vault`.

`kubectl exec` em pod da malha precisa de `-c`: o Consul coloca o `consul-dataplane` primeiro, e ele não tem shell (`-c vault`, `-c postgres`). O `vault-0` fica `1/2` até o init/unseal — esperado.

---

## 8. Variáveis por serviço

Valores típicos do minikube local. Secrets: deixe o `keycloak.sh` preencher.

### web-app (`deploy-k8s/web-app.env`)

| Variável | Exemplo local | Notas |
| --- | --- | --- |
| `KEYCLOAK_CLIENT_ID` | `web` | |
| `KEYCLOAK_CLIENT_SECRET` | *(gerado)* | upsert do `keycloak.sh` |
| `KEYCLOAK_BASE_URL` | `http://localhost:8081` | browser |
| `KEYCLOAK_INTERNAL_BASE_URL` | `http://keycloak.virtual.consul` | token + JWKS |
| `KEYCLOAK_REALM` | `demo` | |
| `KEYCLOAK_REDIRECT_URI` | `http://localhost:8080/callback` | tem de bater com o client |
| `KEYCLOAK_SCOPES` | `openid profile email Agent.Invoke` | |
| `AI_AGENT_API_URL` | `http://litellm-gateway.virtual.consul:4000` | BFF → LiteLLM (`/v1/agent/*` pass-through para o agente) |
| `SESSION_PASSWORD` | ≥ 32 caracteres | cookie Iron Session |

### ai-agent (`deploy-k8s/ai-agent.env`)

| Variável | Default / exemplo | Notas |
| --- | --- | --- |
| `LANGCHAIN_MODEL` | `openai:qwen-local` | alias do `litellm-gateway/config.yaml`, **não** o provider cru, quando `LITELLM_BASE_URL` está setado |
| `LITELLM_BASE_URL` | `http://litellm-gateway.virtual.consul:4000` | tem prioridade sobre Ollama direto |
| `OLLAMA_BASE_URL` | `http://host.containers.internal:11434` | só se `LANGCHAIN_MODEL=ollama:...` e sem LiteLLM |
| `TOKEN_EXCHANGE_URL` | `http://token-exchange.virtual.consul/v1/identity/obo-token` | o manifest já injeta isso |
| `USER_MCP_URL` | `http://litellm-gateway.virtual.consul:4000/user-mcp` | pass-through LiteLLM → user-mcp; o manifest já injeta isso |
| `ACTOR_TOKEN_PATH` | `/vault/secrets/actor-token` | Vault Agent inject |
| `OBO_ROLE_NAME` | `agent-runtime` | |
| `MCP_TOOL_CALL_TIMEOUT_SECONDS` | `120` | **≥** `USER_MCP_CIBA_POLL_TIMEOUT_SECONDS` (110) |
| `BYPASS_AUTH_TOKEN_EXCHANGE` | `false` | `true` só em dev sem Keycloak |

### user-mcp (`deploy-k8s/user-mcp.env`)

| Variável | Exemplo local | Notas |
| --- | --- | --- |
| `USER_BACKEND` | `postgres` | `file` ignora Vault/DB |
| `USER_MCP_PG_URL` | `postgresql://postgres.default.svc.cluster.local:5432/users?sslmode=disable` | **sem** user/senha |
| `USER_MCP_DB_AUTH_MODE` | `vault` | `direct` usa `USER_MCP_DB_USER` / `PASSWORD` |
| `USER_MCP_VAULT_ADDR` | `https://vault.vault.svc:8200` | DNS in-cluster, não o ALB |
| `USER_MCP_VAULT_VERIFY_TLS` | `false` no lab (CA self-signed) | |
| `USER_MCP_VAULT_JWT_PATH` | `jwt-keycloak` | probe CIBA only |
| `USER_MCP_VAULT_JWT_READ_ROLE` | `user-mcp-oidc-read` | |
| `USER_MCP_VAULT_JWT_WRITE_ROLE` | `user-mcp-oidc-write` | |
| `USER_MCP_KEYCLOAK_BASE_URL` | `http://keycloak.virtual.consul/realms/demo` | issuer |
| `USER_MCP_AUDIENCE` | `user-mcp` | |
| `USER_MCP_ALLOW_UNAUTH_DISCOVERY` | `true` | `tools/list` sem Bearer; `tools/call` ainda exige scope |
| `USER_MCP_BYPASS_AUTH` | `false` | **incompatível** com `DB_AUTH_MODE=vault` |
| `USER_MCP_CIBA_KEYCLOAK_URL` | `http://keycloak.virtual.consul` | |
| `USER_MCP_CIBA_REALM` | `demo` | |
| `USER_MCP_CIBA_CLIENT_ID` | `ciba-client` | |
| `USER_MCP_CIBA_CLIENT_SECRET` | *(gerado)* | |
| `USER_MCP_CIBA_POLL_TIMEOUT_SECONDS` | `110` | |
| `USER_MCP_CIBA_APPROVE_URL` | `http://ciba-channel.virtual.consul:8093` | |

### token-exchange (`deploy-k8s/token-exchange.env`)

Prefixo `IDENTITY_BROKER_`.

| Variável | Exemplo local |
| --- | --- |
| `IDENTITY_BROKER_KEYCLOAK_URL` | `http://keycloak.virtual.consul` |
| `IDENTITY_BROKER_KEYCLOAK_REALM` | `demo` |
| `IDENTITY_BROKER_KEYCLOAK_TOKEN_EXCHANGE_AUDIENCE` | `user-mcp` |
| `IDENTITY_BROKER_OBO_CLIENT_ID` | `token-exchange` |
| `IDENTITY_BROKER_OBO_CLIENT_SECRET` | *(gerado, obrigatório — o processo não sobe sem ele)* |
| `IDENTITY_BROKER_VAULT_ADDR` | `https://vault.vault.svc.cluster.local:8200` |
| `IDENTITY_BROKER_VAULT_TLS_VERIFY` | `false` no lab |

Autorização OBO **antes** do Keycloak (`token-exchange/keycloak/authorization.py`): `users.read` → grupos `user` ou `admin`; `users.write` → só `admin`. 403 se o subject não tiver o grupo.

### ciba-channel

| Variável | Default |
| --- | --- |
| `CIBA_CHANNEL_HOST` / `PORT` | `0.0.0.0` / `8093` |
| `CIBA_CALLBACK_URL` | `http://keycloak.virtual.consul/realms/demo/protocol/openid-connect/ext/ciba/auth/callback` |

---

## 9. LLM (LiteLLM)

LiteLLM é o AI Gateway (PEP + PDP). Três rotas:

| Quem | URL no gateway | Upstream |
| --- | --- | --- |
| web-app | `/v1/agent/*` | `ai-agent:8000/v1/agent/*` |
| ai-agent (LLM) | `/v1/chat/completions` | Ollama / OpenAI via `model_list` |
| ai-agent (MCP) | `/user_mcp/mcp` | LiteLLM MCP gateway → `user-mcp/mcp` |

`ai-agent` não fala com o provider nem com o MCP. `LANGCHAIN_MODEL=openai:<model_name>` + `LITELLM_BASE_URL` apontam para um alias em `litellm-gateway/config.yaml`. `USER_MCP_URL` aponta para o gateway MCP `/user_mcp/mcp` (não HTTP pass-through — esse caminho quebra o handshake streamable-HTTP).

Admissão: os serviços não enviam credencial ao LiteLLM. A extensão `builtin/lua` no `ServiceDefaults` do `litellm-gateway` (`mesh-timeouts.yaml`) copia o SPIFFE ID do peer mTLS para `x-mesh-caller-spiffe`, e `pdp_auth.py` admite só `default/ai-agent` e `default/web`. `Authorization` permanece o JWT de upstream. `LITELLM_MASTER_KEY` fica só no `litellm-gateway-env` (login admin/UI); qualquer outro chamador, incluindo o `litellm-api-gateway`, cai na autenticação nativa do LiteLLM. A UI admin faz SSO no Keycloak; isso exige `DATABASE_URL` no banco Postgres `litellm` (criado por `keycloak.sh`).

O MCP server `user_mcp` usa `auth_type: "none"` (não `true_passthrough`): a UI admin trata `true_passthrough` como token repassado pelo cliente e mostra "Authentication required" em vez de listar as tools. O `extra_headers` continua repassando o `Authorization` (OBO) para o `user-mcp` nas chamadas de tool.

Agents > Discovery busca o agent card numa URL informada pelo usuário, e o guarda anti-SSRF do LiteLLM bloqueia os IPs virtuais do Consul (`240.0.0.0/4`, erro "URL targets a blocked address"). Por isso `general_settings.user_url_allowed_hosts` libera só `ai-agent.virtual.consul`, e o `ai-agent` serve o card em `GET /.well-known/agent-card.json` (também `/.well-known/agent.json` e `/agent.json`), sem autenticação. Depois de mudar o `config.yaml`, o ConfigMap é reaplicado pelo `make deploy`, mas o LiteLLM só lê no start: `kubectl rollout restart deployment/litellm-gateway`.

Aliases atuais:

| `model_name` | Destino |
| --- | --- |
| `qwen-local` | Ollama na máquina host (`host.containers.internal:11434/v1`), modelo `qwen2.5:7b` |
| `gpt-5-mini` | OpenAI (`OPENAI_API_KEY` no Secret `litellm-gateway-env`) |

Para Ollama:

```bash
ollama pull qwen2.5:7b
# em ai-agent.env:
LANGCHAIN_MODEL=openai:qwen-local
LITELLM_BASE_URL=http://litellm-gateway.virtual.consul:4000
```

O comment no `config.yaml` explica por que a rota Ollama usa o provider `openai/` contra o `/v1` do Ollama (streaming de tool_calls). Não troque para `ollama/` sem revalidar tools.

`OPENAI_API_KEY` no host entra no Secret na hora do `make deploy` (`OPENAI_API_KEY=${OPENAI_API_KEY:-}`). Sem a key, só `qwen-local` funciona.

---

## 10. Timeouts (CIBA)

A tool `create_user` **bloqueia** até o Approve. Toda a cadeia precisa caber ~110 s:

| Camada | Onde | Valor típico |
| --- | --- | --- |
| Poll CIBA | `USER_MCP_CIBA_POLL_TIMEOUT_SECONDS` | 110 |
| Cliente MCP do agente | `MCP_TOOL_CALL_TIMEOUT_SECONDS` | 120 |
| Cliente MCP do LiteLLM | `LITELLM_MCP_CLIENT_TIMEOUT` | 150 |
| Malha Consul | `infra/local-minikube/mesh-timeouts.yaml` | `requestTimeout` acima do poll |

Se o agente devolver timeout e o CIBA ainda estiver `pending`, o poll do MCP é menor que o timeout do LangChain, ou o HTTPRoute do web não recebeu o `RouteTimeoutFilter` (o `make deploy` aplica `mesh-timeouts.yaml` por último de propósito).

---

## 11. Combinações que quebram

| Combinação | Sintoma |
| --- | --- |
| `USER_MCP_BYPASS_AUTH=true` + `USER_MCP_DB_AUTH_MODE=vault` | recusado no boot (`configuration_error`) |
| `KEYCLOAK_BASE_URL` = DNS in-cluster | login redireciona para um host que o browser não resolve |
| `USER_MCP_VAULT_ADDR` / `USER_MCP_PG_URL` apontando para ALB/host | `ConnectError` / `Name or service not known` de dentro do pod |
| Catálogo MCP ainda com `default/ai-agent` | 403 em `tools/call` (`x-authz-reason` aponta `agent=default/litellm-gateway`) |
| `MCP_TOOL_CALL_TIMEOUT_SECONDS` < poll CIBA | create/delete aborta com o pedido ainda no canal `:8082` |
| Licença Vault sem Agentic IAM | 403 em `database/creds` mesmo com JWT válido |
| `kubectl exec vault-0` / `postgres-0` sem `-c` | `exec: "sh": executable file not found` (cai no `consul-dataplane`) |
| Vault reiniciado e ainda selado | sem novos certificados de folha na malha (o Vault é a CA) até o unseal |
| MCP server com `auth_type: true_passthrough` | UI do LiteLLM mostra "Authentication required" e não lista tools (a API lista normalmente) |
| Agents > Discovery sem `user_url_allowed_hosts` | "URL targets a blocked address (240.0.0.x)" |
| `OPA_GOV_API_URL` setado com `opa-gov-api` / `opa-server` fora do ar | guardrail nega todo prompt (`PDP_Decision=DENY path=guardrail/opa_unreachable`) |
| Extensão Lua do `litellm-gateway` não aplicada | `x-mesh-caller-spiffe` chega do cliente sem ser removido — a admissão por identidade não pode ser confiada |

---

## 12. Verificação rápida

```bash
cd infra/local-minikube && make status

curl -s http://localhost:8081/realms/demo/.well-known/openid-configuration | jq .issuer
# http://localhost:8081/realms/demo

VAULT_ADDR=https://localhost:8200 VAULT_SKIP_VERIFY=1 VAULT_TOKEN=$(cat generated/vault_token) \
  vault read sys/config/oauth-resource-server/keycloak-demo
```

```bash
consul connect ca get-config | jq -r .Provider   # vault (com CONSUL_HTTP_ADDR/TOKEN apontando para o Consul)
```

Fluxo funcional: login `admin`/`admin` em `:8080` → “List all users.” (LoA 1, sem CIBA) → create user (pedido em `:8082`) → Approve → inspector **LoA 2 ALLOW** em `create_user`.

---

## 13. AWS / EKS

O mesmo conjunto de manifests em `deploy-k8s/` vale depois do Terraform em `infra/` (`module.common` → `servers` → `consul_client_k8s` → `observability`). Diferenças:

- `VAULT_ADDR` e Postgres usam o DNS **in-cluster**, nunca o ALB (SG do LB só admite o `/32` de quem aplicou o Terraform).
- Imagens Docker Hub `panchalravi/agentguard-*` são amd64; em Apple Silicon o `make images` constrói tags `:local`.
- Não rode `make up` contra um cluster AWS — o profile `local-minikube-demo` é só o laboratório nesta máquina.
