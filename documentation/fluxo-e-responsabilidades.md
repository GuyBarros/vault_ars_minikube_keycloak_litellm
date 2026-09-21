# Fluxo e responsabilidades — laboratório minikube

Estado **atual** do lab (`make up` em `infra/local-minikube`). Não descreve o desenho AWS/EKS nem o PEP Lua legado.

Mapa de pastas, tools e scripts: [`arquitetura-detalhada.md`](./arquitetura-detalhada.md).  
Inventário das regras (incluindo o que ainda não migrou): [`pep-inventory.md`](./pep-inventory.md).  
Configuração: [`Guia_Configuracao.md`](./Guia_Configuracao.md).

PEP = intercepta o hop e aplica ALLOW / DENY / rewrite / step-up.  
PDP = calcula a decisão.  
Runtime = executa a tool depois da decisão.

---

## 1. Produtos e papéis

| Produto / processo | Papel | Faz | Não faz |
| --- | --- | --- | --- |
| **Consul** | API Gateway + malha | Norte-sul (`:8080` web, `:8081` Keycloak, `:8082` CIBA, `:4000` LiteLLM UI). mTLS SPIFFE e Service Intentions no leste-oeste. | Tool, scope, LoA, prompt, credencial Postgres |
| **LiteLLM** | PEP + AI Gateway | Admissão SPIFFE (`pdp_auth.py`). Em cada `/v1/agent/*` vindo de `web`, JWKS do subject JWT (`aud=token-exchange`). Proxy `/v1/agent`, `/v1/chat/completions`, MCP `/user_mcp/mcp`. `tools/call`: JWT OBO/CIBA → OPA → injeta (`pdp_mcp.py`). | Guardar ACL. Executar SQL. Mintar credencial de banco |
| **OPA `opa-server`** (`mcp.pep`) | PDP de IA (tools/call) | Catálogo Vault KV + `required_scopes` + `ciba_tools`. `POST /v1/data/mcp/pep/decision` | Interceptar HTTP. Falar com o browser |
| **OPA + `opa-gov-api`** | PDP de conteúdo (opcional) | Prompt injection / code safety via guardrail LiteLLM | Ligado no lab só se `OPA_GOV_API_URL` estiver setado; fail-closed se o OPA cair |
| **Vault** | Segredos + CA da malha | Actor token do agente, `database/creds`, Transform PII, OAuth Resource Server, Connect CA, bundle/catálogo OPA | Decidir `tools/call`. Não é o PDP de catálogo/CIBA neste lab |
| **Keycloak** | IdP | Login do humano, OBO RFC 8693, CIBA HITL | PEP de malha |
| **token-exchange** | Broker OBO | Troca JWT do usuário + actor Vault → JWT `aud=user-mcp` com scope | Catálogo MCP |
| **ai-agent** | Orquestrador | Chat, escolhe tool, pede OBO, chama MCP **via LiteLLM** | PDP de tools/call |
| **user-mcp** | Runtime MCP | SQL + retrieve Vault (`X-Vault-Token` = JWT). `USER_MCP_PEP_MODE=runtime`: extrai claims sem JWKS, sem scope/CIBA | ALLOW/DENY de tool (isso é LiteLLM+OPA) |
| **web-app** | BFF | Login Keycloak, stream do chat para o LiteLLM | Falar direto com o agente ou com o MCP |
| **ciba-channel** | Canal HITL | Approve/Deny em `:8082` | Política (quem precisa de CIBA) |
| **Ollama** (host) | LLM local | `qwen2.5:7b` em `0.0.0.0:11434`; LiteLLM usa `openai/qwen2.5:7b` contra `/v1` | |

---

## 2. Fluxo de um `list users` (LoA 1, sem CIBA)

```
Browser
  │  TLS — Consul API Gateway :8080
  ▼
web-app  (login Keycloak; POST /api/agent/query)
  │  mTLS SPIFFE default/web
  ▼
LiteLLM pdp_auth          PEP admit_mesh + JWKS do subject em /v1/agent
  │  pass-through /v1/agent/query
  ▼
ai-agent                  relê actor token (exp) antes do LLM; OBO via token-exchange
  │  chat/completions (modelo qwen-local)
  ▼
LiteLLM → Ollama          host.minikube.internal:11434/v1
  │  tools/call list_all_users  (Bearer = JWT OBO)
  ▼
LiteLLM pdp_mcp           PEP: valida JWKS, POST OPA mcp.pep
  │  enforce=inject_obo_jwt
  ▼
user-mcp                  runtime: jwt_identity_bound, SQL
  │  X-Vault-Token = OBO
  ▼
Vault                     database/creds + Transform (se não-admin)
  ▼
Postgres                  SELECT; lease revogado ao fim
  ▼
ai-agent → web → browser
```

`create_user` insere **CIBA** depois do OPA (`ciba_required`): LiteLLM poll Keycloak, humano em `:8082`, `enforce=inject_ciba_jwt`. Update é silencioso. `delete_user_by_email` é recusada no PEP antes do OPA e do CIBA (`enforce=deny`, `reason=tool_disabled`); o agente também não recebe essa tool.

---

## 3. Onde está cada regra

| Decisão | PEP (enforce) | PDP (política) | Artefato |
| --- | --- | --- | --- |
| Quem fala com quem (rede) | Envoy sidecar | Consul Service Intentions | `deploy-k8s/service-intentions.yaml` |
| Admissão no AI Gateway | `pdp_auth.py` | SPIFFE em `{default/web, default/ai-agent}` | header `x-mesh-caller-spiffe` (Lua em `mesh-timeouts.yaml`) |
| Catálogo MCP (qual tool) | `pdp_mcp.py` `pre_mcp_call` | `mcp.pep` + Vault KV `opa-policies/mcp-authz/catalog` | `infra/config/opa_policies/mcp_pep.rego` |
| Scope da tool | idem | `required_scopes` no Rego | `users.read` / `users.write` |
| HITL / LoA | LiteLLM poll CIBA + injeta JWT; `delete_user_by_email` é deny no PEP | `ciba_tools` = `create_user`; `disabled_tools` = `delete_user_by_email` | Keycloak CIBA + `ciba-channel` |
| Credencial Postgres | user-mcp pede; Vault recusa se ACL falhar | OAuth Resource Server + policies da entity | `database/creds/user-mcp-{read,write}-role` |
| Máscara PII | user-mcp chama Transform | role `user-mcp-transform`; skip se `groups` contém `admin` | Vault Transform |
| Prompt injection | guardrail LiteLLM (lab: código ON, efetivo só com OPA no ar) | bundle `opa-policies/bundle` via `opa-gov-api` | Lua no `ai-agent` **não** é aplicado pelo `make deploy` |

O LiteLLM **não** tem ACL nativa para catálogo + CIBA + SPIFFE. YAML nativo cobre keys/allowlist/OBO RFC 8693; o restante é CustomGuardrail `pdp_mcp.py` + `custom_auth` `pdp_auth.py`.

`ext_authz` no inbound de `user-mcp` está **desligado**. O catálogo no Vault continua; quem consulta é o **opa-server**, não o sidecar do MCP.

---

## 4. Observabilidade (UAT)

```bash
cd infra/local-minikube
make hop-logs    # http://127.0.0.1:8753/
```

Três abas no **mesmo** SSE (`kubectl logs -f`):

| Aba | O que mostra |
| --- | --- |
| Fluxo / hops | Cada hop com **PEP enforce** e **política PDP** (`package`, `reason`, scopes, catálogo) |
| Timeline por requisição | Eventos correlacionados por `request_id` |
| Auditoria Vault / OBO | Mint OBO, `database/creds`, Transform, decisão PEP |

UAT humano: http://localhost:8080 (`user`/`user` para list; `writer`/`writer` + `:8082` para create).

Eventos JSON relevantes: `pdp_decision`, `jwt_identity_bound`, `vault_db_creds_issued`, `transform_encode`, `tool_invoked`, `ciba_started` / `ciba_approved`.

---

## 5. Ollama (modelo local)

O alias `qwen-local` no LiteLLM aponta para `http://host.minikube.internal:11434/v1` (Colima/minikube; **não** `host.containers.internal`).

```bash
# Homebrew bind default é 127.0.0.1 — pods não alcançam.
# LaunchAgent local.ollama com OLLAMA_HOST=0.0.0.0:11434
ollama pull qwen2.5:7b
```

Não use `brew services start ollama` sem `OLLAMA_HOST=0.0.0.0:11434`.

---

## 6. Como iterar código

| Mudança | Comando |
| --- | --- |
| `litellm-gateway/*.py` / `config.yaml` | ConfigMap `litellm-gateway-config` + `rollout restart deploy/litellm-gateway` (`make deploy` já faz) |
| `user-mcp/` / `ai-agent/` / `web-app/` | `make images` (ou build da imagem) + `make redeploy` |
| Rego `mcp_pep.rego` / bundle | `vault kv put opa-policies/bundle …` (OPA `--watch`) |
| Catálogo MCP | Vault KV `opa-policies/mcp-authz/catalog` ou UI `consul-mcp-authz` |

Não commitar `deploy-k8s/*.env` nem `infra/local-minikube/generated/`.
