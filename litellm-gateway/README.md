# LiteLLM — PEP + AI Gateway

No laboratório minikube este diretório **não** é um serviço com Dockerfile. O `make deploy` copia os arquivos para o ConfigMap `litellm-gateway-config`, montado no container da imagem oficial LiteLLM.

Arquitetura e papéis: [`documentation/arquitetura-detalhada.md`](../documentation/arquitetura-detalhada.md). Fluxo hop a hop: [`documentation/fluxo-e-responsabilidades.md`](../documentation/fluxo-e-responsabilidades.md).

## Responsabilidade

- Admitir chamadores da malha por SPIFFE (`default/web`, `default/ai-agent`).
- Proxy `/v1/agent/*` → `ai-agent`, `/v1/chat/completions` → Ollama/OpenAI, MCP `/user_mcp/mcp` → `user-mcp`.
- Em `tools/call`: validar JWT Keycloak, perguntar ao OPA `mcp.pep`, se `ciba_required` poll Keycloak, injetar OBO ou JWT CIBA em `Authorization`.

Não guarda ACL, não executa SQL, não minta credencial Postgres.

O YAML nativo do LiteLLM (keys, allowlist, OBO RFC 8693) **não** cobre catálogo OPA + CIBA + SPIFFE. Por isso `custom_auth` + CustomGuardrail.

## Arquivos

| Arquivo | Papel |
| --- | --- |
| `config.yaml` | Modelos, guardrails, MCP server `user_mcp` (`auth_type: none`) |
| `pdp_auth.py` | Admissão SPIFFE / fallthrough SSO |
| `pdp_mcp.py` | PEP `pre_mcp_call` |
| `pdp_guardrail.py` | Conteúdo → `opa-gov-api/evaluate` |
| `test_pdp_auth.py` / `test_pdp_mcp.py` | Testes |

Ollama: alias `qwen-local` usa provider `openai/` contra `http://host.minikube.internal:11434/v1` (não `ollama/` e não `host.containers.internal`).

Audit no hop viewer: JSON em stdout (`pdp_decision`). Não usar `logging.getLogger("litellm-gateway.*")` — a imagem engole esses loggers.
