#!/usr/bin/env python3
"""Build Telefônica/Vivo demonstration minutes and APIM architecture docs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/guybarros/GIT_ROOT/ai-iam-guardrails/documentation/telefonica-vivo-homologacao")
SHOT = ROOT / "screenshots"
ATA = ROOT / "Ata_Demonstracao_Caderno_Testes_IA.docx"
APIM = ROOT / "Arquitetura_Azure_APIM_AI_Gateway.docx"


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True)
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stdout + "\n" + proc.stderr)
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(args[:6])}")
    return proc


def batch(file: Path, commands: list[dict]) -> None:
    payload = json.dumps(commands, ensure_ascii=False)
    last_err = ""
    for attempt in range(3):
        proc = subprocess.run(
            ["officecli", "batch", str(file), "--json"],
            input=payload,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            print(proc.stdout.strip()[-400:] if proc.stdout else "batch ok")
            return
        last_err = (proc.stdout or "") + "\n" + (proc.stderr or "")
        print(f"batch attempt {attempt + 1} failed, retrying...")
    sys.stderr.write(last_err)
    raise SystemExit("batch failed")


def setup_doc(file: Path, *, title: str, header: str, footer: str) -> None:
    if file.exists():
        subprocess.run(["officecli", "close", str(file)], capture_output=True, text=True)
        file.unlink()
    run(["officecli", "create", str(file)])
    run(["officecli", "open", str(file)])
    batch(
        file,
        [
            {
                "command": "set",
                "path": "/",
                "props": {
                    "title": title,
                    "author": "Guy Barros",
                    "docDefaults.font": "Calibri",
                    "docDefaults.fontSize": "11pt",
                },
            },
            {
                "command": "add",
                "parent": "/",
                "type": "header",
                "props": {"text": header, "size": "9pt", "color": "525252"},
            },
            {
                "command": "add",
                "parent": "/",
                "type": "footer",
                "props": {"text": footer, "size": "9pt"},
            },
        ],
    )


class Doc:
    def __init__(self) -> None:
        self.cmds: list[dict] = []
        self.p = 0

    def add(self, parent: str, typ: str, **props) -> None:
        self.cmds.append({"command": "add", "parent": parent, "type": typ, "props": props})

    def para(self, text: str = "", **props) -> int:
        if text:
            props["text"] = text
        self.add("/body", "paragraph", **props)
        self.p += 1
        return self.p

    def h1(self, text: str) -> int:
        return self.para(text, bold="true", size="20pt", color="0F62FE", spaceBefore="18pt", spaceAfter="8pt")

    def h2(self, text: str) -> int:
        return self.para(text, bold="true", size="14pt", color="161616", spaceBefore="14pt", spaceAfter="6pt")

    def h3(self, text: str) -> int:
        return self.para(text, bold="true", size="12pt", color="161616", spaceBefore="10pt", spaceAfter="4pt")

    def body(self, text: str) -> int:
        return self.para(text, style="Normal", spaceAfter="8pt")

    def caption(self, text: str) -> int:
        return self.para(text, italic=trueish(), size="9pt", color="525252", spaceBefore="4pt", spaceAfter="10pt")

    def picture(self, parent_p: int, src: Path, alt: str, width: str = "16cm") -> None:
        self.add(
            f"/body/p[{parent_p}]",
            "picture",
            src=str(src),
            alt=alt,
            width=width,
        )

    def table(self, rows: list[list[str]], col_widths: str, caption: str) -> None:
        props: dict = {
            "cols": len(rows[0]),
            "rows": len(rows),
            "style": "light1",
            "firstRow": "true",
            "layout": "fixed",
            "colWidths": col_widths,
            "caption": caption,
        }
        for r_i, row in enumerate(rows, 1):
            for c_i, cell in enumerate(row, 1):
                props[f"r{r_i}c{c_i}"] = cell
        self.add("/body", "table", **props)


def trueish() -> str:
    return "true"


def build_ata() -> None:
    setup_doc(
        ATA,
        title="Ata de Demonstração — Caderno de Testes de Segurança de Workloads de IA",
        header="Ata de Demonstração — Workloads de IA — Telefônica/Vivo",
        footer="Confidencial — uso interno da demonstração  ·  ",
    )

    d = Doc()
    d.para(
        "Ata de Demonstração e Homologação",
        bold="true",
        size="26pt",
        color="0F62FE",
        spaceAfter="6pt",
    )
    d.para(
        "Caderno de Testes de Segurança de Arquitetura — Workloads de IA",
        size="16pt",
        color="161616",
        spaceAfter="12pt",
    )
    d.body(
        "PEP / PDP / mTLS / LoA  ·  16 de setembro de 2026  ·  Ambiente local minikube (Consul + Vault + Keycloak)"
    )
    d.body(
        "Participantes da evidência: Guy Barros (HashiCorp). Artefatos de referência: Caderno_de_Testes_de_Segurança_-_Workloads_de_IA.pdf e o diagrama de arquitetura Telefônica/Vivo (Azure API Management / AI Gateway + Ping Authorize)."
    )
    d.body(
        "As capturas de tela foram obtidas das mesmas URLs que estavam abertas no navegador Arc (AI Runtime Security em http://localhost:8080/landing, CIBA approval em http://localhost:8082/, Consul Intentions em https://localhost:8501/ui/dc1/intentions). A sessão autenticada usada nas evidências de JWT é o usuário admin do realm Keycloak demo. A seção 7 registra o ensaio ponta a ponta executado ao vivo nesta sessão (list_all_users LoA 1 → create_user CIBA LoA 2)."
    )

    d.h1("1. Objetivo")
    d.body(
        "Registrar, caso a caso, como o runtime agentic deste repositório atende o caderno de testes Telefônica/Vivo. O caderno homologa autenticação OAuth 2.0/JWT com LoA, identidade de workload em mTLS, decisões ALLOW/DENY do PDP, step-up de autenticação e auditoria fail-closed. Esta ata não substitui o caderno: ela cruza cada CT com o controle equivalente já implantado no laboratório."
    )

    d.h1("2. Mapeamento de arquitetura")
    d.body(
        "O diagrama do cliente posiciona o Azure API Management como PEP (AI Gateway) e o Ping Authorize como PDP. O laboratório demonstra o mesmo desenho de controle com produtos HashiCorp/Keycloak: o Consul API Gateway + sidecars Envoy são o PEP; OPA (opa-mcp-authz / opa-gov-api) e as ACL Vault (interruptor CIBA) são o PDP; o Keycloak é o IdP; o Vault é o cofre de senhas que emite credenciais de banco sob o JWT OBO/CIBA."
    )
    cap = d.para()
    d.picture(cap, SHOT / "00-diagrama-telefonica-vivo.jpg", "Diagrama de arquitetura Telefônica/Vivo com Azure APIM e Ping Authorize", "16.5cm")
    d.caption("Figura 1 — Arquitetura de referência Telefônica/Vivo (PEP = Azure APIM / AI Gateway; PDP = Ping Authorize).")
    d.body(
        "O diagrama seguinte replica a mesma topologia no laboratório: o canal continua à esquerda, o PEP no centro, o PDP à direita, IdP em cima, agente/LLM acima do PEP, MCP e cofre embaixo. Os nomes de produto mudam; o desenho de controle não."
    )
    cap = d.para()
    d.picture(cap, SHOT / "00-demo-equivalencia-vivo.png", "Diagrama de equivalência do laboratório de demo com Consul, OPA, Vault e Keycloak", "16.5cm")
    d.caption("Figura 2 — Ambiente de demo na mesma topologia: Consul API Gateway + Envoy = PEP; OPA + ACL Vault CIBA = PDP; Keycloak = IdP; CIBA :8082 = step-up LoA 2.")

    d.table(
        [
            ["Bloco no diagrama", "Papel no caderno", "Equivalente no laboratório"],
            ["Cliente + Canal / Agente de Atendimento", "Inicia o Authorization Code e transporta o JWT", "web-app (Next.js) em http://localhost:8080"],
            ["Provedor de Identidades (IdP)", "Emite JWT com LoA; executa step-up", "Keycloak realm demo (http://localhost:8081)"],
            ["Azure API Management / AI Gateway", "PEP: mTLS, JWT, consulta PDP", "Consul API Gateway (web-api-gateway) + Envoy sidecars"],
            ["Ping Authorize", "PDP: ALLOW / DENY / STEP_UP", "OPA (opa-mcp-authz, opa-gov-api) + ACL Vault ciba/<ação>/<usuário>"],
            ["Agente de IA + LLM", "Workload com certificado cliente", "ai-agent + LiteLLM gateway, identidade SPIFFE no mesh"],
            ["Servidor MCP", "Ferramentas do agente (APIs / dados)", "user-mcp (FastMCP)"],
            ["Cofre de Senhas + Banco", "Credenciais efêmeras após autorização", "Vault OAuth Resource Server + Postgres"],
            ["API Sistemas", "Destino com JWT na chamada", "Ferramentas MCP e APIs internas via mesh"],
        ],
        "2800,2800,3800",
        "Mapeamento dos blocos do diagrama Telefônica para o laboratório",
    )
    d.body(
        "LoA no laboratório é auto-gerenciado pelo PDP de aplicação (user-mcp/loa.py), não uma claim loa/acr emitida pelo IdP. LoA 1 = OBO silencioso. LoA 2 = JWT CIBA após aprovação humana. Não há LoA 3: só existe um mecanismo de elevação (CIBA), portanto só um nível elevado é honesto de reivindicar."
    )

    d.h1("3. Ambiente da demonstração")
    d.body(
        "Cluster local-minikube-demo no momento da ata: web, web-api-gateway, ai-agent, user-mcp, token-exchange, keycloak, ciba-channel, litellm-gateway, opa-mcp-authz, postgres — todos Running. Front door do canal: Consul API Gateway publicado em localhost:8080 (NodePort 30080). IdP: localhost:8081. Canal CIBA: localhost:8082. Consul UI: https://localhost:8501."
    )
    cap = d.para()
    d.picture(cap, SHOT / "01-login-web-app.png", "Tela de login da web-app AI Runtime Security", "16.5cm")
    d.caption("Figura 3 — Canal (web-app): Authorization Code Flow via “Login with Keycloak” (CT-01.1).")

    d.h1("4. Matriz de cobertura")
    d.table(
        [
            ["CT", "Objetivo do caderno", "Status", "Como atendemos"],
            ["01.1", "Login senha, JWT com LoA=1", "Equivalência", "Keycloak password + JWT; LoA 1 no PDP (OBO silencioso), não na claim do IdP"],
            ["01.2", "MFA, JWT com LoA=2", "Equivalência", "Elevação CIBA (não TOTP no login) → LoA 2 no PDP e no inspector"],
            ["01.3", "Biometria / FIDO2, LoA=3", "Gap", "Não implementado — só há um degrau de elevação"],
            ["01.4", "Credenciais inválidas, sem JWT", "Atendido", "Keycloak recusa; tela “Invalid username or password”"],
            ["01.5", "PEP recusa JWT forjado", "Atendido (código)", "Validação JWKS em user-mcp e token-exchange; 401 antes do PDP de negócio"],
            ["02.1", "mTLS + extração de CN", "Equivalência", "Consul Connect mTLS; identidade SPIFFE do serviço, não CN X.509 corporativo"],
            ["02.2", "CN não autorizado", "Equivalência", "Service intentions: só origens listadas falam com o destino"],
            ["02.3", "Certificado expirado/revogado", "Parcial", "CA do Consul rotaciona; não houve demo de CRL/OCSP nesta sessão"],
            ["02.4", "Bypass do PEP", "Atendido", "web só aceita web-api-gateway; ai-agent só aceita web"],
            ["03.1", "ALLOW ponta a ponta", "Atendido", "create_user após CIBA: PDP_Decision=ALLOW, LoA 2, tool_invoked"],
            ["03.2", "PDP < 15 ms / 100 RPS", "Não demonstrado", "OPA é local no mesh; carga de 100 req não foi executada"],
            ["04.1", "DENY por perfil", "Atendido", "Grupo user sem users.write; catálogo opa-mcp-authz; escopos OBO"],
            ["04.2", "JWT expirado no fluxo", "Atendido (código)", "exp no JWT; validadores rejeitam; TTL curto do OBO (300s)"],
            ["04.3", "DENY ação entre agentes", "Equivalência", "ext_authz + catálogo (source-agent, MCP, tool); CIBA em delete"],
            ["05.1", "STEP_UP_REQUIRED", "Atendido", "ACL Vault ciba/create_user/admin dispara CIBA; log pdp_decision"],
            ["05.2", "Step-up bem-sucedido", "Equivalência", "Aprovação CIBA no canal http://localhost:8082 (não biometria)"],
            ["05.3", "Reenvio após step-up", "Equivalência", "A ferramenta bloqueia até o approve e então grava — um único call"],
            ["05.4", "Cancelamento do step-up", "Atendido (código)", "Deny no canal CIBA → access_denied → PDP DENY; write não ocorre"],
            ["06.1", "Log de auditoria JSON", "Equivalência", "pdp_decision com TransactionID=request_id, UserID, LoA, PDP_Decision"],
            ["06.2", "Fail-closed se PDP cair", "Atendido", "Envoy ext_authz statusOnError=403; Lua do opa-gov-api fail-closed"],
        ],
        "1100,2600,1800,3900",
        "Matriz de rastreabilidade CT × laboratório",
    )

    d.h1("5. Evidências por categoria")

    d.h2("5.1 Categoria 01 — Autenticação e JWT (IdP)")
    d.h3("CT-01.1 Autenticação com fator único (LoA 1)")
    d.body(
        "O canal inicia Authorization Code + PKCE (client_id=web, scope openid profile email Agent.Invoke) contra o realm demo. Após senha válida o callback entrega a sessão e o Identity inspector mostra o subject token: iss=http://localhost:8081/realms/demo, preferred_username=admin, groups=admin e user, exp/iat, azp=web. Isso cumpre RFC 6749/6750. A claim loa/acr pedida pelo caderno não é emitida pelo Keycloak nesta demo; o LoA 1 aparece depois, no PDP, como “silent OBO” quando a ferramenta não exige CIBA."
    )
    cap = d.para()
    d.picture(cap, SHOT / "02-keycloak-login-form.png", "Formulário Keycloak Sign in to demo", "16.5cm")
    d.caption("Figura 4 — IdP Keycloak (realm DEMO): Authorization Code em andamento.")
    cap = d.para()
    d.picture(cap, SHOT / "04-landing-admin-subject-jwt.png", "Landing autenticada com inspector do subject JWT", "16.5cm")
    d.caption("Figura 5 — CT-01.1: JWT de sujeito emitido, grupos admin/user, inspector no canal.")

    d.h3("CT-01.2 MFA / LoA 2")
    d.body(
        "Não há TOTP no login. O segundo fator é CIBA (push no canal de aprovação) quando a política Vault exige step-up para a ação. Após approve, o PDP registra LoA_Level=2 e o inspector de Assurance passa a mostrar “LoA 2 — Elevated (CIBA-approved)”. Equivalente funcional ao LoA 2 do caderno, com mecanismo diferente do OTP."
    )
    d.h3("CT-01.3 LoA 3 / FIDO2")
    d.body(
        "Gap consciente. O código documenta que não existe LOA=3 porque só há um mecanismo de elevação. WebAuthn no Keycloak seria o caminho natural se o IdP corporativo exigir três degraus."
    )
    d.h3("CT-01.4 Credenciais inválidas")
    d.body(
        "Senha incorreta no IdP: a UI retorna “Invalid username or password”, o fluxo OAuth não completa e nenhum JWT chega ao canal. Evidência de falha esperada."
    )
    cap = d.para()
    d.picture(cap, SHOT / "03-keycloak-invalid-credentials.png", "Keycloak recusando senha inválida", "16.5cm")
    d.caption("Figura 6 — CT-01.4: IdP recusa a autenticação e não emite token.")

    d.h3("CT-01.5 Integridade do JWT no PEP")
    d.body(
        "user-mcp valida assinatura JWKS, aud, iss e exp (auth/jwt_validator.py) antes de qualquer ferramenta. token-exchange faz o mesmo no OBO. Um JWT adulterado é rejeitado com 401 na borda do serviço, sem consulta de negócio ao PDP CIBA. Não forjamos um token nesta sessão; o controle está no caminho quente de todas as tools/call."
    )

    d.h2("5.2 Categoria 02 — mTLS e identidade de workload")
    d.body(
        "O caderno pede certificado X.509 com CN=agente-generalista-prod no APIM. No laboratório a identidade de workload é SPIFFE emitida pelo Consul (CA do mesh) e o PEP de rede são as Service Intentions. Extração de “CN” vira o service name Consul (ai-agent, web, user-mcp)."
    )
    d.body(
        "Intentions observadas ao vivo em https://localhost:8501 (16 regras). Recorte relevante: web-api-gateway → web allow; web → ai-agent allow; ai-agent → user-mcp / token-exchange / litellm-gateway allow; user-mcp → opa-mcp-authz allow; keycloak-api-gateway → keycloak allow. Qualquer outra origem é deny implícito (fail-closed da malha)."
    )
    d.table(
        [
            ["Origem", "Destino", "Ação", "CT"],
            ["web-api-gateway", "web", "allow", "02.1 / 02.4 — único front door do canal"],
            ["web", "ai-agent", "allow", "03.1 — canal fala com o agente só pela malha"],
            ["ai-agent", "user-mcp", "allow", "02.1 — agente especialista/MCP"],
            ["ai-agent", "token-exchange", "allow", "01.x — OBO"],
            ["user-mcp", "opa-mcp-authz", "allow", "04.3 / 06.2 — ext_authz"],
            ["(qualquer outro)", "web / ai-agent / user-mcp", "deny implícito", "02.2 / 02.4"],
        ],
        "2400,2400,2200,2400",
        "Intentions Consul como ACL de mTLS/CN",
    )
    d.body(
        "CT-02.3 (expirado/revogado) não foi ensaiado com um certificado velho. A CA do Consul emite e rotaciona os certificados de sidecar; a revogação operacional é rotação + intentions, não OCSP corporativo."
    )

    d.h2("5.3 Categoria 03 — ALLOW")
    d.h3("CT-03.1 Fluxo concedido")
    d.body(
        "No ensaio ponta a ponta desta ata (request_id=a6683a53-66c9-4519-8ce9-343130a288b3, 02:18–02:19 UTC), o admin pediu create_user (Carla Santos / carla.e2e@example.com). A política Vault ciba/create_user/admin exigiu step-up. Após CIBA approve no canal :8082, o PDP emitiu ALLOW com LoA atual = LoA requerido = 2 e a ferramenta foi invocada. Trecho literal dos logs estruturados de user-mcp:"
    )
    d.para(
        "event=ciba_started  user=admin agent=ai-agent  “Vault policy required CIBA; waiting for human approval”  request_id=a6683a53-66c9-4519-8ce9-343130a288b3",
        font="Consolas",
        size="9pt",
        spaceAfter="4pt",
    )
    d.para(
        "event=ciba_approved  “CIBA approved; using Keycloak JWT for Vault”",
        font="Consolas",
        size="9pt",
        spaceAfter="4pt",
    )
    d.para(
        "event=pdp_decision  tool=create_user  PDP_Decision=ALLOW  LoA_Level=2  Required_LoA=2",
        font="Consolas",
        size="9pt",
        spaceAfter="4pt",
    )
    d.para(
        "event=tool_invoked  create_user invoked",
        font="Consolas",
        size="9pt",
        spaceAfter="8pt",
    )
    d.body(
        "Isso é o Cenário A do caderno com LoA elevado. Leituras (list/search) permanecem LoA 1 (OBO silencioso) — ALLOW sem step-up, alinhado à nota do diagrama de “consulta padrão” versus transação sensível."
    )
    cap = d.para()
    d.picture(cap, SHOT / "05-assurance-loa-panel.png", "Painel Assurance level no Identity inspector", "16.5cm")
    d.caption("Figura 7 — Inspector de Assurance (PDP decision / LoA). Após um tool call o painel passa a mostrar LoA 1 ou LoA 2 e a decisão ALLOW/DENY/STEP_UP.")

    d.h3("CT-03.2 Performance do PDP")
    d.body(
        "Não executamos a bateria de 100 requisições. O PDP de ferramenta (OPA sidecar / ext_authz) e o probe de ACL Vault são hops locais no cluster; o SLA de 15 ms é plausível para OPA in-process, mas fica como item de homologação de carga, não desta ata."
    )

    d.h2("5.4 Categoria 04 — DENY")
    d.h3("CT-04.1 Perfil de negócio")
    d.body(
        "O usuário “user” pertence só ao grupo user; o admin pertence a user+admin. Vault JWT role user-mcp-oidc-write está bound a groups=admin e scope users.write. Tentativa de create/delete com o perfil standard resulta em 403 (escopo ou catálogo opa-mcp-authz) sem invocar o Postgres. Equivale a Clientes_Standard × VIP_Exclusivo."
    )
    d.h3("CT-04.2 Token expirado")
    d.body(
        "O subject JWT desta sessão tinha iat/exp de uma hora. Validadores recusam exp vencido. O caderno pede um token de 5 s; o laboratório usa TTL de sessão/OBO (300 s no role Vault). Controle presente, ensaio de 5 s não rodado."
    )
    d.h3("CT-04.3 Ação proibida entre agentes")
    d.body(
        "opa-mcp-authz autoriza pares (agente origem → MCP destino → tool). ext_authz no inbound de user-mcp recusa tools/call fora do catálogo com 403, fail-closed. delete_user_by_email ainda exige CIBA para admin. O Agente não “pula” o PEP: não há intention de outro workload para user-mcp além de ai-agent e consul-mcp-authz."
    )

    d.h2("5.5 Categoria 05 — Step-up / LoA insuficiente")
    d.body(
        "O caderno descreve WWW-Authenticate: Bearer step_up_required, loa=3 e redirecionamento biométrico. O laboratório faz o step-up de forma síncrona: a tool create_user/delete bloqueia, o humano aprova ou nega em http://localhost:8082, e só então o Vault emite credenciais de escrita. A decisão STEP_UP_REQUIRED é logada antes do poll CIBA; ALLOW/DENY/EXPIRED depois."
    )
    cap = d.para()
    d.picture(cap, SHOT / "06-ciba-approval-channel.png", "Canal CIBA aguardando aprovação humana", "16.5cm")
    d.caption("Figura 8 — CT-05.x: canal CIBA (localhost:8082). Approve/Deny; sem approve o Vault não emite database/creds.")
    d.body(
        "CT-05.3 difere do caderno na UX: não há “reenvio” separado do canal com um novo JWT LoA=3. O mesmo tools/call espera a aprovação e conclui. O efeito de segurança é o mesmo (a operação sensível só ocorre após LoA elevado)."
    )
    d.body(
        "CT-05.4: Deny no canal devolve access_denied, o PDP registra DENY, a sessão anterior (LoA 1) permanece, e o INSERT não acontece."
    )

    d.h2("5.6 Categoria 06 — Auditoria e resiliência")
    d.h3("CT-06.1 Logs")
    d.body(
        "Cada linha JSON de user-mcp já carrega timestamp UTC, request_id (TransactionID), preferred_username (UserID) e agent_id. O evento pdp_decision adiciona PDP_Decision, LoA_Level e Required_LoA — os nomes que o caderno Telefônica pede. Workload_mTLS_CN equivale ao service name Consul (ai-agent) no campo agent. PDP_Decision_ID não é um UUID separado; o request_id correlaciona o fluxo."
    )
    d.h3("CT-06.2 Fail-closed")
    d.body(
        "service-defaults de user-mcp configura Envoy ext_authz com statusOnError 403 contra opa-mcp-authz. O filtro Lua do opa-gov-api também responde e encerra se campos obrigatórios faltam. Indisponibilidade do PDP não “abre” o caminho ao MCP/LLM."
    )

    d.h1("6. Lacunas e equivalências a alinhar com o cliente")
    d.table(
        [
            ["Tema", "Caderno", "Laboratório", "Proposta"],
            ["LoA no JWT do IdP", "claims loa/acr 1,2,3", "LoA no PDP de aplicação", "Mapper Keycloak acr; LoA 2 = CIBA; LoA 3 = WebAuthn se necessário"],
            ["PEP de borda", "Azure APIM", "Consul API Gateway + Envoy", "Ver documento Azure APIM anexo"],
            ["PDP de produto", "Ping Authorize", "OPA + Vault ACL", "OPA permanece para IA (prompt/PII); Ping no APIM para AuthZ de API"],
            ["CN X.509", "agente-generalista-prod", "SPIFFE / service name", "mTLS no APIM com certificado de workload + still mesh leste-oeste"],
            ["Step-up UX", "401 + reenvio do canal", "Tool bloqueante + CIBA", "Manter CIBA; opcionalmente devolver STEP_UP ao canal"],
            ["Carga PDP 15 ms", "100 req simultâneas", "Não medido", "Ensaio de performance em homologação"],
        ],
        "1800,2400,2400,2800",
        "Gaps e equivalências",
    )

    d.h1("7. Ensaio ponta a ponta ao vivo")
    d.body(
        "Executado em 16/09/2026 (horário local) / 17/09/2026 02:15–02:19 UTC, sessão admin no canal http://localhost:8080/landing. Objetivo: percorrer o Cenário A do caderno (consulta LoA 1 sem step-up, depois transação sensível com STEP_UP → CIBA → ALLOW LoA 2) e anexar as telas."
    )
    d.table(
        [
            ["Passo", "Ação", "Resultado observado", "CT"],
            ["1", "Workspace autenticado (admin)", "Sessão Keycloak; inspector de tokens visível", "01.1"],
            ["2", "Prompt “List all users.”", "Composer pronto para envio", "03.1 (consulta)"],
            ["3", "Resposta list_all_users", "7 usuários listados; sem CIBA", "03.1 ALLOW LoA 1"],
            ["4", "OBO token após a leitura", "aud=user-mcp, scope=users.read, act.sub=admin", "01.1 / OBO RFC 8693"],
            ["5", "Assurance após list", "LoA 1 — Baseline (silent OBO), decision ALLOW, tool list_all_users", "03.1 / 06.1"],
            ["6", "Prompt create_user Carla Santos", "Pedido de escrita ainda em LoA 1", "05.1"],
            ["7", "Canal CIBA :8082", "Pending admin / create_user / users.write — Approve|Deny", "05.1 / 05.2"],
            ["8", "Após Approve", "Carla Santos criada; LoA 2 — Elevated (CIBA-approved), ALLOW, tool create_user", "03.1 / 05.2 / 05.3"],
        ],
        "900,2800,3900,1800",
        "Roteiro do ensaio E2E e CTs cobertos",
    )
    d.body(
        "Correlação nos logs de user-mcp: list_all_users invoked request_id=154e07b4-8ab8-46e6-b90d-07ad595031b2 (02:15:01Z). create_user request_id=a6683a53-66c9-4519-8ce9-343130a288b3: 02:18:11Z PDP_Decision=STEP_UP_REQUIRED LoA_Level=1 Required_LoA=2; ciba_started; 02:19:28Z ciba_approved; PDP_Decision=ALLOW LoA_Level=2 Required_LoA=2; tool_invoked create_user. O Vault só emitiu credenciais de escrita depois do Approve."
    )

    d.h3("Passo 1 — Workspace autenticado")
    cap = d.para()
    d.picture(cap, SHOT / "e2e-01-workspace-admin.png", "Workspace AI Runtime Security com admin autenticado e conversa vazia", "16.5cm")
    d.caption("Figura 9 — Canal autenticado como admin. Identity inspector disponível; 0 mensagens.")

    d.h3("Passo 2 — Prompt de consulta (LoA 1)")
    cap = d.para()
    d.picture(cap, SHOT / "e2e-02-prompt-list-users.png", "Composer com o prompt List all users pronto para envio", "16.5cm")
    d.caption("Figura 10 — Operador envia listagem. Leituras usam OBO silencioso; o canal CIBA não é acionado.")

    d.h3("Passo 3 — ALLOW list_all_users")
    cap = d.para()
    d.picture(cap, SHOT / "e2e-03-allow-list-users.png", "Agente devolve a lista de usuários sem step-up", "16.5cm")
    d.caption("Figura 11 — CT-03.1 consulta: 7 registros devolvidos. Nenhum pedido CIBA.")

    d.h3("Passo 4 — Token OBO users.read")
    cap = d.para()
    d.picture(cap, SHOT / "e2e-04-obo-users-read.png", "Inspector do JWT OBO com scope users.read", "16.5cm")
    d.caption("Figura 12 — OBO RFC 8693: iss Keycloak demo, aud=user-mcp, scope=users.read, act.sub=admin, azp=token-exchange.")

    d.h3("Passo 5 — Assurance LoA 1 ALLOW")
    cap = d.para()
    d.picture(cap, SHOT / "e2e-05-assurance-loa1-allow.png", "Painel Assurance LoA 1 ALLOW list_all_users", "16.5cm")
    d.caption("Figura 13 — PDP no inspector: level LoA 1, decision ALLOW, tool list_all_users, required_level LoA 1.")

    d.h3("Passo 6 — Prompt de escrita (dispara step-up)")
    cap = d.para()
    d.picture(cap, SHOT / "e2e-06-prompt-create-user.png", "Composer com pedido de create_user Carla Santos", "16.5cm")
    d.caption("Figura 14 — CT-05.1: create_user ainda em LoA 1. A política Vault ciba/create_user/admin vai exigir CIBA.")

    d.h3("Passo 7 — STEP_UP_REQUIRED no canal CIBA")
    cap = d.para()
    d.picture(cap, SHOT / "e2e-07-ciba-step-up-pending.png", "Canal CIBA com pedido pending admin create_user users.write", "16.5cm")
    d.caption("Figura 15 — CT-05.1/05.2: binding_message=create_user, login_hint=admin, scope openid users.write profile roles. Sem Approve o Vault não emite database/creds.")

    d.h3("Passo 8 — ALLOW LoA 2 após Approve")
    cap = d.para()
    d.picture(cap, SHOT / "e2e-08-create-user-success.png", "Agente confirma criação de Carla Santos e inspector em LoA 2 ALLOW", "16.5cm")
    d.caption("Figura 16 — CT-03.1 / 05.2 / 05.3: usuário criado; Assurance level LoA 2 — Elevated (CIBA-approved), decision ALLOW, tool create_user, required_level LoA 2.")

    d.h1("8. Conclusão")
    d.body(
        "Dos 20 casos, 6 estão atendidos de forma direta nesta demo, 9 por equivalência de controle (mesmo requisito de segurança, produto ou UX diferentes), 3 parciais (código presente, ensaio específico não rodado), 1 não demonstrado (carga) e 1 gap (LoA 3 / FIDO2). O ensaio da seção 7 confirma na UI o ALLOW LoA 1 (leitura) e o ciclo STEP_UP → CIBA → ALLOW LoA 2 (escrita). O desenho PEP–PDP–IdP–cofre–MCP do diagrama Telefônica está implementado. O documento complementar descreve como o PEP de borda passaria a ser o Azure API Management, a malha leste-oeste Istio (no lugar do Consul), sem abandonar Vault e o step-up CIBA."
    )
    d.h2("Assinatura de homologação")
    d.table(
        [
            ["Papel", "Nome", "Data", "Resultado"],
            ["Apresentação técnica", "Guy Barros", "16/09/2026", "Evidências anexas"],
            ["Segurança da informação (cliente)", "", "", ""],
            ["Arquitetura de IA (cliente)", "", "", ""],
        ],
        "2800,2400,1800,2400",
        "Folha de assinatura",
    )

    batch(ATA, d.cmds)
    run(["officecli", "save", str(ATA)])
    print("ATA paragraphs", d.p)


def build_apim() -> None:
    setup_doc(
        APIM,
        title="Arquitetura com Azure API Management como AI Gateway",
        header="Azure APIM como AI Gateway — malha Istio no leste-oeste",
        footer="Documento complementar à Ata de Demonstração  ·  ",
    )

    d = Doc()
    d.para(
        "Como a arquitetura muda com Azure API Management como AI Gateway",
        bold="true",
        size="22pt",
        color="0F62FE",
        spaceAfter="8pt",
    )
    d.body(
        "Documento complementar à Ata de Demonstração. Parte do diagrama Telefônica/Vivo que o laboratório ainda não instancia como produto: o Azure API Management (APIM) no papel de PEP / AI Gateway, consultando o Ping Authorize como PDP. A malha leste-oeste no alvo do cliente é Istio (não Consul). O restante do desenho — IdP, agente, LLM, MCP, cofre, banco — permanece."
    )

    d.h1("1. O que o laboratório já é")
    d.body(
        "Hoje o PEP está distribuído na malha Consul: o Consul API Gateway é o único norte-sul do canal (web) e do IdP/CIBA; cada sidecar Envoy autentica mTLS SPIFFE e aplica Service Intentions; o inbound de user-mcp chama opa-mcp-authz (ext_authz) e o ai-agent pode consultar opa-gov-api via Lua. O PDP de LoA/CIBA vive em user-mcp + ACL Vault. Isso atende o caderno por equivalência, mas não é o APIM do diagrama nem a malha Istio da plataforma alvo."
    )
    cap = d.para()
    d.picture(cap, SHOT / "00-diagrama-telefonica-vivo.jpg", "Diagrama Telefônica com Azure APIM no centro", "16.5cm")
    d.caption("Figura 1 — Alvo do cliente: todo JWT e mTLS de agente passam pelo Azure APIM, que consulta o Ping Authorize. Atrás do APIM, o leste-oeste corre em Istio.")

    d.h1("2. O que muda se o PEP de borda for o APIM")
    d.body(
        "São duas substituições, não uma. Norte-sul: o APIM assume o PEP que o caderno atribui ao AI Gateway (no laboratório isso é o Consul API Gateway). Leste-oeste: Istio substitui o Consul Connect — mTLS STRICT, identidade SPIFFE no ServiceAccount, AuthorizationPolicy fail-closed — atrás do APIM. O APIM não substitui a malha; o Consul não permanece no alvo."
    )

    d.h2("2.1 Norte-sul no APIM (novo PEP)")
    d.table(
        [
            ["Controle do caderno", "Onde fica hoje (lab Consul)", "Onde fica com APIM + Istio"],
            ["Validação JWT (CT-01.5, 04.2)", "user-mcp / token-exchange", "Política validate-jwt no inbound do APIM (JWKS do IdP) + defesa em profundidade no MCP"],
            ["mTLS de workload (CT-02.x)", "Consul Connect", "Inbound APIM: certificado cliente obrigatório; ACL de thumbprint/CN (agente-generalista-prod). Leste-oeste: PeerAuthentication STRICT no Istio"],
            ["Consulta PDP ALLOW/DENY/STEP_UP", "OPA + Vault ACL", "Política send-request ao Ping Authorize; APIM aplica a decisão"],
            ["Cabeçalho step_up (CT-05.1)", "Tool bloqueante CIBA", "APIM devolve 401 WWW-Authenticate: Bearer step_up_required, loa=N"],
            ["Fail-closed (CT-06.2)", "ext_authz statusOnError=403", "APIM: backend Ping indisponível → 503, sem forward ao agente/MCP"],
            ["Auditoria (CT-06.1)", "logs JSON dos pods", "APIM diagnostic logs + correlação com Application Insights; ainda emitir pdp_decision no MCP"],
            ["JWT segue na chamada às APIs", "OBO/CIBA no Authorization", "Política set-header: o mesmo JWT (ou token trocado) vai ao MCP / API Sistemas"],
        ],
        "2400,2800,4200",
        "Movimentação de controles para o APIM",
    )

    d.h2("2.2 O que não deve migrar para o APIM")
    d.body(
        "Cofre de senhas (Vault) continua emitindo database/creds por JWT — o APIM não deve conhecer a senha do Postgres. O MCP continua sendo o PEP de ferramenta (escopo users.read/write, catálogo de tools). O LLM continua atrás de um gateway (LiteLLM hoje; Azure OpenAI/APIM LLM backend amanhã) com mTLS do sidecar Istio. CIBA/HITL continua no IdP: o APIM só sinaliza STEP_UP; quem sobe o LoA é o Keycloak (ou o IdP corporativo)."
    )

    d.h2("2.3 Leste-oeste: Istio no lugar do Consul")
    d.body(
        "O laboratório prova os controles com Consul Connect. No alvo Telefônica/Vivo a malha é Istio (sidecars Envoy injetados pelo istiod, o mesmo data plane). Consul Intentions default-deny; Istio AuthorizationPolicy default-allow se não houver política — por isso o namespace da carga precisa de um deny-all + ALLOW explícitos, senão CT-02.2/02.4 ficam abertos."
    )
    d.table(
        [
            ["Controle no laboratório (Consul)", "Equivalente no alvo (Istio)", "CT"],
            ["Consul API Gateway (norte-sul)", "Azure APIM; Istio Ingress só para tráfego cluster-interno, se houver", "01.5 / 02.4"],
            ["Consul Connect mTLS", "PeerAuthentication mode: STRICT em todo o namespace da carga", "02.1"],
            ["SPIFFE = service name Consul", "SPIFFE = spiffe://<trust-domain>/ns/<ns>/sa/<serviceaccount>", "02.1"],
            ["Service Intentions allow/deny implícito", "AuthorizationPolicy: deny-all no namespace + ALLOW por principal (SA do APIM, ai-agent, user-mcp…)", "02.2 / 02.4"],
            ["CA do Consul / rotação de cert sidecar", "CA do istiod (ou mesh trust domain corporativo); rotação automática do SDS", "02.3"],
            ["Envoy ext_authz → opa-mcp-authz", "Sidecar Istio + EnvoyFilter / ext_authz (OPA continua; não vai para o Ping)", "04.3 / 06.2"],
            ["service-defaults Lua → opa-gov-api", "EnvoyFilter Istio no inbound/outbound do ai-agent (prompt/PII)", "06.2"],
        ],
        "3200,4000,2200",
        "Tradução Consul → Istio dos controles de malha",
    )
    d.body(
        "Identidade de workload no Ping/APIM: o CN X.509 do caderno (agente-generalista-prod) no norte-sul; o SAN SPIFFE do ServiceAccount no leste-oeste. Não reutilizar o service name Consul como se fosse Istio."
    )

    d.h1("3. Fluxo alvo (canal → APIM → PDP → agente → MCP)")
    mermaid = """flowchart LR
  cliente[Cliente] --> canal[Canal / Agente de Atendimento]
  canal -->|Authorization Code| idp[IdP / Keycloak]
  idp -->|JWT LoA| canal
  canal -->|JWT + mTLS| apim[Azure APIM / AI Gateway PEP]
  apim <--> ping[Ping Authorize PDP]
  subgraph istio [Malha Istio mTLS STRICT]
    agente[Agente de IA]
    llm[LLM]
    mcp[Servidor MCP]
    vault[Vault / Cofre]
    db[(Postgres)]
  end
  apim -->|mTLS + JWT| agente
  apim -->|mTLS + JWT| llm
  apim -->|mTLS + JWT| mcp
  mcp -->|JWT OBO/CIBA| vault
  vault -->|creds efêmeras| mcp
  mcp --> db
  mcp -->|JWT| apis[API Sistemas]
"""
    d.add("/body", "diagram", mermaid=mermaid, width="16cm", render="native")
    d.caption("Figura 2 — APIM no norte-sul; Istio no leste-oeste. O canal nunca fala direto com o agente, o MCP ou o LLM. O Ping Authorize decide; o Vault continua cego ao PEP de borda.")

    d.h1("4. LoA no IdP — o asterisco do diagrama")
    d.body(
        "O diagrama pede “Implementar LoA no IdP”. Hoje o LoA é calculado no user-mcp. Com APIM + Ping, o JWT precisa carregar acr/loa para o PDP não depender de estado da aplicação:"
    )
    d.body(
        "LoA 1 — senha, claim acr=1. Cliente Vivo na rede (nota laranja do diagrama) pode nascer já em LoA 1. LoA 2 — MFA ou CIBA; o APIM/Ping exigem acr>=2 para escrita. LoA 3 — WebAuthn/FIDO2 no IdP para transação de alto valor, se o caderno CT-01.3/05.x for literal. O laboratório cobre 1 e 2; 3 continua gap até o IdP corporativo expor o autenticador."
    )

    d.h1("5. Desenho híbrido recomendado (não big-bang)")
    d.body(
        "Fase 1 — Trocar a malha: sidecars Istio no lugar do Consul Connect, PeerAuthentication STRICT, AuthorizationPolicy deny-all + ALLOW das origens atuais (equivalente às 16 intentions). APIM na frente do canal e do ai-agent, ainda sem Ping: validate-jwt + mTLS, backend = serviços já meshed. Prova CT-01.5 e CT-02.x no produto que o cliente vai operar."
    )
    d.body(
        "Fase 2 — Ping Authorize como PDP chamado pelo APIM. Mapear as AuthorizationPolicies e o catálogo opa-mcp-authz para políticas Ping (CN, groups, action, loa). OPA permanece só para guardrail de prompt/PII (não é AuthZ de API)."
    )
    d.body(
        "Fase 3 — Step-up no contrato HTTP do caderno: APIM traduz STEP_UP_REQUIRED em 401 + WWW-Authenticate; o canal redireciona ao IdP; o JWT novo reentra no APIM. CIBA pode continuar como authenticator do LoA 2 para escritas MCP, agora disparado depois do ALLOW do Ping ou como ação do próprio IdP."
    )
    d.body(
        "Istio não some: leste-oeste MCP↔Vault↔Postgres e a proibição de bypass (CT-02.4) continuam sendo AuthorizationPolicy. O APIM (identidade de workload / SA de egress) torna-se o único principal autorizado a falar com o ai-agent, exatamente como web-api-gateway é hoje a única origem Consul de web."
    )

    d.h1("6. Contratos que o APIM precisa preservar")
    d.table(
        [
            ["Contrato", "Hoje (lab Consul)", "No APIM + Istio"],
            ["Actor token do agente", "Vault / Kubernetes JWT injetado", "APIM não substitui; encaminha ou o agente apresenta no outbound"],
            ["OBO RFC 8693", "token-exchange + Keycloak", "Permanece; APIM pode exigir o OBO no backend-id do MCP"],
            ["CIBA poll", "user-mcp → Keycloak", "Igual; APIM só vê o JWT CIBA na retry"],
            ["Escopos users.read / users.write", "OBO + require_scopes", "Ping pode espelhar; MCP continua enforcement"],
            ["PII / prompt injection", "opa-gov-api no sidecar Consul", "EnvoyFilter Istio no ai-agent — fora do AuthZ Ping"],
            ["Bypass do PEP (CT-02.4)", "Intention: só web-api-gateway → web", "AuthorizationPolicy: só o principal do APIM → ai-agent / MCP"],
        ],
        "2400,3200,3800",
        "Contratos que não podem se perder na migração",
    )

    d.h1("7. Conclusão")
    d.body(
        "Colocar o Azure APIM como AI Gateway não redesenha o agente, o MCP ou o cofre. Move o PEP de borda para o produto que o caderno nomeia, faz o Ping Authorize decidir ALLOW/DENY/STEP_UP com LoA no JWT, e troca o Consul Connect por Istio como malha interna fail-closed (mTLS STRICT + deny-all). O laboratório já prova os controles; APIM + Istio é a forma de operá-los no padrão de plataforma Telefônica/Vivo."
    )

    batch(APIM, d.cmds)
    run(["officecli", "save", str(APIM)])
    print("APIM paragraphs", d.p)


if __name__ == "__main__":
    targets = sys.argv[1:] or ["ata", "apim"]
    if "ata" in targets:
        build_ata()
    if "apim" in targets:
        build_apim()
    print("done")
