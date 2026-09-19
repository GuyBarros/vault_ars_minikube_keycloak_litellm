# Hop viewer (vault-log)

Mini aplicação (HTML/CSS/JS, sem build) para UAT do lab: hops com **PEP enforce** e **política PDP**, timeline por `request_id`, auditoria Vault/OBO.

Arquitetura: [`documentation/arquitetura-detalhada.md`](../documentation/arquitetura-detalhada.md).

## Ao vivo (caminho do lab)

```bash
cd infra/local-minikube
make hop-logs
```

Abre http://127.0.0.1:8753/. `serve.py` segue `kubectl logs -f` (web, ai-agent, token-exchange, litellm-gateway, user-mcp, ciba-channel, opa, vault) e empurra SSE em `/stream`. Use o canal em http://localhost:8080 ao mesmo tempo — não precisa de `collect-cluster.sh`.

Três abas no **mesmo** stream:

| Aba | Conteúdo |
| --- | --- |
| Fluxo / hops | Cada hop; bloco `pep` / `pdp` / `package` / `reason` / scopes |
| Timeline por requisição | Eventos correlacionados por `request_id` |
| Auditoria Vault / OBO | Mint OBO, `database/creds`, Transform, `pdp_decision` |

Eventos úteis: `pdp_decision`, `jwt_identity_bound`, `vault_db_creds_issued`, `transform_encode`, `tool_invoked`, `ciba_started`, `ciba_approved`. CIBA só aparece em `create_user` / `delete_user_by_email`.

`python3 -m http.server` **não** faz live: só serve arquivos estáticos. Dump opcional: `./collect-cluster.sh`.

## Arquivos

| Arquivo | Papel |
| --- | --- |
| `serve.py` | HTTP + SSE + `kubectl logs -f` |
| `app.js` | Parse JSON/kv, três abas, scroll live |
| `index.html` / `styles.css` | UI |
| `collect-cluster.sh` | Snapshot estático |
