# Arquitetura detalhada — laboratório minikube

Estado **atual** do `make up` em `infra/local-minikube`. Papéis, pasta de cada peça, tools, scripts e o que cada um faz.

| Documento | Função |
| --- | --- |
| Este arquivo | Mapa do projeto: quem é responsável, onde está o código, o que cada script/tool faz |
| [`fluxo-e-responsabilidades.md`](./fluxo-e-responsabilidades.md) | Hop a hop de um `list users` / step-up e tabela PEP vs PDP |
| [`pep-inventory.md`](./pep-inventory.md) | Inventário de **regras** (incluindo Lua e `USER_MCP_PEP_MODE=local`, que o lab não usa) |
| [`Guia_Configuracao.md`](./Guia_Configuracao.md) | Env vars, URLs, Keycloak, Ollama, timeouts |

O desenho de produto (diagramas PNG) em `documentation/agent_control_plane/` ainda mostra o agente com policy engine Lua — o lab já passou o hop de IA pelo LiteLLM.

---

## 1. Visão geral

```
                         localhost
  :8080 web   :8081 Keycloak   :4000 LiteLLM UI
  :8200 Vault :8501 Consul     :8753 hop viewer (host)
  :11434 Ollama (host, 0.0.0.0)

Browser
  │  Consul API Gateway (web-api-gateway)
  ▼
web-app  ──login──► Keycloak
  │  mTLS SPIFFE default/web
  ▼
LiteLLM  ──PEP admissão──► pdp_auth.py (SPIFFE)
  │  /v1/agent/* pass-through
  ▼
ai-agent  ──OBO──► token-exchange ──RFC 8693──► Keycloak
  │  /v1/chat/completions
  ▼
LiteLLM ──► Ollama qwen2.5:7b  (openai/ contra /v1)
  │  MCP /user_mcp/mcp  tools/call
  ▼
LiteLLM pdp_mcp.py  ──PDP──► opa-server  POST /v1/data/mcp/pep/decision
  │  extra_headers Authorization = OBO
  ▼
user-mcp (runtime)  ──X-Vault-Token──► Vault ──creds──► Postgres
                     Transform PII se groups ∌ admin
```

**Fatia de tools/call:** LiteLLM = PEP · OPA `mcp.pep` = PDP · Vault = segredos · user-mcp = runtime SQL.

---

## 2. Papéis dos produtos

| Produto | Papel | Onde vive no lab | Não faz |
| --- | --- | --- | --- |
| **Consul** | API Gateway + malha mTLS SPIFFE + Service Intentions | Helm no profile `local-minikube-demo`; CRs em `deploy-k8s/` e `infra/local-minikube/mesh-*.yaml` | Tool, scope, LoA, prompt, credencial |
| **LiteLLM** | PEP + AI Gateway | Imagem oficial + ConfigMap `litellm-gateway/` | Guardar ACL; executar SQL |
| **OPA `opa-server`** | PDP de IA (`mcp.pep`) | `deploy-k8s/opa-server.yaml`; Rego `infra/config/opa_policies/mcp_pep.rego` | Interceptar HTTP |
| **OPA + `opa-gov-api`** | PDP de conteúdo (prompt injection / code safety) | `opa-gov-api/`; bundle `infra/config/opa_policies/*.rego` | Catálogo MCP |
| **Vault** | Segredos, CA Connect, bundle/catálogo OPA, OAuth Resource Server | Helm; policies em `configure.sh` / `keycloak.sh` | Decidir `tools/call` |
| **Keycloak** | IdP (login, OBO, step-up OTP) | Imagem com SPI `keycloak-providers/` | PEP de malha |
| **Ollama** | LLM local | Host `0.0.0.0:11434` (LaunchAgent `local.ollama`, não `brew services`) | |

---

## 3. Mapa do repositório

```
web-app/                 BFF Next.js (login + chat → LiteLLM)
ai-agent/                Orquestrador LangChain (escolhe tool, pede OBO)
litellm-gateway/         PEP + rotas LLM/MCP (ConfigMap, sem Dockerfile próprio)
user-mcp/                Runtime FastMCP (SQL + Vault creds/Transform)
token-exchange/          Broker RFC 8693
keycloak-providers/      SPI Java (mapper Vault/RAR) na imagem Keycloak
opa-gov-api/             HTTP /evaluate e /mask na frente do OPA
opa-mcp-auth/            Rego legado ext_authz + seed do catálogo Vault
consul-mcp-authz/        UI/API operador do catálogo KV
opa-policy-studio/       PoC browser para editar Rego (não sobe no make up)
vault-log/               Viewer SSE dos hops
deploy-k8s/              Manifests + *.env (env git-ignored)
infra/local-minikube/    make up / scripts do lab
infra/config/            Licenças + bundle OPA
documentation/           Guias e inventários
```

---

## 4. Aplicações — responsabilidade e arquivos

### 4.1 `web-app/` — BFF

Papel: login Keycloak (Authorization Code + PKCE), cookie de sessão, stream do chat para o LiteLLM. **Não** fala com `ai-agent` nem com `user-mcp`.

| Arquivo | O que faz |
| --- | --- |
| `src/app/api/auth/{login,callback,logout,me,claims}/route.ts` | OAuth |
| `src/app/api/agent/query/route.ts` | Proxy stream → `AI_AGENT_API_URL` (LiteLLM `:4000`) |
| `src/app/api/agent/{tokens,assurance}/route.ts` | Inspector de tokens / LoA |
| `src/lib/auth/*` | Sessão, JWKS, PKCE |
| `src/lib/agent/{client,stream,normalize}.ts` | Cliente HTTP do agente via gateway |
| `src/components/chat/*` | UI Carbon |
| `src/lib/config.ts` | Env (`KEYCLOAK_*`, `AI_AGENT_API_URL`) |

Manifest: `deploy-k8s/web-app.yaml` + `web-app-gateway.yaml`. Env: `deploy-k8s/web-app.env`.

### 4.2 `ai-agent/` — orquestrador

Papel: valida o JWT do humano, descobre tools MCP, para cada tool pede OBO só com os scopes da tool, chama o LLM e o MCP **via LiteLLM**. Não é PEP de tools/call.

| Arquivo | O que faz |
| --- | --- |
| `agent_api.py` | FastAPI: `POST /v1/agent/query`, `GET /v1/agent/tokens`, agent-card |
| `agent_runtime.py` | LangChain: bind tools, stream |
| `mcp_client.py` | `tools/list` e `tools/call` em `USER_MCP_URL` (LiteLLM `/user_mcp/mcp`) |
| `scoped_tool.py` | Wrapper: OBO por chamada com `_meta.required_scopes` |
| `identity.py` | Actor token Vault + cliente do token-exchange |
| `security.py` | Bearer inbound |
| `tools.py` | Tool **local** `shell` (subprocess bash) — **não** passa pelo MCP PEP |
| `config.py` | `LANGCHAIN_MODEL=openai:qwen-local`, `LITELLM_BASE_URL`, timeouts |

No lab, `USER_MCP_URL=http://litellm-gateway.virtual.consul:4000/user_mcp/mcp`. Scope ALLOW/DENY é o LiteLLM+OPA; em `USER_MCP_PEP_MODE=runtime` o MCP **não** revalida scope.

### 4.3 `litellm-gateway/` — PEP + AI Gateway

Não tem imagem própria: `make deploy` monta estes arquivos num ConfigMap no container LiteLLM.

| Arquivo | O que faz |
| --- | --- |
| `config.yaml` | `model_list` (`qwen-local` → Ollama `/v1`, `gpt-5-mini`), guardrails, MCP `user_mcp` (`auth_type: none`), `custom_auth` |
| `pdp_auth.py` | Admissão: SPIFFE `default/web` / `default/ai-agent` → `admit_mesh`; UI/SSO → fallthrough |
| `pdp_mcp.py` | `pre_mcp_call`: JWKS Keycloak → POST OPA → `extra_headers Authorization` |
| `pdp_guardrail.py` | Conteúdo: POST `opa-gov-api/evaluate` se `OPA_GOV_API_URL` setado |
| `test_pdp_auth.py` / `test_pdp_mcp.py` | Testes unitários do PEP |

YAML nativo do LiteLLM cobre keys, allowlist, OBO RFC 8693, Presidio. **Não** cobre catálogo OPA + LoA + SPIFFE — por isso CustomGuardrail + `custom_auth`.

Audit: `print()` JSON em stdout (`pdp_decision`). Loggers `litellm-gateway.*` são engolidos pela imagem.

### 4.4 `user-mcp/` — runtime MCP

Papel no lab (`USER_MCP_PEP_MODE=runtime`): executar a tool. Extrai claims do Bearer **sem** JWKS, **não** faz scope check. Apresenta o JWT a Vault como `X-Vault-Token`; o Vault valida o `acr` de novo no login `jwt-keycloak`.

`USER_MCP_PEP_MODE=local` (não é o `make up`): este processo volta a ser PEP (JWKS + `scope_check`). Código ainda existe.

| Arquivo | O que faz |
| --- | --- |
| `server.py` | ASGI uvicorn |
| `mcp_app.py` | FastMCP + lifespan do repositório |
| `config.py` | `USER_MCP_*`, `pep_mode` |
| `tools/users.py` | Cinco tools MCP + contrato `TOOL_SCOPE_REQUIREMENTS` (ainda declarado em `_meta` para o agente) |
| `auth/jwt_validator.py` | `runtime` = decode unverified; `local` = JWKS |
| `auth/scope_check.py` | Enforce de scope **só em modo local** |
| `auth/context.py` | ContextVar identidade |
| `storage/postgres_repo.py` | SQL + `database/creds` + Transform |
| `storage/file_repo.py` | Backend arquivo (dev) |
| `vault_client.py` | HTTP Vault; logs `vault_db_creds_issued`, `transform_encode` |
| `loa.py` | Metadados de assurance na resposta da tool |

### 4.5 `token-exchange/` — broker OBO

| Arquivo | O que faz |
| --- | --- |
| `api/main.py` / `api/routes.py` | HTTP broker |
| `keycloak/obo_broker.py` | RFC 8693: subject (humano) + actor (Vault) → JWT `aud=user-mcp` |
| `keycloak/authorization.py` | Gate de grupo: `users.read` / `users.write` |
| `broker/vault_client.py` | Actor / identity Vault |

### 4.6 `keycloak-providers/`

`VaultJwtCompatMapper.java`: tira `typ`, injeta `act` do actor Vault, sintetiza RAR a partir de `users.read`/`users.write`. Sem isso o OAuth Resource Server do Vault recusa o JWT. Bake na imagem em `keycloak.sh`.

### 4.7 `opa-gov-api/`

`opa_gov_api.py` + `opa_client.py`: `POST /evaluate` (prompt injection + code safety) e `POST /mask` (PII). O LiteLLM no lab chama `/evaluate` (não `/mask`). Bundle: `infra/config/opa_policies/{prompt_injection,code_safety,pii_filter,patterns}.rego`.

### 4.8 `opa-mcp-auth/` (legado de enforce; vivo como catálogo)

O `make deploy` ainda sobe `opa-mcp-authz` para o **consul-mcp-authz** editar o KV. O sidecar de `user-mcp` **não** tem `ext_authz`. Quem consulta o catálogo no hop é o **opa-server** (`data.rules`).

| Arquivo | O que faz |
| --- | --- |
| `policy/mcp/authz/mcp_authz.rego` | Política gRPC ext_authz (não está no caminho do lab) |
| `vault/seed-catalog.sh` | Seed inicial do KV |

### 4.9 `consul-mcp-authz/`

API FastAPI (`api/`) + UI Next (`ui/`) no mesmo pod. CRUD do documento Vault `opa-policies/mcp-authz/catalog`. O OPA `--watch` recarrega; o próximo `tools/call` no LiteLLM já vê a regra nova. **Não** é o PEP.

### 4.10 `vault-log/` — observabilidade UAT

| Arquivo | O que faz |
| --- | --- |
| `serve.py` | HTTP `127.0.0.1:8753` + SSE `/stream`; `kubectl logs -f` (web, ai-agent, token-exchange, litellm-gateway, user-mcp, opa, vault) |
| `app.js` | Abas Fluxo/hops, Timeline, Auditoria; parse JSON PEP/PDP |
| `index.html` / `styles.css` | UI |
| `collect-cluster.sh` | Dump estático (opcional; o viewer ao vivo não precisa) |

`make hop-logs` mata o que estiver em `:8753` e sobe `serve.py`.

### 4.11 Fora do caminho do `make up`

| Pasta | Papel |
| --- | --- |
| `opa-policy-studio/` | Autoração Rego no browser |

---

## 5. Tools

### 5.1 MCP em `user-mcp/tools/users.py`

Contrato também em `mcp_pep.rego` (`required_scopes`, `loa2_tools`, `disabled_tools`) e no catálogo Vault.

| Tool | Scope | LoA no lab | Efeito |
| --- | --- | --- | --- |
| `list_all_users` | `users.read` | 1 | `SELECT` todos; Transform se não-admin |
| `search_users_by_first_name` | `users.read` | 1 | `SELECT` filtrado |
| `update_user_by_email` | `users.write` | 1 | Update silencioso |
| `create_user` | `users.write` | **2** | INSERT só depois do step-up OTP (`acr=2`) |
| `delete_user_by_email` | `users.write` | — | **Desabilitada** (`disabled_tools`); PEP nega antes do OPA, agente não recebe a tool |

Discovery: `USER_MCP_ALLOW_UNAUTH_DISCOVERY=true` deixa `tools/list` sem Bearer (canal já é mTLS).

### 5.2 Tool local do agente (`ai-agent/tools.py`)

| Tool | O que faz | PEP |
| --- | --- | --- |
| `shell` | `bash -lc` no cwd da imagem do agente | **Não** passa por `pdp_mcp`. Intentions + prompt injection (se o guardrail estiver efetivo) |

---

## 6. Políticas (PDP) e catálogo

| Artefato | Onde | Quem consulta |
| --- | --- | --- |
| `infra/config/opa_policies/mcp_pep.rego` | Bundle Vault `opa-policies/bundle` + arquivo no opa-server | LiteLLM `pdp_mcp.py` |
| Catálogo JSON `rules[source][dest].allow` | Vault KV `opa-policies/mcp-authz/catalog` → `data.rules` | `mcp.pep` |
| `prompt_injection.rego`, `code_safety.rego`, `pii_filter.rego`, `patterns.rego` | mesmo bundle | `opa-gov-api` → guardrail LiteLLM |
| `deploy-k8s/service-intentions.yaml` | Consul | Envoy sidecar |
| Lua SPIFFE `x-mesh-caller-spiffe` | `infra/local-minikube/mesh-timeouts.yaml` | `pdp_auth.py` |
| Role Vault `user-mcp-oidc-write` (`bound_claims.acr=2`) | `keycloak.sh` | Vault, no login `jwt-keycloak` (revalida o step-up) |
| Transform + `database/creds` roles | `configure.sh` / `keycloak.sh` | user-mcp |

Regra de catálogo no lab: `source=default/litellm-gateway`, `dest=default/user-mcp` (não mais `default/ai-agent`).

---

## 7. Scripts e Makefile

Tudo em `infra/local-minikube/` salvo nota.

| Alvo / script | O que faz |
| --- | --- |
| `make up` | `bootstrap` → `configure` → `keycloak` → `images` → `deploy` → `verify` → `credentials` |
| `make down` | Apaga o profile minikube + container bridge `:4000` |
| `make redeploy` | `images` + `deploy` (iteração de código) |
| `make hop-logs` | Viewer http://127.0.0.1:8753/ |
| `make status` / `verify` | Pods + curl nas portas do host |
| `bootstrap.sh` | minikube (driver **docker**/Colima), Helm Consul+Vault, TLS, NodePorts |
| `configure.sh` | Postgres users, Vault `k8s_jwt`, bundle OPA, identity OIDC, `database/` engine, Connect CA, token consul-mcp-authz |
| `keycloak.sh` | Imagem SPI, realm `demo`, clients, ORS Vault, Agent Registry, upsert `deploy-k8s/*.env` |
| `ensure-litellm-pep-vault-role.sh` | Role/policy Vault para o LiteLLM (actor token / JWKS path se precisar de secret) |
| `print-credentials.sh` | URLs e logins no fim do `make up` |
| `local-config.sh` | Versões Helm, senha Postgres, claims OIDC |
| `opa-mcp-auth/vault/seed-catalog.sh` | Seed do catálogo (também coberto pelo `configure.sh`) |
| `vault-log/collect-cluster.sh` | Snapshot de logs (opcional) |
| `vault-log/serve.py` | SSE ao vivo |

Gerados (git-ignored) em `infra/local-minikube/generated/`: tokens Vault/Consul, secrets Keycloak, Helm values, Corefile.

Imagens locais (`make images`): `user-mcp`, `ai-agent`, `web-app`, `token-exchange`, `opa-gov-api`. LiteLLM e OPA usam imagens oficiais.

---

## 8. Manifests Kubernetes (`deploy-k8s/`)

| Manifest | Serviço |
| --- | --- |
| `web-app.yaml` + `web-app-gateway.yaml` | BFF + API Gateway `:8080` |
| `ai-agent.yaml` + `ai-agent-config.yaml` | Agente + actor token |
| `litellm-gateway.yaml` + `litellm-gateway-gateway.yaml` | PEP + UI `:4000` |
| `user-mcp.yaml` | Runtime MCP |
| `service-defaults-user-mcp.yaml` | Timeout HTTP; **sem** ext_authz |
| `token-exchange.yaml` | Broker |
| `opa-server.yaml` | PDP `mcp.pep` (ns `opa`) |
| `opa-gov-api.yaml` | Conteúdo |
| `opa-mcp-authz.yaml` | OPA legado + vault-agent do catálogo |
| `service-intentions.yaml` | Who-may-talk |
| `proxy-defaults.yaml` / `mesh.yaml` | Defaults da malha |
| `keycloak.yaml` + gateway | IdP `:8081` |
| `service-defaults-agent-*.yaml` | Lua/OPA no **inbound do agente** — **não** aplicados pelo `make deploy` |

NodePorts locais: `infra/local-minikube/{web,keycloak,litellm-gateway}-nodeport.yaml`. Timeouts e Lua SPIFFE: `mesh-timeouts.yaml`. Vault/Postgres na malha: `mesh-vault-postgres.yaml`.

---

## 9. Identidade e hops (resumo)

| Hop | Identidade | PEP |
| --- | --- | --- |
| Browser → web | Cookie sessão / JWT Keycloak | Consul API Gateway |
| web → LiteLLM | SPIFFE `default/web` | `pdp_auth` `admit_mesh` |
| LiteLLM → ai-agent | JWT do humano (Authorization intacto) | Intentions |
| ai-agent → token-exchange | JWT humano + actor Vault | Intentions + grupos no broker |
| ai-agent → LiteLLM (LLM) | SPIFFE `default/ai-agent` | `pdp_auth` + guardrail conteúdo |
| LiteLLM → Ollama | sem auth | bind host `0.0.0.0:11434` |
| ai-agent → LiteLLM (MCP) | JWT OBO | `pdp_mcp` + `mcp.pep` |
| LiteLLM → user-mcp | JWT OBO injetado | Intentions; MCP não revalida |
| user-mcp → Vault | mesmo JWT como `X-Vault-Token` | ORS + ACL da entity |
| user-mcp → Postgres | lease dinâmico | Intentions `user-mcp → postgres` |

---

## 10. Observabilidade e UAT

| Superfície | URL |
| --- | --- |
| Canal | http://localhost:8080 (`user`/`user` list; `writer`/`writer` + step-up OTP create) |
| Keycloak | http://localhost:8081 |
| LiteLLM UI | http://localhost:4000/ui |
| Hop viewer | http://127.0.0.1:8753/ (`make hop-logs`) |
| Vault | https://localhost:8200 |
| Consul | https://localhost:8501 |

Eventos JSON no viewer: `pdp_decision` (enforce + `pdp_package`/`pdp_policy`/`reason`/scopes), `jwt_identity_bound`, `vault_db_creds_issued`, `transform_encode`, `tool_invoked`.

---

## 11. Como iterar

| Mudança | Onde | Comando |
| --- | --- | --- |
| PEP LiteLLM | `litellm-gateway/*.py`, `config.yaml` | `make deploy` (recria ConfigMap + restart) |
| MCP / agente / web | pastas da app | `make images` + `make redeploy` |
| Rego `mcp.pep` / conteúdo | `infra/config/opa_policies/` | `vault kv put opa-policies/bundle …` (OPA `--watch`) |
| Catálogo de tools | KV ou UI consul-mcp-authz | imediato no próximo `tools/call` |
| Intentions / timeouts | YAML mesh | `kubectl apply` / `make deploy` |

Não commitar `deploy-k8s/*.env` nem `infra/local-minikube/generated/`.
