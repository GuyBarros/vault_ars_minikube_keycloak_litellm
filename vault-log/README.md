# Vault Agent Timeline

Mini aplicação (HTML/CSS/JS puro, **sem build, sem instalação**) que mostra, em
formato de **timeline** e de **auditoria**, os logs de um agente de IA que usa o
**HashiCorp Vault Identity Broker** para troca de tokens **OBO** (on-behalf-of) e
invocação de ferramentas **MCP** com escopo.

---

## 1. Como abrir

Os navegadores bloqueiam abrir `index.html` direto pelo `file://`, então sirva a
pasta por HTTP local (qualquer uma das opções abaixo).

**Opção A — Python (já vem no macOS/Linux):**

```bash
cd vault-log-ars
python3 -m http.server 8753
```

Depois abra: <http://localhost:8753/index.html>

**Opção B — Node (se tiver instalado):**

```bash
cd vault-log-ars
npx serve -l 8753
```

Para parar o servidor: `Ctrl + C` no terminal.

> Os logs de exemplo já vêm embutidos — ao abrir, a timeline aparece pronta.

---

## 2. As duas visões

No topo há duas abas:

### Timeline por requisição
Cada requisição do agente (`request_id`) vira um card com os eventos em ordem
cronológica e o tempo decorrido (`+Xs`) entre cada passo:

1. `agent_request_started` — requisição recebida
2. `identity_broker_call` — chamada ao Identity Broker do Vault
3. `obo_token_exchange_completed` — token OBO emitido **ou**
   `scoped_tool_token_exchange_failed` — troca de token negada
4. `scoped_tool_invoke` — ferramenta MCP invocada com o token/escopo
5. `response_sent` — resposta enviada ao usuário

Cada card recebe um selo de **resultado no Vault**:

| Selo | Significado |
|---|---|
| **Token emitido** | passou pelo broker e o Vault gerou um token OBO novo |
| **Token reutilizado** | a ferramenta foi chamada **sem** ir ao broker (token de cache) |
| **Acesso negado** | o Vault recusou a troca de token |

### Auditoria de acessos ao Vault
Responde "**quem** acessou o Vault para obter token/credencial e **quando**", na
menor granularidade possível:

- **Tabela "Quem acessou o Vault"** — por identidade: nº de requisições, tokens
  emitidos, reutilizados de cache e negados.
- **Feed cronológico** com um evento por linha: cada solicitação de troca, cada
  token emitido (com expiração), cada negação (com a mensagem de erro), cada uso
  de token em ferramenta e cada consulta de token do agente — com identidade,
  escopo, ferramenta, `request_id` e URL do broker.

### Recursos comuns
- **Cards de resumo** focados em Vault (chamadas ao broker, emitidos,
  reutilizados, negados, consultas de token).
- **Filtro por usuário** e **busca** livre.
- **Mascarar dados sensíveis** (ligado por padrão): SSN, cartão, telefone, IP e
  e-mail ficam mascarados; ao desligar, o dado exposto aparece destacado em
  vermelho.
- **JSON bruto** expansível em cada evento.

---

## 3. Atualizando com novos logs

Há duas formas. A **forma rápida** não exige nada além do navegador.

### Forma rápida (sem terminal) — para uma visualização pontual
1. Abra a aplicação no navegador.
2. Clique em **Carregar logs** (canto superior direito).
3. **Cole** o texto do log **ou** clique em **Selecionar arquivo…** e escolha um
   arquivo `.txt`, `.log` ou `.jsonl`.
4. Clique em **Visualizar timeline**.

> Observação: o upload por arquivo aceita texto puro. Se o log estiver em `.rtf`,
> use a forma definitiva abaixo (ou cole o conteúdo já em texto).

### Forma definitiva — para deixar os novos logs embutidos no projeto
Use o script `build-data.js` (precisa do **Node.js** instalado). Ele aceita
`.rtf` (export do Terminal do macOS) **ou** texto puro, limpa o ruído de infra
(Envoy/consul-dataplane) e regenera os dados embutidos:

```bash
node build-data.js /caminho/para/o/novo-log.rtf
```

Isso atualiza dois arquivos: `sample-logs.txt` e `logs-data.js`. Depois é só
**recarregar o index.html** no navegador — o novo conteúdo já aparece como padrão.

Funciona igual com texto puro:

```bash
node build-data.js /caminho/para/o/novo-log.txt
```

---

## 4. Formatos de log aceitos

A aplicação entende as linhas que aparecem nos logs do agente:

1. **JSON estruturado** (um objeto por linha):
   `{"event": "agent_request_started", "request_id": "...", "message": "[user=...]", ...}`
2. **Acesso HTTP (uvicorn):**
   `INFO:     127.0.0.1:57370 - "POST /v1/agent/query HTTP/1.1" 200 OK`

As linhas podem ter (ou não) um timestamp de container no início — ambos os casos
funcionam. Linhas de infraestrutura (Envoy/consul-dataplane) são ignoradas.

> O arquivo `token_exchange_log.rtf` recebido é majoritariamente log de infra do
> sidecar Envoy do serviço `token-exchange` e **não** contém eventos de token por
> usuário; a rastreabilidade "quem acessou o Vault" vem do log do agente
> (`log_ai_agent.rtf`).

---

## 5. Arquivos do projeto

| Arquivo | Descrição |
|---|---|
| `index.html` | Estrutura da página |
| `styles.css` | Tema escuro e estilos |
| `app.js` | Parser dos logs, agrupamento, máscara de PII e renderização |
| `logs-data.js` | Logs embutidos (gerado por `build-data.js`) |
| `sample-logs.txt` | Os mesmos logs em texto puro |
| `build-data.js` | Script para regenerar os dados a partir de um `.rtf`/`.txt` |
| `README.md` | Este arquivo |
