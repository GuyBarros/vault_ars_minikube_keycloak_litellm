# Inventário do PEP — Agentic IAM Runtime Security

Mapa do Policy Enforcement Point **no laboratório minikube** e do que ainda é legado.

Fonte do fluxo e papéis: [`fluxo-e-responsabilidades.md`](./fluxo-e-responsabilidades.md).  
Mapa de código, tools e scripts: [`arquitetura-detalhada.md`](./arquitetura-detalhada.md).

PEP = ponto que **intercepta** o hop e aplica ALLOW / DENY / rewrite / step-up.  
PDP = ponto que **calcula** a decisão.

## Alvo (três produtos) — é o estado do `make up`

| Produto | Papel | Responsabilidade |
| --- | --- | --- |
| **Consul** | API Gateway | Norte-sul do canal (`web-api-gateway` :8080, Keycloak :8081, CIBA :8082, LiteLLM UI :4000). mTLS SPIFFE e Service Intentions no leste-oeste. Não decide tool, LoA, prompt nem credencial. |
| **LiteLLM** | PEP + AI Gateway | ALLOW / DENY / STEP_UP de tráfego de IA (`web → agente`, `agente → LLM`, `agente → MCP`). Consulta o **OPA**; não guarda ACL; não executa SQL. |
| **OPA (`opa-server`)** | PDP | Fonte da decisão de IA: catálogo MCP, tool→scope, interruptor CIBA. Bundle + catalog no Vault KV; o OPA calcula. Guardrail de conteúdo (`opa-gov-api`) é separado e opcional. |
| **Vault** | Segredos | Credenciais (`database/creds`, Transform, actor token, CA Connect). Não intercepta o hop HTTP; **não** decide tools/call. |

Keycloak = IdP. `user-mcp` = runtime de tool (SQL + retrieve de credenciais no Vault), **sem** PEP no modo `runtime`.

```
Browser
  │  Consul API Gateway  (borda)
  ▼
web-app
  │
  ▼
LiteLLM  =  PEP + AI Gateway     ──ask──►  OPA opa-server = PDP (mcp.pep)
  │              │              │
  ▼              ▼              ▼
ai-agent      Ollama         user-mcp runtime
                               │
                               └── creds/Transform ──► Vault
```

O LiteLLM **não** é fonte da policy. O hop `tools/call` é **LiteLLM PEP → OPA PDP → user-mcp runtime**.

---

## 1. Onde está o PEP hoje

Não há um único PEP de *conteúdo* (Lua no agente ainda é legado e **não** entra no `make deploy`). O hop `tools/call` já passa pelo LiteLLM.

```
Browser
  │  PEP de borda (Consul API Gateway + Service Intentions)
  ▼
web-app
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│  LiteLLM  — PEP + AI Gateway                                │
│  • admissão SPIFFE pdp_auth.py              (ATIVO)         │
│  • guardrail /evaluate OPA                  (código ON;     │
│                                              efetivo com    │
│                                              OPA_GOV_API_URL)│
│  • mask /PII no gateway                     (NÃO existe)    │
│  • catálogo + JWT/scope/CIBA pdp_mcp.py     (ATIVO)         │
└─────────────────────────────────────────────────────────────┘
  │                    │                    │
  ▼                    ▼                    ▼
ai-agent          Ollama/OpenAI          user-mcp runtime
  │                                        │  JWT inbound (sem JWKS)
  │  Lua inbound (4 variantes)             │  SQL + database/creds
  │    NÃO aplicado pelo `make deploy`     │  Transform PII
  │                                        │  (sem CIBA / sem ext_authz)
```

| # | PEP | Onde aplica | Lab `make up` | Migrar para LiteLLM? |
| --- | --- | --- | --- | --- |
| A | Conteúdo do agente (prompt injection, código inseguro, PII na resposta) | Envoy Lua no **inbound de `ai-agent`** — 4 `ServiceDefaults` mutuamente exclusivos | **Não** | **Sim — PEP legado de conteúdo** |
| B | Admissão do gateway | LiteLLM `custom_auth` (`pdp_auth.py`) por SPIFFE | **Sim** | Já está no LiteLLM |
| C | Conteúdo via guardrail LiteLLM | `OpaPdpGuardrail` → `opa-gov-api /evaluate` | Código sim; depende de `OPA_GOV_API_URL` | Completar `/mask` |
| D | Catálogo MCP (quem pode chamar qual tool) | LiteLLM `pdp_mcp.py` (`pre_mcp_call`); catálogo no Vault KV; PDP `mcp.pep` | **Sim** | Já no LiteLLM |
| E | Who-may-talk-to-whom (mTLS SPIFFE) | Consul Service Intentions | **Sim** | **Não** — PEP de rede |
| G | Recurso MCP: JWT, scope, LoA/CIBA | LiteLLM `pdp_mcp.py`; user-mcp só executa | **Sim** | Já no LiteLLM |

Os quatro YAML em `deploy-k8s/service-defaults-agent-*.yaml` todos se chamam `ai-agent`. Só um pode estar aplicado. O caminho minikube **substituiu o hop `web → ai-agent` por `web → LiteLLM → ai-agent`**, mas **não portou as regras Lua**.

`ext_authz` no inbound de `user-mcp` está **desligado** (`service-defaults-user-mcp.yaml` só timeout + `protocol: http`).

---

## 2. PEP A — Envoy Lua no inbound de `ai-agent` (legado)

Contrato comum das quatro variantes:

- Listener: **inbound** do sidecar de `ai-agent`.
- Request: lê o body, decide ALLOW ou bloqueia **antes** do FastAPI.
- Response (exceto a variante inline): só em HTTP 200, reescreve o body (PII mask).
- Body vazio: skip.

### 2.1 Variante `service-defaults-agent-lua.yaml` — regex in-process

PEP e PDP no mesmo script. Sem OPA. Só request; **não mascara resposta**.

Bloqueio: HTTP **403** `Blocked: <categoria>`. Fail-closed no match.

#### Prompt injection (`injection_patterns`)

Lua patterns (não PCRE). Body em **lowercase**.

| Padrão | Intenção |
| --- | --- |
| `ignore.-instructions`, `disregard.-instructions`, `forget.-instructions`, `skip.-instructions`, `override.-instructions`, `ignore.-rules` | override de system prompt |
| `you are now`, `act as`, `persona:`, `take on the role of` | jailbreak de persona |
| `unfiltered`, `unrestricted`, `do anything now`, `dan%s?mode` | DAN / unfiltered |
| `print.-prompt`, `show.-prompt`, `reveal.-prompt`, `repeat.-prompt` | prompt leak |
| `output as code`, `output as json`, `output as base64` | format hijack |
| `###%s*instruction`, `system:`, `user:`, `assistant:`, `<\|system\|>` | spoof de delimitador |
| `the new rule is`, `from now on`, `end of previous conversation` | logic hijack |
| `translate.-and then execute` | translate-then-execute |

#### Código / path (`direct_patterns`)

| Padrão | Intenção |
| --- | --- |
| `eval(`, `exec(`, `os.system(`, `subprocess.popen/call/run(` | execução Python |
| `rm -rf`, `sudo rm/chmod/chown/dd/mkfs` | shell destrutivo |
| `curl … \| bash/sh/zsh` | pipe-to-shell |
| `/etc/passwd`, `/etc/shadow`, `../../`, `[espaço ou /].env` | arquivos / traversal |

#### Comandos restritos (`restricted_words`, word-boundary Lua `%f`)

`ls`, `cd`, `pwd`, `cat`, `mkdir`, `touch`, `whoami`, `id`, `uname`, `hostname`, `ps`, `top`, `kill`, `df`, `du`, `python`, `python3`, `node`, `perl`, `php`, `ruby`, `gcc`, `make`.

### 2.2 Variante `service-defaults-agent-opa.yaml` — Lua → OPA direto

PDP = OPA `opa-service` (ns `opa`). Timeout 5 s.

| Fase | Path OPA | Ação do PEP |
| --- | --- | --- |
| Request | `POST /v1/data/app/security` | body → base64 → `{"input":"..."}`. Qualquer campo `true` em `result` → **400** `This content was blocked due to security policy violation` |
| Response 200 | `POST /v1/data/app/masking/masked_result` | substitui o body pelo JSON cru do OPA (`{"result": …}` — envelope **não** é unwrapped) e força `content-type: application/json` |

Fail: **open** (log + continua) se OPA cair ou faltar metadata Consul. Metadata ausente também skip.

Regras efetivas = §3 (Rego).

### 2.3 Variante `service-defaults-agent-opa-gov.yaml` — Lua → `opa-gov-api`

Mesmo contrato, Lua fino:

| Fase | HTTP | Ação do PEP |
| --- | --- | --- |
| Request | `POST /evaluate` text/plain, timeout 5 s | **400** → responde 400 com o body do gov-api (ou mensagem padrão). Outros status → ALLOW |
| Response 200 | `POST /mask` | substitui body; propaga `content-type` |

Fail: **open**. Body vazio skip. `opa-gov-api` default `OPA_FAIL_MODE=open`; unwrap do envelope OPA **ligado**.

### 2.4 Variante `service-defaults-agent-wx-gov.yaml` — Lua → `wx-gov-api`

Igual ao 2.3, timeout **30 s**, body **base64**. `/evaluate` 400 bloqueia; `/mask` reescreve 200. Fail-open.

PDP watsonx.governance (§4).

---

## 3. Regras do PDP OPA (o que o PEP A/C consulta)

Fonte: `infra/config/opa_policies/`. Input sempre **base64** do texto (Lua OPA ou `opa-gov-api`).

`opa-gov-api` bloqueia se `is_injection OR is_unsafe`. PII **não** bloqueia no `/evaluate`; só mascara no `/mask`.

### 3.1 Prompt injection — `app.security.is_injection`

Regex case-insensitive (`patterns.prompt_injection_regex`):

1. `(ignore|disregard|forget|skip|override)\s+(all|previous|system)?\s*(instructions|rules|directives)`
2. `(you are now|act as|persona:|take on the role of|hypothetically speaking)`
3. `(unfiltered|unrestricted|do anything now|DAN\s?mode)`
4. `(print|show|output|reveal|repeat)\s+(the|your)?\s*(initial|system|hidden|original)?\s*(prompt|instructions|text)`
5. `(output|respond)\s+as\s+(code|json|markdown|base64|hex)`
6. `(###\s*instruction|system:|user:|assistant:|admin:|<\|system\|>)`
7. `(the new rule is|from now on|end of previous conversation|start of new session)`
8. `(translate the following and then execute|summarize and then follow)`

Walk em JSON decodificado (qualquer string) **ou** string crua.

Diferenças vs Lua inline: OPA tem `hypothetically speaking`, `admin:`, `markdown`/`hex`, `start of new session`, `summarize and then follow`, e âncoras `\s+` mais estritas.

### 3.2 Code safety — `app.security.is_unsafe`

1. `eval(`, `exec(`, `os.system(`, `subprocess.(Popen|call|run)(`
2. `rm -rf`, `sudo (rm|chmod|chown|dd|mkfs)`, `curl … | (sudo)? (bash|sh|zsh)`
3. `\b(ls|cd|pwd|cat|mkdir|touch|whoami|id|uname|hostname|ps|top|kill|df|du)\s+` — exige **espaço depois** (Lua não)
4. `\b(python|python3|node|perl|php|ruby|gcc|g\+\+|make)\b` — OPA inclui `g++`
5. `/etc/passwd`, `/etc/shadow`, `.env\b`, `\.\./\.\./`

### 3.3 PII mask — `app.masking.masked_result` (resposta, não bloqueio)

Cadeia `regex.replace` na ordem:

| Tipo | Padrão | Template |
| --- | --- | --- |
| SSN | `\d{3}-\d{2}-(\d{4})` | `` `***-**-$1` `` |
| Cartão | `(?:\d[ -]*?){9,12}(\d{4})` | `` `****-****-****-$1` `` |
| Email | user@domínio | `` `$1***`@$2 `` |
| IP | `a.b.c.d` | `` `$1.***.***.$2` `` |
| Telefone | US opcional +1 | `` `($1) ***-$3` `` |
| AWS key | `(AKIA|ASIA)[0-9A-Z]{16}` | `$1` + 16 `*` |
| API key / secret / password / token | label + 20+ chars | `$1: [REDACTED_SECRET]` |
| Log level | `DEBUG\|INFO\|WARN\|ERROR\|FATAL` | `` `[LOG_LEVEL:$1]` `` |

JSON válido → mascara a string e faz unmarshal; senão devolve string.

---

## 4. Regras do PDP watsonx (variante wx-gov)

`wx-gov-api/ai_guardrails_api.py` + `realtime_detections.py`.

**Evaluate** — métricas, threshold **0.5**. Bloqueia se qualquer filtro **exceto `pii`** estourar:

- `HarmMetric`
- `HAPMetric`
- `PromptSafetyRiskMetric(method="granite_guardian")`
- `JailbreakMetric`
- `UnethicalBehaviorMetric`

**Mask** — policy watsonx (`CUSTOM_GUARDRAIL_POLICY_ID`), PII via Guardrails Manager. Não é o Rego da §3.3.

---

## 5. PEP B + C — o que o LiteLLM já faz

Arquivos: `litellm-gateway/pdp_auth.py`, `pdp_mcp.py`, `pdp_guardrail.py`, `config.yaml`.  
Deploy: ConfigMap `litellm-gateway-config` + `deploy-k8s/litellm-gateway.yaml`. `make deploy` seta `PEP_OPA_URL` e `OPA_GOV_API_URL`.

Hops que passam pelo LiteLLM (`service-intentions.yaml`):

| Origem | Destino via LiteLLM |
| --- | --- |
| `web` | `/v1/agent/*` → `ai-agent:8000/v1/agent/*` |
| `ai-agent` | `/v1/chat/completions` → Ollama `qwen-local` (`host.minikube.internal:11434/v1`) ou `gpt-5-mini` |
| `ai-agent` | MCP nativo `/user_mcp/mcp` → `user-mcp/mcp` (`auth_type: none`; Authorization injetado pelo `mcp-pep`) |
| `litellm-api-gateway` | UI admin SSO Keycloak |

### 5.1 Admissão (`pdp_auth.user_api_key_auth`) — ATIVO

| Condição | Decisão / enforce |
| --- | --- |
| Path público (`/health`, `/ui`, `/sso`, …) | ALLOW `admit_public` (não logado no viewer) |
| `x-mesh-caller-spiffe` → `default/web` ou `default/ai-agent` | ALLOW `admit_mesh` |
| Senão | `fallthrough_litellm_auth` (SSO JWT / virtual keys / master key da UI) |

O header só é confiável com a extensão Lua do `ServiceDefaults` `litellm-gateway` (`mesh-timeouts.yaml`), que apaga qualquer cópia do cliente e copia o SPIFFE do peer mTLS.

`Authorization` **não** é a key do gateway: fica o JWT (usuário rumo ao agente, OBO rumo ao MCP).

### 5.2 Guardrail conteúdo (`OpaPdpGuardrail`, `mode: pre_call`) — no lab via Makefile

| Input | Comportamento |
| --- | --- |
| `OPA_GOV_API_URL` vazio | ALLOW sempre |
| POST `{url}/evaluate` → 200 | ALLOW |
| OPA unreachable | **DENY (fail-closed)** |

Não chama `/mask`. Não inspeciona a resposta do LLM nem o body do pass-through `/v1/agent`.

### 5.3 O que o LiteLLM ainda **não** enforce

- PII mask da **resposta do LLM** no gateway (Transform é no user-mcp, nas leituras SQL).
- As regras Lua inline do `ai-agent` (não aplicadas pelo `make deploy`).

Catálogo MCP, JWT, scope e CIBA **já** são enforce no LiteLLM (`pdp_mcp.py`).

---

## 6. PEP D + G — tools/call no LiteLLM (ATIVO); ext_authz desligado

`litellm-gateway/pdp_mcp.py` + `infra/config/opa_policies/mcp_pep.rego`.

O sidecar de `user-mcp` **não** tem `builtin/ext-authz`. O catálogo continua no Vault KV `opa-policies/mcp-authz/catalog` (o `consul-mcp-authz` ainda edita esse documento). O **opa-server** carrega o JSON como `data.rules`. O PEP pergunta:

```
POST http://opa-service.opa.svc.cluster.local/v1/data/mcp/pep/decision
input: {source, dest, tool, scope, user, groups}
```

Regra do lab: `source=default/litellm-gateway`, `dest=default/user-mcp`.

| `mcp.pep` reason | enforce do PEP |
| --- | --- |
| `allow` | `inject_obo_jwt` (LoA 1) |
| `step-up` | `await_ciba` → `inject_ciba_jwt` (LoA 2) |
| `insufficient_scope` / `catalog` / `default-deny` | `deny` |

`ciba_tools` = `{create_user, delete_user_by_email}`. Policies Vault `ciba-*` / `sys/capabilities-self` **não** são o interruptor deste lab.

O peer mTLS continua `litellm-gateway → user-mcp`.

---
| Timeout | 500 ms |
| Body | até 64 KiB, `packAsBytes: false` |
| Identidade origem | Consul metadata `namespace/service` (hoje **`default/litellm-gateway`**, não `ai-agent`) |
| Destino | SPIFFE `spiffe://…/ns/<ns>/…/svc/<svc>` |

Default: **deny**.

| Método JSON-RPC | Regra |
| --- | --- |
| `initialize`, `notifications/initialized`, `ping`, `tools/list`, `resources/list`, `prompts/list` | ALLOW se a origem tem service name Consul (discovery; intention já autenticou o peer) |
| `resources/read`, `prompts/get` | ALLOW só se o par `(src, dst)` existe no catálogo |
| `tools/call` | ALLOW só se `params.name` ∈ `catalog[src][dst].allow` |
| Qualquer outro / par desconhecido | 403, header `x-authz-reason` |

Seed real do lab (`infra/local-minikube/configure.sh`):

```json
{
  "rules": {
    "default/litellm-gateway": {
      "default/user-mcp": {
        "allow": [
          "list_all_users",
          "search_users_by_first_name",
          "update_user_by_email",
          "create_user",
          "delete_user_by_email"
        ]
      }
    }
  }
}
```

`opa-mcp-auth/vault/seed-catalog.sh` está **desatualizado**: só libera list/search/update (sem create/delete). O `configure.sh` é o que o `make up` usa.

Catálogo: Vault KV `opa-policies/mcp-authz/catalog`, hot-reload via vault-agent + OPA `--watch`. UI: `consul-mcp-authz`.

---

## 7. PEP G — `user-mcp` (runtime no lab; local = legado)

No `make up` o modo é `USER_MCP_PEP_MODE=runtime` + `USER_MCP_DB_AUTH_MODE=vault`. JWT, catálogo, scope e CIBA já saíram no LiteLLM (`pdp_mcp.py`). O processo só:

```
JwtAuthMiddleware (decode unverified)  → jwt_identity_bound
  → tool dispatcher (sem require_scopes / sem CIBA)
  → Vault OAuth Resource Server (JWT como X-Vault-Token)
        ACL humana ∩ ceiling do Agent Registry
        → database/creds/{read|write}-role
  → Postgres; lease revogado no finally
  → leituras: Transform mask se groups ∌ admin
```

As subsecções 7.1–7.3 abaixo descrevem **`USER_MCP_PEP_MODE=local`** (este processo como PEP). Policies Vault `ciba-*` / `sys/capabilities-self` **não** são o interruptor do lab.

Vault **não** chama o MCP de volta. Se `/creds` falhar, o user-mcp devolve 401/403/502.

Código: `user-mcp/auth/jwt_validator.py`, `auth/scope_check.py`, `storage/postgres_repo.py`, `vault_client.py`.
Policies: `infra/local-minikube/keycloak.sh` (CIBA, Transform, ORS, entities) e `configure.sh` (database roles).

### 7.1 JWT no inbound (enforcement local, PDP = Keycloak JWKS)

Fail-closed. Sem Bearer válido o ASGI responde **401** e a tool nem corre.

| Check | Valor |
| --- | --- |
| Alg | RS256 |
| `aud` | `user-mcp` |
| `iss` | realm `demo` (`USER_MCP_KEYCLOAK_BASE_URL` / `USER_MCP_ISSUER`) |
| obrigatório | `exp`, `iat`, `aud`, `iss` |
| leeway | 30 s |
| identidade extraída | `preferred_username`, `sub`, `scope`/`scp`, `groups`, `act.agent_id` |

Exceções: `USER_MCP_BYPASS_AUTH=true` (dev; **incompatível** com `DB_AUTH_MODE=vault`). `USER_MCP_ALLOW_UNAUTH_DISCOVERY=true` deixa `tools/list` sem Bearer (identity anonymous, scope vazio); `tools/call` ainda cai no 7.2.

### 7.2 Tool → scope (enforcement local)

Registry em `tools/users.py` (`TOOL_SCOPE_REQUIREMENTS`). Tool não registada = deny.

| Tool | Scope obrigatório |
| --- | --- |
| `list_all_users`, `search_users_by_first_name` | `users.read` |
| `create_user`, `update_user_by_email`, `delete_user_by_email` | `users.write` |

Falta de scope → **403** `insufficient_scope`. `_select_vault_targets` repete o check antes de pedir `database/creds` (write exige `users.write`; read aceita `users.read` ou `users.write`).

Gate extra **antes** do MCP, no broker: `token-exchange/keycloak/authorization.py` (`users.read` → reader\|writer\|admin; `users.write` → writer\|admin). Não é Vault; é PEP do broker.

### 7.3 Interruptor CIBA / LoA — policy Vault, PEP `user-mcp`

Fluxo (`postgres_repo._jwt_for_vault` + `vault_client.ciba_required_by_policy`):

1. `POST auth/jwt-keycloak/login` com o OBO e o role `user-mcp-oidc-read` ou `user-mcp-oidc-write` → client token Vault (TTL 300 s, max 900 s). **Só** para o probe.
2. `POST sys/capabilities-self` path `ciba/<action>/<preferred_username>`.
3. Interpretação no Python: `read` ∈ caps e `deny` ∉ caps → **CIBA obrigatório**. Caso contrário, OBO silencioso.
4. Se CIBA: `ciba_client.fetch_access_token` (Keycloak), poll até Approve/Deny/timeout (~110 s). Approve → JWT CIBA vira o `X-Vault-Token` do passo 7.4. Deny/timeout → 403, a tool não toca o Postgres.

Roles JWT (`auth/jwt-keycloak`):

| Role | `bound_claims` | Policy anexada |
| --- | --- | --- |
| `user-mcp-oidc-read` | `aud=user-mcp`, `groups` ∈ reader\|writer\|admin, `scope` glob `*users.read*` | `ciba-list-users` |
| `user-mcp-oidc-write` | `aud=user-mcp`, `groups` ∈ writer\|admin, `scope` glob `*users.write*` | `ciba-write` |

Policy `ciba-list-users` (leituras = OBO silencioso):

| Path | Capability | Efeito no PEP |
| --- | --- | --- |
| `ciba/list_all_users/{user,writer,admin}` | `deny` | LoA 1, sem HITL |
| `ciba/search_users_by_first_name/{user,writer,admin}` | `deny` | LoA 1, sem HITL |
| `sys/capabilities-self` | `update` | permite o probe |

Policy `ciba-write`:

| Path | Capability | Efeito no PEP |
| --- | --- | --- |
| `ciba/create_user/{writer,admin}` | `read` | LoA 2, bloqueia até Approve |
| `ciba/delete_user_by_email/{writer,admin}` | `read` | LoA 2, bloqueia até Approve |
| `ciba/update_user_by_email/{writer,admin}` | `deny` | LoA 1, update silencioso |
| `ciba/sensitive/{writer,admin}` | `read` | **não é probed hoje** — `rar_check.py` não existe; path reservada |
| `sys/capabilities-self` | `update` | permite o probe |

Não há path `ciba/.../user` em `ciba-write`: o role write nem autentica `groups=reader`. `reader` sem `users.write` já morre no 7.2.

Para desligar HITL em `create_user`: `vault policy write ciba-write` com `deny` nessa path; o próximo login JWT pega a policy nova.

Log `event=pdp_decision`: `STEP_UP_REQUIRED` / `ALLOW` LoA 2 / `DENY` / `EXPIRED`. ALLOW LoA 1 (leitura) **não** emite essa linha de propósito.

Scope pedido no CIBA: writes → `openid users.write`; resto → `openid users.read`. Binding message = nome da tool. Canal humano: `ciba-channel` `:8082`.

### 7.4 Credencial Postgres — policy Vault + OAuth Resource Server, PEP pede e recusa

Depois do CIBA (ou OBO silencioso) o user-mcp **não** usa o client token do jwt-keycloak no banco. Apresenta o JWT Keycloak (OBO ou CIBA) como `X-Vault-Token` em:

- `GET database/creds/user-mcp-read-role` ou `…/user-mcp-write-role`
- `POST transform/encode/user-mcp-transform`
- `POST sys/leases/revoke`

Vault 2.1 OAuth Resource Server (`sys/config/oauth-resource-server/keycloak-demo`):

| Campo | Valor |
| --- | --- |
| issuer | realm Keycloak `demo` |
| JWKS | Keycloak in-cluster |
| `audiences` | `user-mcp` |
| `user_claim` | `sub` |
| `jwt_type` | `access_token` |
| `optional_authorization_details` | `false` (RAR obrigatório — SPI `keycloak-providers`) |

Decisão Vault = ACL da **entity humana** ∩ **ceiling** do Agent Registry (`ai-agent` → `user-mcp-agentic-read` + `user-mcp-agentic-write`).

| Entity | Policies |
| --- | --- |
| `user-user` (login `user`) | `user-mcp-agentic-read` |
| `user-writer` | read + write |
| `user-admin` | read + write |
| `ai-agent-agentic` (ceiling do agente) | read + write |

`user-mcp-agentic-read`: `read` em `database/creds/user-mcp-read-role`; `create,update` em `transform/encode/user-mcp-transform`; `update` em `sys/leases/revoke`.

`user-mcp-agentic-write`: o mesmo em `database/creds/user-mcp-write-role` + Transform + revoke.

SQL que o Vault aplica (TTL 1 h, max 24 h):

| Role | GRANT |
| --- | --- |
| `user-mcp-read-role` | `CONNECT`, `USAGE` schema, `SELECT` em `users` |
| `user-mcp-write-role` | `SELECT, INSERT, UPDATE, DELETE` em `users` |

403/400 do Vault → user-mcp responde **403** `invalid_request`. Sem JWT no context → **401**. Falha de transporte → **502**. Lease revogado no `finally`.

### 7.5 PII por grupo — Transform no Vault, quem chama é o user-mcp

Só nas **leituras** (`list_all`, `search_by_first_name`). Writes não mascaram.

| Caller `groups` | Enforcement |
| --- | --- |
| contém `admin` | plaintext; Transform **não** é chamado |
| qualquer outro (incl. `writer`, `reader`, sem groups) | `POST transform/encode/user-mcp-transform` por campo |
| sem OBO / sem Vault client | skip (não mascara) |
| Transform falha num campo | warning + campo original (fail-open **por campo**) |

| Campo | Transformation | Template regex (shape do seed) |
| --- | --- | --- |
| `ssn` | `user-mcp-ssn` | `(\d{3})-(\d{2})-(\d{4})` |
| `credit_card_number` | `user-mcp-credit-card` | `(\d{4})-(\d{4})-(\d{4})-(\d{4})` |
| `phone` | `user-mcp-phone` | `\+1-(\d{3})-(\d{3})-(\d{4})` |
| `ip_address` | `user-mcp-ip-address` | `(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})` |

Masking one-way, carácter `*`. Independente do mask regex OPA da §3.3 (esse é no body do agente; este é no record MCP).

### 7.6 Alvo: PEP G sobe para o LiteLLM; Vault continua PDP

Implementado nesta fatia: LiteLLM `pdp_mcp.py` (`mode: pre_mcp_call`) valida JWT, pergunta `POST /v1/data/mcp/pep/decision` no **opa-server** (catálogo + scope + CIBA) e faz poll Keycloak se `ciba_required`. user-mcp com `USER_MCP_PEP_MODE=runtime` só executa SQL e pede `database/creds` / Transform com o JWT que o gateway já autorizou. `ext_authz` no inbound de user-mcp foi removido.

---

## 8. Fora do escopo do PEP de IA (não migrar para o LiteLLM)

Ficam atrás do gateway / na malha. O LiteLLM não deve reimplementá-los.

### 8.1 PEP de rede — Service Intentions (default deny)

App (`deploy-k8s/service-intentions.yaml`):

| Destino | Origens allow |
| --- | --- |
| `ai-agent` | `litellm-gateway` |
| `litellm-gateway` | `ai-agent`, `web`, `litellm-api-gateway` |
| `user-mcp` | `litellm-gateway`, `consul-mcp-authz` |
| `token-exchange` | `ai-agent` |
| `opa-gov-api` | `ai-agent`, `litellm-gateway` |
| `opa-service` (ns `opa`) | `ai-agent`, `opa-gov-api` |
| `opa-mcp-authz` | `user-mcp` |
| `wx-gov-api` | `ai-agent` |
| `web` | `web-api-gateway` |
| `keycloak` | gateway, `token-exchange`, `user-mcp`, `litellm-gateway`, `web`, `ciba-channel` |
| `ciba-channel` | `keycloak`, `ciba-channel-gateway` |

Infra (`infra/local-minikube/mesh-vault-postgres.yaml`):

| Destino | Origens allow |
| --- | --- |
| `postgres` | `vault`, `keycloak`, `user-mcp`, `litellm-gateway` |
| `vault` (ns `vault`) | `ai-agent`, `user-mcp`, `consul-mcp-authz`, `opa-service`, `opa-mcp-authz` |

Vault: mTLS **permissive** (NodePort / probes). Postgres: strict.

Token-exchange (PEP do broker, policies hardcoded, não Vault): ver §7.2.

---

## 9. Checklist de migração (Consul API GW → LiteLLM PEP → Vault PDP)

Objetivo: Consul permanece na borda; LiteLLM é o PEP de IA; **OPA `mcp.pep`** é o PDP de tools/call; Vault fica com segredos; user-mcp perde JWT/scope/CIBA/ext_authz no modo runtime.

1. **Borda** — Consul API Gateway já é o norte-sul; não mudar o papel.
2. **Conteúdo no LiteLLM** — `OPA_GOV_API_URL` + deploy `opa-gov-api` (bundle no Vault); portar `/mask`. Fail-closed.
3. **Catálogo no LiteLLM** — feito (`pdp_mcp.py` lê `opa-policies/mcp-authz/catalog`; `ext_authz` desligado).
4. **LoA/CIBA no LiteLLM** — feito (OPA `mcp.pep` + poll Keycloak; JWT CIBA no runtime).
5. **JWT + tool→scope no LiteLLM** — feito (`USER_MCP_PEP_MODE=runtime`).
6. **Não mover para o LiteLLM** — Service Intentions, `database/creds` mint, Transform encode, token-exchange OBO no ai-agent.
7. **Desligar legado A** — nenhum `service-defaults-agent-*.yaml`.
