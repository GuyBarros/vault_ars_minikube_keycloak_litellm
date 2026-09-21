"use strict";

/* ----------------------------------------------------------------------------
 * Vault Agent Timeline
 * Parser + renderer for HashiCorp Vault / AI-agent OBO token-exchange logs.
 * Pure client-side, no build step. Open index.html through a local HTTP server.
 * ------------------------------------------------------------------------- */

const EVENT_META = {
  agent_request_started: {
    color: "var(--c-request)",
    tag: "request",
    title: "Requisição recebida",
    icon: "M12 2v4M12 18v4M2 12h4M18 12h4",
  },
  identity_broker_call: {
    color: "var(--c-broker)",
    tag: "token exchange",
    title: "Chamada ao Identity Broker (Vault)",
    icon: "M21 2 13 10M21 2v6M21 2h-6M7 11a4 4 0 1 0 4 4",
  },
  obo_token_exchange_completed: {
    color: "var(--c-obo)",
    tag: "OBO token",
    title: "Token OBO emitido pelo Vault",
    icon: "M9 12l2 2 4-4M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3Z",
  },
  scoped_tool_token_exchange_failed: {
    color: "var(--c-fail)",
    tag: "token negado",
    title: "Troca de token NEGADA pelo Vault",
    icon: "M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3ZM9 9l6 6M15 9l-6 6",
  },
  scoped_tool_invoke: {
    color: "var(--c-tool)",
    tag: "scoped tool",
    title: "Ferramenta MCP invocada com token",
    icon: "M14 7l3 3-7 7-3-3zM5 19l3-1M19 5l-3 1",
  },
  response_sent: {
    color: "var(--c-response)",
    tag: "response",
    title: "Resposta enviada ao usuário",
    icon: "M22 2 11 13M22 2l-7 20-4-9-9-4z",
  },
  http_access: {
    color: "var(--c-http)",
    tag: "http",
    title: "Acesso HTTP",
    icon: "M4 12h16M4 12l4-4M4 12l4 4",
  },
  token_poll: {
    color: "var(--c-http)",
    tag: "token poll",
    title: "Agente consultou seus tokens",
    icon: "M21 12a9 9 0 1 1-6.2-8.5M21 3v6h-6",
  },
  /* --- Identity broker (token-exchange service) events --- */
  verify_obo_token_exchange: {
    color: "var(--c-obo)",
    tag: "OBO emitido",
    title: "Token OBO emitido (IBM Verify)",
    icon: "M9 12l2 2 4-4M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3Z",
  },
  verify_obo_http_error: {
    color: "var(--c-fail)",
    tag: "erro IBM Verify",
    title: "Erro HTTP no IBM Verify",
    icon: "M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3ZM9 9l6 6M15 9l-6 6",
  },
  verify_obo_retry: {
    color: "var(--c-broker)",
    tag: "retry",
    title: "Tentativa de reenvio OBO",
    icon: "M21 12a9 9 0 1 1-6.2-8.5M21 3v6h-6",
  },
  obo_token_exchange_internal_error: {
    color: "var(--c-fail)",
    tag: "falha interna",
    title: "Falha interna — token OBO não obtido",
    icon: "M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01",
  },
  obo_token_exchange_authz_denied: {
    color: "var(--c-fail)",
    tag: "acesso negado",
    title: "Troca OBO NEGADA (autorização)",
    icon: "M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3ZM9 9l6 6M15 9l-6 6",
  },
  identity_broker_starting: {
    color: "var(--c-http)",
    tag: "startup",
    title: "Identity Broker iniciando",
    icon: "M22 12h-4l-3 9L9 3l-3 9H2",
  },
  settings_loaded: {
    color: "var(--c-http)",
    tag: "config",
    title: "Configurações carregadas",
    icon: "M12 20h9M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z",
  },
  /* --- user-mcp (MCP server) events --- */
  tool_invoked: {
    color: "var(--c-tool)",
    tag: "tool MCP",
    title: "Ferramenta MCP executada (user-mcp)",
    icon: "M14 7l3 3-7 7-3-3zM5 19l3-1M19 5l-3 1",
  },
  /* --- verify-vault-web-app (BFF) events --- */
  web_conversation_started: {
    color: "var(--c-request)",
    tag: "web — conversa",
    title: "Conversa iniciada (web app)",
    icon: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  },
  web_agent_retry: {
    color: "var(--c-broker)",
    tag: "web — retry",
    title: "Web app: retry no agent service",
    icon: "M21 12a9 9 0 1 1-6.2-8.5M21 3v6h-6",
  },
  web_agent_error: {
    color: "var(--c-fail)",
    tag: "web — erro",
    title: "Web app: agent retornou erro",
    icon: "M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01",
  },
  web_auth_callback: {
    color: "var(--c-obo)",
    tag: "web — auth",
    title: "Autenticação concluída (web app)",
    icon: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  },
  web_auth_logout: {
    color: "var(--c-http)",
    tag: "web — logout",
    title: "Logout (web app)",
    icon: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  },
  pdp_decision: {
    color: "var(--c-obo)",
    tag: "PEP",
    title: "Decisão do PEP",
    icon: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  },
  token_chain_subject: {
    color: "var(--c-obo)",
    tag: "subject JWT",
    title: "Cadeia — subject (Keycloak)",
    icon: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  },
  token_chain_actor: {
    color: "var(--c-broker)",
    tag: "actor JWT",
    title: "Cadeia — actor (Vault)",
    icon: "M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3Z",
  },
  ciba_started: {
    color: "var(--c-broker)",
    tag: "CIBA",
    title: "CIBA iniciado — espera Approve",
    icon: "M12 8v4l3 3M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z",
  },
  ciba_approved: {
    color: "var(--c-obo)",
    tag: "CIBA LoA2",
    title: "CIBA aprovado — JWT elevado",
    icon: "M9 12l2 2 4-4M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3Z",
  },
  ciba_denied: {
    color: "var(--c-fail)",
    tag: "CIBA negado",
    title: "CIBA negado ou expirado",
    icon: "M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3ZM9 9l6 6M15 9l-6 6",
  },
  jwt_identity_bound: {
    color: "var(--c-request)",
    tag: "identidade",
    title: "JWT ligado no user-mcp",
    icon: "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 7a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  },
  vault_db_creds_issued: {
    color: "var(--c-obo)",
    tag: "Vault mint",
    title: "Vault emitiu credencial Postgres",
    icon: "M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3Z",
  },
  db_call: {
    color: "var(--c-tool)",
    tag: "Postgres",
    title: "Conexão Postgres com credencial dinâmica",
    icon: "M4 6h16M4 12h16M4 18h16",
  },
  transform_encode: {
    color: "var(--c-broker)",
    tag: "Transform",
    title: "Vault Transform mascarou um campo",
    icon: "M12 2v20M2 12h20",
  },
  vault_lease_revoked: {
    color: "var(--c-http)",
    tag: "lease",
    title: "Lease da credencial Postgres revogado",
    icon: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6",
  },
  mesh_svid: {
    color: "var(--c-request)",
    tag: "mTLS SVID",
    title: "Identidade SPIFFE mintada pelo Consul",
    icon: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  },
  mesh_intention: {
    color: "var(--c-http)",
    tag: "intention",
    title: "Service Intention (quem pode falar com quem)",
    icon: "M17 8h2a2 2 0 0 1 2 2v8H3V10a2 2 0 0 1 2-2h2M12 2v10",
  },
  opa_catalog: {
    color: "var(--c-broker)",
    tag: "OPA catalog",
    title: "Catálogo MCP carregado no OPA",
    icon: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 4.5A2.5 2.5 0 0 1 6.5 7H20v13H6.5A2.5 2.5 0 0 1 4 17.5z",
  },
  llm_error: {
    color: "var(--c-fail)",
    tag: "LLM",
    title: "Falha no modelo (LiteLLM → Ollama)",
    icon: "M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01",
  },
};

const LEAD_TS_RE = /^(\d{4}-\d{2}-\d{2}T\S+?)\s+(.*)$/s;
const ACCESS_RE = /INFO:\s+(\S+)\s+-\s+"(\S+)\s+(\S+)\s+HTTP\/[\d.]+"\s+(\d+)\s*(.*)$/;
const USER_RE = /\[user=([^\s\]]+)\s+agent=([^\s\]]+)\]/;
const USER_SOLO_RE = /\[user=([^\s\]]+)\]/;
const USER_NOBRACK_RE = /^user=([^\s]+)\s+agent=([^\s]+)/;
const JSON_IN_LINE_RE = /(\{.*\})\s*$/;
const LOG_PREFIX_RE = /^(?:INFO|WARNING|ERROR|DEBUG):\s*[\w.]+[:\s]+/;
const VAULT_LEASE_RE = /revoked lease:\s*lease_id=(\S+)/;
const NOISE_PATH_RE = /\/health\/(live|ready)/;

let STATE = {
  sessions: [],
  system: [],
  snapshot: null,
  search: "",
  userFilter: "all",
  sourceFilter: "all",
  mask: true,
  view: "hops",
};

const SOURCE_COLORS = [
  "var(--c-request)", "var(--c-tool)", "var(--c-broker)",
  "var(--c-obo)", "var(--c-response)", "var(--c-http)",
];

/* ---------------------------- RTF stripping -------------------------------- */

function stripRtf(text) {
  if (!text.trimStart().startsWith('{\\rtf')) return text;

  // 1. Hex escapes \'XX → character
  text = text.replace(/\\'([0-9a-fA-F]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16)));

  // 2. Protect RTF escape sequences before stripping control words
  text = text.replace(/\\\\/g, '\x00BS\x00');
  text = text.replace(/\\\{/g, '\x00OB\x00');
  text = text.replace(/\\\}/g, '\x00CB\x00');

  // 3. Backslash at end of line (RTF paragraph/line break) → newline
  text = text.replace(/\\\n/g, '\n');

  // 4. Remove RTF control words: \letters, \letters123, \letters-123
  text = text.replace(/\\[a-zA-Z]+-?\d*[ ]?/g, '');

  // 5. Remove remaining RTF control symbols (e.g. \*)
  text = text.replace(/\\[^a-zA-Z\x00\n]/g, '');

  // 6. Remove RTF group delimiters
  text = text.replace(/[{}]/g, '');

  // 7. Restore escaped chars
  text = text.replace(/\x00BS\x00/g, '\\');
  text = text.replace(/\x00OB\x00/g, '{');
  text = text.replace(/\x00CB\x00/g, '}');

  return text;
}

/* ---------------------------- Parsing ------------------------------------- */

function inferEventType(json) {
  if (json.event) return json.event;
  const logger = (json.logger || "").toLowerCase();
  const level = (json.level || "").toUpperCase();
  if (logger.includes("api.agent.query")) return "web_conversation_started";
  if (logger.includes("services.agent_api") || logger.includes("service.agent_api"))
    return level === "ERROR" ? "web_agent_error" : "web_agent_retry";
  if (logger.includes("api.auth.callback")) return "web_auth_callback";
  if (logger.includes("api.auth.logout")) return "web_auth_logout";
  return "log";
}

function parseKvFields(line) {
  const json = {};
  const re = /\b([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)/g;
  let m;
  while ((m = re.exec(line))) {
    let value = m[2];
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (value === "-" || value === "None" || value === "null") value = null;
    json[m[1]] = value;
  }
  return json;
}

function looksLikeSnapshot(json) {
  return json && typeof json === "object" && Array.isArray(json.identities) && json.trust_domain;
}

function parseLogs(raw, source) {
  raw = stripRtf(raw);
  const lines = raw.split(/\r?\n/).map((l) => l.trimEnd()).filter((l) => l.trim());
  const events = [];
  let snapshot = null;
  let currentSource = source || null;
  let expectSnapshot = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("### SOURCE ")) {
      currentSource = trimmed.slice("### SOURCE ".length).trim();
      continue;
    }
    if (trimmed === "### SNAPSHOT") {
      expectSnapshot = true;
      continue;
    }
    if (trimmed.startsWith("# ") || trimmed === "#") continue;

    let payload = trimmed;
    let containerTs = null;
    const lead = trimmed.match(LEAD_TS_RE);
    if (lead) {
      containerTs = lead[1];
      payload = lead[2].trim();
    }

    if (payload.startsWith("{")) {
      try {
        const json = JSON.parse(payload);
        if (expectSnapshot || looksLikeSnapshot(json)) {
          snapshot = json;
          expectSnapshot = false;
          continue;
        }
        if ((json.level || "").toUpperCase() === "DEBUG") continue;
        events.push(buildJsonEvent(json, json.timestamp || containerTs, currentSource));
      } catch (e) {
        /* skip malformed json */
      }
      continue;
    }
    expectSnapshot = false;

    const jsonTail = payload.match(JSON_IN_LINE_RE);
    if (jsonTail && (payload.startsWith("{") === false) && jsonTail[1].includes('"event"')) {
      try {
        const json = JSON.parse(jsonTail[1]);
        if ((json.level || "").toUpperCase() === "DEBUG") continue;
        events.push(buildJsonEvent(json, json.timestamp || containerTs, currentSource));
        continue;
      } catch (e) {
        /* fall through */
      }
    }

    const stripped = payload.replace(LOG_PREFIX_RE, "");
    if (stripped !== payload && stripped.startsWith("{")) {
      try {
        const json = JSON.parse(stripped);
        events.push(buildJsonEvent(json, json.timestamp || containerTs, currentSource));
        continue;
      } catch (e) {
        /* fall through */
      }
    }

    const accessLine = payload.match(ACCESS_RE);
    if (accessLine) {
      if (!NOISE_PATH_RE.test(accessLine[3])) {
        events.push(buildAccessEvent(accessLine, containerTs, currentSource));
      }
      continue;
    }

    if (/\bevent=/.test(payload)) {
      const kv = parseKvFields(stripped.includes("=") ? stripped : payload);
      if (!kv.event) continue;
      events.push(buildJsonEvent(kv, kv.timestamp || containerTs, currentSource));
      continue;
    }

    const lease = payload.match(VAULT_LEASE_RE);
    if (lease) {
      events.push(buildJsonEvent({
        event: "vault_lease_revoked",
        lease_id: lease[1],
        message: payload.slice(0, 240),
        timestamp: containerTs,
      }, containerTs, currentSource));
      continue;
    }

    if (/OpenAIException|Received Model Group=|InternalServerError:/.test(payload)) {
      events.push(buildJsonEvent({
        event: "llm_error",
        message: payload.slice(0, 400),
        timestamp: containerTs,
        path: "/v1/chat/completions",
      }, containerTs, currentSource));
    }
  }

  const grouped = groupEvents(events);
  grouped.snapshot = snapshot;
  return grouped;
}

function toDate(tsStr) {
  return tsStr ? new Date(tsStr.replace(/(\.\d{3})\d+/, "$1")) : null;
}

function buildJsonEvent(json, containerTs, source) {
  const msg = json.message || "";
  const mFull = msg.match(USER_RE);
  const mNobrack = !mFull && msg.match(USER_NOBRACK_RE);
  const mSolo = !mFull && !mNobrack && msg.match(USER_SOLO_RE);
  const userFromMsg = (mFull || mNobrack)?.[1] || mSolo?.[1] || null;
  return {
    kind: "json",
    type: inferEventType(json),
    ts: toDate(json.timestamp || containerTs),
    tsRaw: json.timestamp || containerTs,
    requestId: json.request_id && json.request_id !== "-" ? json.request_id : null,
    user: userFromMsg || json.preferred_username || json.login_hint || json.user || null,
    agentId: (mFull || mNobrack)?.[2] || json.agent_id || null,
    path: json.path || json.request_path || null,
    source: source || json.source || null,
    json,
  };
}

function buildAccessEvent(a, containerTs, source) {
  const isTokenPoll = a[3].includes("/v1/agent/tokens");
  return {
    kind: "access",
    type: isTokenPoll ? "token_poll" : "http_access",
    ts: toDate(containerTs),
    tsRaw: containerTs,
    requestId: null,
    user: null,
    method: a[2],
    path: a[3],
    status: Number(a[4]),
    statusText: a[5] || "",
    source: source || null,
    json: { source: a[1], method: a[2], path: a[3], status_code: Number(a[4]) },
  };
}

function groupEvents(events) {
  // Access lines without a timestamp inherit time from the preceding event.
  let lastTs = null;
  for (const ev of events) {
    if (!ev.ts && lastTs) ev.ts = new Date(lastTs.getTime() + 1);
    if (ev.ts) lastTs = ev.ts;
  }

  const byReq = new Map();
  const order = [];
  const access = [];

  for (const ev of events) {
    if (ev.requestId) {
      if (!byReq.has(ev.requestId)) {
        byReq.set(ev.requestId, []);
        order.push(ev.requestId);
      }
      byReq.get(ev.requestId).push(ev);
    } else {
      access.push(ev);
    }
  }

  const sessions = order.map((rid) => {
    const evs = byReq.get(rid).filter((e) => e.ts).sort((a, b) => a.ts - b.ts);
    if (!evs.length) return null;  // all events had no timestamp — skip
    const start = evs[0].ts;
    const end = evs[evs.length - 1].ts;
    const startEv = evs.find((e) => e.type === "agent_request_started");
    const respEv = evs.find((e) => e.type === "response_sent");
    const toolEv = evs.find((e) => e.type === "scoped_tool_invoke");
    const brokerEv = evs.find((e) => e.type === "identity_broker_call");
    const failEv = evs.find((e) => e.type === "scoped_tool_token_exchange_failed");
    const oboEv = evs.find((e) => e.type === "obo_token_exchange_completed");
    // Identity-broker (token-exchange service) events
    const verifySuccessEv = evs.find((e) => e.type === "verify_obo_token_exchange");
    const authzDeniedEv = evs.find((e) => e.type === "obo_token_exchange_authz_denied");
    const verifyFailEv = evs.find((e) => e.type === "obo_token_exchange_internal_error");
    const httpErrorEv = evs.find((e) => e.type === "verify_obo_http_error");

    let tokenOutcome = "n/a";
    if (failEv || verifyFailEv || authzDeniedEv) tokenOutcome = "denied";
    else if (oboEv || verifySuccessEv) tokenOutcome = "issued";
    else if (toolEv && !brokerEv) tokenOutcome = "cached";

    // web-app (BFF) events
    const webConvEv = evs.find((e) => e.type === "web_conversation_started");
    const webErrorEv = evs.find((e) => e.type === "web_agent_error");
    const toolInvokedEv = evs.find((e) => e.type === "tool_invoked");

    const anyBrokerEv = verifySuccessEv || authzDeniedEv || verifyFailEv || httpErrorEv;
    const scopeFromVerify = (verifySuccessEv || httpErrorEv)?.json?.scope;
    let brokerQuery = "";
    if (scopeFromVerify) brokerQuery = `OBO exchange: ${scopeFromVerify}`;
    else if (authzDeniedEv) brokerQuery = "OBO exchange (negado — autorização)";
    else if (verifyFailEv) brokerQuery = "OBO exchange (falhou após retries)";
    else if (httpErrorEv) brokerQuery = "OBO exchange (erro IBM Verify)";
    const pepToolEv = evs.find((e) => e.type === "pdp_decision" && e.json?.tool);
    return {
      requestId: rid,
      events: evs,
      start,
      end,
      user: startEv?.user || webConvEv?.user || anyBrokerEv?.user || evs[0].user || "desconhecido",
      query: startEv?.json?.user_message || respEv?.json?.user_message || brokerQuery || "",
      path: startEv?.path || webConvEv?.path || evs[0]?.path || "/v1/agent/query",
      status: respEv?.json?.status_code || webErrorEv?.json?.status || null,
      tool: (toolEv || failEv)?.json?.tool || toolInvokedEv?.json?.tool || pepToolEv?.json?.tool || null,
      scopes: (toolEv || failEv || brokerEv)?.json?.required_scopes ||
        (brokerEv?.json?.scope ? [brokerEv.json.scope] : null) ||
        (scopeFromVerify ? [scopeFromVerify] : null) ||
        toolInvokedEv?.json?.required_scopes || null,
      tokenOutcome,
      durationMs: end - start,
    };
  }).filter(Boolean);

  // Attach matching POST access logs to their session window; rest is "system".
  // Skip access events with no resolvable timestamp (e.g. /mcp protocol logs without container prefix).
  const MESH_TYPES = new Set(["mesh_svid", "mesh_intention", "opa_catalog", "collect_skip"]);
  const ATTACH_TYPES = new Set([
    "pdp_decision", "token_chain_subject", "token_chain_actor",
    "ciba_started", "ciba_approved", "ciba_denied",
    "jwt_identity_bound", "vault_db_creds_issued", "db_call", "transform_encode",
    "tool_invoked", "vault_lease_revoked",
  ]);
  const system = [];
  for (const ev of access.filter((e) => e.ts)) {
    if (ev.type === "http_access" && ev.method === "POST") {
      const host = sessions.find(
        (s) =>
          ev.ts >= s.start &&
          ev.ts <= new Date(s.end.getTime() + 8000) &&
          (ev.path === s.path ||
            String(ev.path || "").includes("/chat/completions") ||
            String(ev.path || "").includes("/user_mcp") ||
            String(ev.path || "").includes("/v1/agent"))
      );
      if (host) {
        host.events.push(ev);
        host.events.sort((a, b) => a.ts - b.ts);
        continue;
      }
    }
    if (ATTACH_TYPES.has(ev.type) && !MESH_TYPES.has(ev.type)) {
      const tool = ev.json?.tool || ev.json?.binding_message || null;
      const host = sessions.find((s) => {
        const userOk = !ev.user || s.user === ev.user || s.user === "desconhecido";
        const toolOk = !tool || !s.tool || s.tool === tool;
        const windowOk = ev.ts >= new Date(s.start.getTime() - 5000) &&
          ev.ts <= new Date(s.end.getTime() + 180000);
        return userOk && toolOk && windowOk;
      });
      if (host) {
        host.events.push(ev);
        host.events.sort((a, b) => a.ts - b.ts);
        host.end = host.events[host.events.length - 1].ts;
        host.durationMs = host.end - host.start;
        if (host.user === "desconhecido" && ev.user) host.user = ev.user;
        if (!host.tool && tool) host.tool = tool;
        continue;
      }
    }
    system.push(ev);
  }

  return { sessions, system };
}

/* ------------------------- PII masking ------------------------------------ */

const PII_PATTERNS = [
  { name: "Cartão", re: /\b(\d{4})-(\d{4})-(\d{4})-(\d{4})\b/g, mask: (m, a, b, c, d) => `••••-••••-••••-${d}` },
  { name: "SSN", re: /\b(\d{3})-(\d{2})-(\d{4})\b/g, mask: (m, a, b, c) => `•••-••-${c}` },
  { name: "Telefone", re: /\+\d-\d{3}-\d{3}-(\d{4})/g, mask: (m, last) => `+•-•••-•••-${last}` },
  { name: "IP", re: /\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b/g, mask: () => `•••.•••.•••.•••` },
  { name: "Email", re: /\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b/g, mask: (m, f, dom) => `${f}•••@${dom}` },
];

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderSensitive(text, masked) {
  let html = escapeHtml(text);
  for (const p of PII_PATTERNS) {
    html = html.replace(p.re, (...args) => {
      const original = args[0];
      if (masked) return `<span class="pii masked" title="${p.name} mascarado">${p.mask(...args)}</span>`;
      return `<span class="pii" title="${p.name} exposto">${original}</span>`;
    });
  }
  return html;
}

/* ---------------------------- Formatting ---------------------------------- */

function fmtTime(d) {
  if (!d) return "—";
  return d.toLocaleTimeString("pt-BR", { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}
function fmtDateTime(d) {
  if (!d) return "—";
  return d.toLocaleString("pt-BR", { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}
function fmtDur(ms) {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(2)}s`;
}
function initials(user) {
  const name = user.split("@")[0].replace(/[._]/g, " ").trim();
  const parts = name.split(/\s+/);
  return ((parts[0]?.[0] || "?") + (parts[1]?.[0] || "")).toUpperCase();
}
function nodeIcon(meta) {
  return `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${meta.icon}"/></svg>`;
}

const OUTCOME_META = {
  issued: { label: "Token emitido", cls: "ok", desc: "Vault gerou um novo token OBO" },
  cached: { label: "Token reutilizado", cls: "cache", desc: "token de cache, sem chamada ao Vault" },
  denied: { label: "Acesso negado", cls: "fail", desc: "Vault recusou a troca de token" },
  "n/a": { label: "—", cls: "", desc: "" },
};

/* --------------------------- Timeline view -------------------------------- */

function eventDescription(ev) {
  const j = ev.json || {};
  switch (ev.type) {
    case "agent_request_started":
      return `Usuário enviou: <code>${escapeHtml(j.user_message || "")}</code>`;
    case "identity_broker_call":
      return `Solicitando token OBO com escopo <code>${escapeHtml(j.scope || "")}</code> em <code>${escapeHtml(j.token_exchange_url || "")}</code>`;
    case "obo_token_exchange_completed":
      return `Token on-behalf-of emitido${j.obo_token_present ? " (presente)" : ""}. Expira em <code>${j.expiry_time ? new Date(j.expiry_time * 1000).toLocaleString("pt-BR") : "—"}</code>`;
    case "scoped_tool_token_exchange_failed":
      return `Falha ao obter token para <code>${escapeHtml(j.tool || "")}</code> (escopos <code>${escapeHtml((j.required_scopes || []).join(", "))}</code>): <code>${escapeHtml(j.error_message || j.error || "")}</code>`;
    case "scoped_tool_invoke":
      return `Invocando <code>${escapeHtml(j.tool || "")}</code> exigindo escopos <code>${escapeHtml((j.required_scopes || []).join(", "))}</code>`;
    case "response_sent":
      return `Agente finalizou o processamento &middot; HTTP <code>${j.status_code || ""}</code>`;
    case "token_poll":
      return `<code>${ev.method} ${escapeHtml(ev.path)}</code> &rarr; <code>${ev.status} ${escapeHtml(ev.statusText)}</code> &middot; agente buscando seus tokens de serviço`;
    case "http_access":
      return `<code>${ev.method} ${escapeHtml(ev.path)}</code> &rarr; <code>${ev.status} ${escapeHtml(ev.statusText)}</code>`;
    case "verify_obo_token_exchange":
      return `Token OBO emitido para escopo <code>${escapeHtml(j.scope || "")}</code> em <code>${j.duration_ms != null ? j.duration_ms + "ms" : "—"}</code>${j.cache_hit ? " (cache hit)" : ""}`;
    case "verify_obo_http_error": {
      const errCode = j.verify_error?.code || "";
      const errDetail = j.verify_error?.detail || j.error || "";
      return `IBM Verify retornou HTTP <code>${j.status_code || "—"}</code>${errCode ? ` &mdash; <code>${escapeHtml(errCode)}</code>` : ""}${errDetail ? `: ${escapeHtml(String(errDetail).slice(0, 120))}` : ""}`;
    }
    case "verify_obo_retry":
      return `Tentativa <code>${j.attempt_number ?? "?"}</code> &mdash; próxima em <code>${j.next_sleep_seconds != null ? j.next_sleep_seconds + "s" : "—"}</code>${j.error ? `: <span class="err-inline">${escapeHtml(j.error)}</span>` : ""}`;
    case "obo_token_exchange_internal_error":
      return `Todas as tentativas esgotadas &mdash; token OBO não obtido${j.error ? `: <span class="err-inline">${escapeHtml(j.error)}</span>` : ""}`;
    case "obo_token_exchange_authz_denied":
      return `Troca OBO NEGADA &mdash; <code>${escapeHtml(j.error || "")}</code>`;
    case "identity_broker_starting":
      return `Identity Broker inicializando${j.message ? `: ${escapeHtml(j.message)}` : ""}`;
    case "settings_loaded":
      return `Configurações carregadas${j.message ? `: ${escapeHtml(j.message)}` : ""}`;
    case "tool_invoked":
      return `Ferramenta <code>${escapeHtml(j.tool || "")}</code> executada com escopo <code>${escapeHtml((j.required_scopes || []).join(", "))}</code>${j.total_count != null ? ` &middot; <code>${j.total_count}</code> resultado(s)` : ""}`;
    case "web_conversation_started":
      return `Nova conversa iniciada &middot; <code>${escapeHtml(j.request_path || "/api/agent/query")}</code>`;
    case "web_agent_retry":
      return `Retry <code>${j.attempt ?? "?"}</code>/<code>${j.maxAttempts ?? 3}</code> ao agent service &middot; delay <code>${j.delayMs ?? "—"}ms</code>${j.err ? `: <span class="err-inline">${escapeHtml(j.err)}</span>` : ""}`;
    case "web_agent_error":
      return `Agent service retornou HTTP <code>${j.status || "—"}</code>${j.body ? `: <span class="err-inline">${escapeHtml(j.body)}</span>` : ""}`;
    case "web_auth_callback":
      return `Autenticação OIDC concluída &middot; <code>${escapeHtml(j.request_path || "/api/auth/callback")}</code>`;
    case "web_auth_logout":
      return `Sessão encerrada &middot; <code>${escapeHtml(j.request_path || "/api/auth/logout")}</code>`;
    case "token_chain_subject":
    case "token_chain_actor": {
      const decision = j.PDP_Decision || "";
      const token = j.token ? ` <code>${escapeHtml(j.token)}</code>` : "";
      const enforce = j.enforce ? ` enforce=<code>${escapeHtml(j.enforce)}</code>` : "";
      const reason = j.reason ? ` reason=<code>${escapeHtml(String(j.reason))}</code>` : "";
      return `cadeia${token} <code>${escapeHtml(decision)}</code>${enforce}${reason}`;
    }
    case "pdp_decision": {
      const decision = j.PDP_Decision || j.decision || "";
      const enforce = j.enforce ? ` enforce=<code>${escapeHtml(j.enforce)}</code>` : "";
      const pep = j.pep ? ` PEP <code>${escapeHtml(j.pep)}</code>` : "";
      const pdp = j.pdp ? ` PDP <code>${escapeHtml(j.pdp)}</code>` : "";
      const who = j.caller ? ` caller <code>${escapeHtml(j.caller)}</code>` :
        j.tool ? ` tool <code>${escapeHtml(j.tool)}</code>` :
        j.path ? ` path <code>${escapeHtml(j.path)}</code>` : "";
      const policy = j.pdp_package ? ` package <code>${escapeHtml(j.pdp_package)}</code>` : "";
      const reason = j.reason ? ` reason=<code>${escapeHtml(String(j.reason))}</code>` : "";
      const loa = j.LoA_Level ? ` LoA ${escapeHtml(String(j.LoA_Level))}/${escapeHtml(String(j.Required_LoA || j.LoA_Level))}` : "";
      return `decisão <code>${escapeHtml(decision)}</code>${enforce}${pep}${pdp}${policy}${who}${reason}${loa}`;
    }
    case "ciba_started":
      return `HITL para <code>${escapeHtml(j.login_hint || ev.user || "")}</code> · tool <code>${escapeHtml(j.binding_message || j.tool || "")}</code>${j.approve_url ? ` · <code>${escapeHtml(j.approve_url)}</code>` : ""}`;
    case "ciba_approved":
      return `Humano aprovou · JWT CIBA (LoA 2) para <code>${escapeHtml(j.login_hint || ev.user || "")}</code>`;
    case "ciba_denied":
      return `CIBA recusado/expirado · <code>${escapeHtml(j.ciba_error || "")}</code>`;
    case "jwt_identity_bound":
      return `user-mcp ligou <code>${escapeHtml(j.preferred_username || ev.user || "")}</code> · modo <code>${escapeHtml(j.pep_mode || "")}</code>${j.agent_id ? ` · agent <code>${escapeHtml(j.agent_id)}</code>` : ""}`;
    case "vault_db_creds_issued":
      return `Mint Postgres · path <code>${escapeHtml(j.creds_path || j.db_creds_path || "")}</code>${j.lease_id ? ` · lease <code>${escapeHtml(String(j.lease_id).slice(0, 24))}…</code>` : ""}`;
    case "db_call":
      return `Postgres <code>${escapeHtml(j.auth_mode || "")}</code> · user <code>${escapeHtml(j.db_username || "")}</code> · <code>${escapeHtml(j.db_creds_path || "")}</code>`;
    case "transform_encode":
      return `Transform <code>${escapeHtml(j.transformation || "")}</code> no role <code>${escapeHtml(j.role_name || "")}</code>`;
    case "vault_lease_revoked":
      return `Lease revogado <code>${escapeHtml(String(j.lease_id || "").slice(0, 24))}</code>`;
    case "mesh_svid":
      return `<code>${escapeHtml(j.namespace || "")}/${escapeHtml(j.service || "")}</code> · <code>${escapeHtml(j.spiffe_id || "")}</code>`;
    case "mesh_intention":
      return `<code>${escapeHtml(j.action || "allow")}</code> <code>${escapeHtml(j.source || "")}</code> → <code>${escapeHtml(j.dest || "")}</code>`;
    case "opa_catalog":
      return `Catálogo MCP no OPA (Vault KV)`;
    case "llm_error":
      return `<span class="err-inline">${escapeHtml((j.message || "").slice(0, 280))}</span>`;
    default:
      return escapeHtml(j.message || ev.type);
  }
}

function eventExtra(ev, masked) {
  const j = ev.json || {};
  let out = "";
  if (ev.type === "response_sent" && j.response_text) {
    out += `<details class="disclosure"><summary>Ver resposta retornada ao usuário</summary>`;
    out += `<div class="response-box">${renderSensitive(j.response_text, masked)}</div></details>`;
  }
  if (ev.type === "verify_obo_http_error") {
    if (j.subject_token_diag) {
      out += `<details class="disclosure"><summary>Diagnóstico subject_token</summary><pre class="json">${escapeHtml(JSON.stringify(j.subject_token_diag, null, 2))}</pre></details>`;
    }
    if (j.actor_token_diag) {
      out += `<details class="disclosure"><summary>Diagnóstico actor_token</summary><pre class="json">${escapeHtml(JSON.stringify(j.actor_token_diag, null, 2))}</pre></details>`;
    }
  }
  const kvs = [];
  if (j.module) kvs.push(["módulo", j.module]);
  if (j.method_name) kvs.push(["função", j.method_name]);
  if (j.hostname) kvs.push(["host", j.hostname]);
  if (j.level) kvs.push(["nível", j.level]);
  if (j.agent_id) kvs.push(["agent pod", j.agent_id]);
  if (j.duration_ms != null && ev.type === "verify_obo_token_exchange") kvs.push(["duração", j.duration_ms + "ms"]);
  if (j.attempt_number != null) kvs.push(["tentativa", String(j.attempt_number)]);
  if (j.attempt != null) kvs.push(["tentativa", `${j.attempt}/${j.maxAttempts ?? 3}`]);
  if (j.url) kvs.push(["url destino", j.url]);
  if (j.operation) kvs.push(["operação", j.operation]);
  if (j.service) kvs.push(["serviço", j.service]);
  if (ev.source) kvs.push(["fonte", ev.source]);
  if (j.caller) kvs.push(["SPIFFE caller", j.caller]);
  if (j.spiffe_id) kvs.push(["SPIFFE", j.spiffe_id]);
  if (j.pep_mode) kvs.push(["PEP mode", j.pep_mode]);
  if (j.pep) kvs.push(["PEP", j.pep]);
  if (j.enforce) kvs.push(["enforce", j.enforce]);
  if (j.pdp) kvs.push(["PDP", j.pdp]);
  if (j.pdp_package) kvs.push(["PDP package", j.pdp_package]);
  if (j.pdp_path) kvs.push(["PDP path", j.pdp_path]);
  if (j.pdp_policy) kvs.push(["política PDP", j.pdp_policy]);
  if (j.reason) kvs.push(["reason", String(j.reason)]);
  if (j.PDP_Decision) kvs.push(["decisão", j.PDP_Decision]);
  if (j.required_scopes) kvs.push(["scopes exigidos", Array.isArray(j.required_scopes) ? j.required_scopes.join(", ") : String(j.required_scopes)]);
  if (j.granted_scopes) kvs.push(["scopes no JWT", Array.isArray(j.granted_scopes) ? j.granted_scopes.join(", ") : String(j.granted_scopes)]);
  if (j.catalog_source) kvs.push(["catálogo source", j.catalog_source]);
  if (j.catalog_dest) kvs.push(["catálogo dest", j.catalog_dest]);
  if (j.ciba_required != null && j.ciba_required !== "") kvs.push(["ciba_required", String(j.ciba_required)]);
  if (kvs.length) {
    out += `<div class="kv">` + kvs.map(([k, v]) => `<span><b>${k}:</b> ${escapeHtml(v)}</span>`).join("") + `</div>`;
  }
  out += `<details class="disclosure"><summary>JSON bruto</summary><pre class="json">${escapeHtml(JSON.stringify(ev.json, null, 2))}</pre></details>`;
  return out;
}

function renderEvent(ev, sessionStart, masked) {
  if (!ev.ts) return "";
  const meta = EVENT_META[ev.type] || EVENT_META.http_access;
  const delta = sessionStart ? ev.ts - sessionStart : 0;
  return `
    <div class="event" style="--node:${meta.color}">
      <div class="event-time">
        ${fmtTime(ev.ts)}
        ${delta > 0 ? `<span class="delta">+${fmtDur(delta)}</span>` : ""}
      </div>
      <div class="event-rail">
        <div class="event-node">${nodeIcon(meta)}</div>
        <div class="event-line"></div>
      </div>
      <div class="event-body">
        <div class="event-title">${meta.title}<span class="event-tag">${meta.tag}</span></div>
        <div class="event-desc">${eventDescription(ev)}</div>
        ${eventExtra(ev, masked)}
      </div>
    </div>`;
}

function renderSession(s, masked) {
  const eventsHtml = s.events.map((ev) => renderEvent(ev, s.start, masked)).join("");
  const oc = OUTCOME_META[s.tokenOutcome];
  const scopeBadge = s.scopes ? `<span class="badge">scope: ${escapeHtml(s.scopes.join(", "))}</span>` : "";
  const toolBadge = s.tool ? `<span class="badge">${escapeHtml(s.tool)}</span>` : "";
  const outcomeBadge = oc.cls ? `<span class="badge ${oc.cls}" title="${oc.desc}">${oc.label}</span>` : "";
  return `
    <article class="session outcome-${s.tokenOutcome}" data-user="${escapeHtml(s.user)}" data-request-id="${escapeHtml(s.requestId)}">
      <div class="session-head">
        <div class="avatar">${initials(s.user)}</div>
        <div class="session-meta">
          <span class="session-user">${escapeHtml(s.user)}</span>
          <span class="session-query">${s.query ? `"<b>${escapeHtml(s.query)}</b>" &middot; ` : ""}${escapeHtml(s.requestId)}</span>
        </div>
        <div class="session-spacer"></div>
        <div class="session-badges">
          ${outcomeBadge}${toolBadge}${scopeBadge}
          <span class="badge time">${fmtDur(s.durationMs)}</span>
          <svg class="chevron" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>
        </div>
      </div>
      <div class="events">${eventsHtml}</div>
    </article>`;
}

function renderSystemSession(system, masked) {
  if (!system.length) return "";
  const start = system[0].ts;
  const eventsHtml = system.map((ev) => renderEvent(ev, start, masked)).join("");
  const hasStartup = system.some((e) => ["identity_broker_starting", "settings_loaded"].includes(e.type));
  const label = hasStartup ? "Eventos de sistema / inicialização" : "Tráfego HTTP de sistema";
  const sub = hasStartup
    ? `${system.length} eventos de startup e polling (sem request_id)`
    : `${system.length} consultas de token / polling (sem request_id)`;
  return `
    <article class="session collapsed" data-user="__system__">
      <div class="session-head">
        <div class="avatar">SYS</div>
        <div class="session-meta">
          <span class="session-user">${label}</span>
          <span class="session-query">${sub}</span>
        </div>
        <div class="session-spacer"></div>
        <div class="session-badges">
          <span class="badge">${system.length} eventos</span>
          <svg class="chevron" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>
        </div>
      </div>
      <div class="events">${eventsHtml}</div>
    </article>`;
}

/* --------------------------- Vault audit view ----------------------------- */

const AUDIT_META = {
  identity_broker_call: { result: "request", label: "Solicitou troca de token OBO", color: "var(--c-broker)" },
  obo_token_exchange_completed: { result: "ok", label: "Token OBO emitido pelo Vault", color: "var(--c-obo)" },
  scoped_tool_token_exchange_failed: { result: "fail", label: "Troca de token NEGADA", color: "var(--c-fail)" },
  scoped_tool_invoke: { result: "use", label: "Token usado para invocar ferramenta", color: "var(--c-tool)" },
  token_poll: { result: "poll", label: "Agente consultou seus tokens de serviço", color: "var(--c-http)" },
  verify_obo_token_exchange: { result: "ok", label: "Token OBO emitido (IBM Verify)", color: "var(--c-obo)" },
  verify_obo_http_error: { result: "fail", label: "Erro HTTP no IBM Verify", color: "var(--c-fail)" },
  verify_obo_retry: { result: "retry", label: "Tentativa de reenvio ao IBM Verify", color: "var(--c-broker)" },
  obo_token_exchange_internal_error: { result: "fail", label: "Falha interna — token OBO não obtido", color: "var(--c-fail)" },
  obo_token_exchange_authz_denied: { result: "fail", label: "Troca OBO negada (autorização)", color: "var(--c-fail)" },
  tool_invoked: { result: "use", label: "Ferramenta MCP executada (user-mcp)", color: "var(--c-tool)" },
  web_agent_error: { result: "fail", label: "Erro no agent service (web app)", color: "var(--c-fail)" },
  pdp_decision: { result: "ok", label: "Decisão PEP (LiteLLM / OPA)", color: "var(--c-obo)" },
  ciba_started: { result: "request", label: "CIBA iniciado", color: "var(--c-broker)" },
  ciba_approved: { result: "ok", label: "CIBA aprovado (identidade LoA 2)", color: "var(--c-obo)" },
  ciba_denied: { result: "fail", label: "CIBA negado", color: "var(--c-fail)" },
  vault_db_creds_issued: { result: "ok", label: "Vault mintou credencial Postgres", color: "var(--c-obo)" },
  db_call: { result: "use", label: "Postgres com credencial dinâmica", color: "var(--c-tool)" },
  transform_encode: { result: "use", label: "Vault Transform (PII)", color: "var(--c-broker)" },
  jwt_identity_bound: { result: "ok", label: "JWT ligado no user-mcp", color: "var(--c-request)" },
};

const RESULT_CHIP = {
  request: "solicitação",
  ok: "emitido",
  fail: "negado",
  use: "uso de token",
  poll: "polling",
  retry: "retry",
};

function buildVaultEvents() {
  const rows = [];
  const pushFrom = (ev, fallbackUser) => {
    const meta = AUDIT_META[ev.type];
    if (!meta) return;
    const j = ev.json || {};
    rows.push({
      ts: ev.ts,
      user: ev.user || fallbackUser || "ai-agent (serviço)",
      requestId: ev.requestId,
      type: ev.type,
      meta,
      scope: (j.required_scopes && j.required_scopes.join(", ")) || j.scope || null,
      tool: j.tool || null,
      expiry: j.expiry_time ? new Date(j.expiry_time * 1000) : null,
      error: j.error_message || (j.verify_error?.detail) || j.error || null,
      brokerUrl: j.token_exchange_url || j.http_path || null,
      pep: j.pep || null,
      enforce: j.enforce || null,
      pdp: j.pdp || null,
      pdpPolicy: j.pdp_policy || null,
      reason: j.reason || null,
    });
  };
  for (const s of STATE.sessions) {
    for (const ev of s.events) pushFrom(ev, s.user);
  }
  for (const ev of STATE.system) pushFrom(ev, null);
  rows.sort((a, b) => a.ts - b.ts);
  return rows;
}

function renderUserSummary() {
  const map = new Map();
  for (const s of STATE.sessions) {
    if (!map.has(s.user)) map.set(s.user, { issued: 0, cached: 0, denied: 0, total: 0 });
    const m = map.get(s.user);
    m.total++;
    if (s.tokenOutcome === "issued") m.issued++;
    else if (s.tokenOutcome === "cached") m.cached++;
    else if (s.tokenOutcome === "denied") m.denied++;
  }
  const rows = [...map.entries()]
    .map(
      ([user, m]) => `
      <tr>
        <td><span class="u-avatar">${initials(user)}</span>${escapeHtml(user)}</td>
        <td class="num">${m.total}</td>
        <td class="num"><span class="pill ok">${m.issued}</span></td>
        <td class="num"><span class="pill cache">${m.cached}</span></td>
        <td class="num"><span class="pill fail">${m.denied}</span></td>
      </tr>`
    )
    .join("");
  return `
    <div class="audit-summary">
      <h3>Quem acessou o Vault</h3>
      <table class="u-table">
        <thead><tr><th>Identidade</th><th class="num">Requisições</th><th class="num">Tokens emitidos</th><th class="num">Cache</th><th class="num">Negados</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderVaultAudit() {
  const rows = buildVaultEvents().filter((r) => {
    if (STATE.userFilter !== "all" && r.user !== STATE.userFilter) return false;
    const q = STATE.search.trim().toLowerCase();
    if (!q) return true;
    return [r.user, r.requestId, r.scope, r.tool, r.meta.label].join(" ").toLowerCase().includes(q);
  });

  const feed = rows
    .map((r) => {
      const details = [];
      if (r.scope) details.push(`<span><b>escopo:</b> <code>${escapeHtml(r.scope)}</code></span>`);
      if (r.tool) details.push(`<span><b>ferramenta:</b> <code>${escapeHtml(r.tool)}</code></span>`);
      if (r.expiry) details.push(`<span><b>expira:</b> ${escapeHtml(r.expiry.toLocaleString("pt-BR"))}</span>`);
      if (r.brokerUrl) details.push(`<span><b>broker:</b> <code>${escapeHtml(r.brokerUrl)}</code></span>`);
      if (r.pep) details.push(`<span><b>PEP:</b> <code>${escapeHtml(r.pep)}</code></span>`);
      if (r.enforce) details.push(`<span><b>enforce:</b> <code>${escapeHtml(r.enforce)}</code></span>`);
      if (r.pdp) details.push(`<span><b>PDP:</b> <code>${escapeHtml(r.pdp)}</code></span>`);
      if (r.pdpPolicy) details.push(`<span><b>política:</b> <code>${escapeHtml(r.pdpPolicy)}</code></span>`);
      if (r.reason) details.push(`<span><b>reason:</b> <code>${escapeHtml(String(r.reason))}</code></span>`);
      if (r.error) details.push(`<span class="err"><b>erro:</b> ${escapeHtml(r.error)}</span>`);
      if (r.requestId) details.push(`<span><b>request:</b> <code>${escapeHtml(r.requestId)}</code></span>`);
      return `
        <div class="audit-row result-${r.meta.result}" style="--node:${r.meta.color}">
          <div class="audit-time">${fmtDateTime(r.ts)}</div>
          <div class="audit-dot"></div>
          <div class="audit-body">
            <div class="audit-head">
              <span class="audit-user">${escapeHtml(r.user)}</span>
              <span class="audit-action">${r.meta.label}</span>
              <span class="result-chip ${r.meta.result}">${RESULT_CHIP[r.meta.result]}</span>
            </div>
            <div class="kv">${details.join("")}</div>
          </div>
        </div>`;
    })
    .join("");

  return renderUserSummary() + `<div class="audit-feed">${feed}</div>`;
}

/* --------------------------- Hop / fluxo view ----------------------------- */

const HOP_DEFS = [
  {
    id: "mesh-svid",
    title: "mTLS / SVID Consul Connect",
    role: "Consul",
    responsibility: "Mint SPIFFE no sidecar consul-dataplane. Service Intentions decidem quem fala com quem. Não decide tool nem CIBA.",
    match: (e) => e.type === "mesh_svid" || e.type === "mesh_intention",
  },
  {
    id: "web",
    title: "Browser → web (API Gateway)",
    role: "Consul API Gateway + web",
    responsibility: "TLS norte-sul (:8080). Login Keycloak. Encaminha o chat para o LiteLLM.",
    match: (e) => ["web_conversation_started", "web_auth_callback"].includes(e.type),
  },
  {
    id: "admit-web",
    title: "Admissão LiteLLM (SPIFFE web)",
    role: "LiteLLM pdp_auth.py",
    responsibility: "Lua no inbound copia uriSanPeerCertificate para x-mesh-caller-spiffe. Só default/web e default/ai-agent entram como mesh.",
    match: (e) => e.type === "pdp_decision" && String(e.json?.caller || "").includes("web") && !e.json?.tool,
  },
  {
    id: "subject-jwt",
    title: "Cadeia — subject JWT (cada mensagem)",
    role: "web-app + LiteLLM PEP",
    responsibility: "JWKS Keycloak no access token da sessão (aud=token-exchange, exp, iss). BFF recusa o chat; o PEP recusa o /v1/agent se o Bearer não conferir.",
    match: (e) => e.type === "token_chain_subject",
  },
  {
    id: "actor-jwt",
    title: "Cadeia — actor JWT (cada mensagem)",
    role: "ai-agent + Vault",
    responsibility: "Relê /vault/secrets/actor-token e exige exp ainda válido antes do LLM. Assinatura fica com o vault-agent.",
    match: (e) => e.type === "token_chain_actor",
  },
  {
    id: "agent",
    title: "LiteLLM → ai-agent",
    role: "ai-agent",
    responsibility: "Orquestra o LLM e dispara tools MCP. Não é PDP de tools/call.",
    match: (e) => e.type === "agent_request_started" || e.type === "web_agent_error",
  },
  {
    id: "llm",
    title: "LiteLLM → LLM (qwen-local / Ollama)",
    role: "LiteLLM AI Gateway",
    responsibility: "Proxy de /v1/chat/completions. Sem Ollama em :11434 este hop 500 e o MCP nem começa.",
    match: (e) =>
      e.type === "llm_error" ||
      (e.type === "http_access" && String(e.path || "").includes("/chat/completions")),
  },
  {
    id: "obo",
    title: "Mint identidade OBO",
    role: "token-exchange + Keycloak",
    responsibility: "RFC 8693: JWT do usuário → JWT aud=user-mcp com scope. Actor token do Vault no broker.",
    match: (e) => ["identity_broker_call", "obo_token_exchange_completed", "verify_obo_token_exchange"].includes(e.type),
  },
  {
    id: "admit-agent",
    title: "Admissão LiteLLM (SPIFFE ai-agent)",
    role: "LiteLLM pdp_auth.py",
    responsibility: "tools/call chega de default/ai-agent. Authorization continua o JWT OBO.",
    match: (e) => e.type === "pdp_decision" && String(e.json?.caller || "").includes("ai-agent"),
  },
  {
    id: "mcp-pep",
    title: "PEP tools/call → OPA",
    role: "LiteLLM pdp_mcp.py + OPA mcp.pep",
    responsibility: "Valida JWT OBO/CIBA (JWKS), pergunta catálogo / scope / CIBA. ALLOW, DENY ou STEP_UP. Terceiro elo da cadeia.",
    match: (e) => e.type === "pdp_decision" && e.json?.tool,
  },
  {
    id: "ciba",
    title: "Step-up CIBA (HITL)",
    role: "LiteLLM + Keycloak + ciba-channel",
    responsibility: "OPA marca ciba_required. O PEP faz poll. Approve em localhost:8082. JWT LoA 2 substitui o OBO.",
    optional: true,
    optionalNote: "Não dispara em list/search/update. Só create_user. delete_user_by_email é recusada no PEP.",
    match: (e) => ["ciba_started", "ciba_approved", "ciba_denied"].includes(e.type) || e.json?.PDP_Decision === "STEP_UP_REQUIRED",
  },
  {
    id: "runtime-jwt",
    title: "user-mcp liga a identidade",
    role: "user-mcp runtime",
    responsibility: "Não valida JWKS. Confia no Bearer que o LiteLLM já autorizou.",
    match: (e) => e.type === "jwt_identity_bound",
  },
  {
    id: "vault-creds",
    title: "Vault mint credencial Postgres",
    role: "Vault ORS + user-mcp",
    responsibility: "database/creds/{read|write}-role. JWT OBO/CIBA como X-Vault-Token. Vault não decide a tool.",
    match: (e) => ["vault_db_creds_issued", "db_call"].includes(e.type),
  },
  {
    id: "transform",
    title: "Vault Transform (PII)",
    role: "Vault Transform + user-mcp",
    responsibility: "Mascara campos nas leituras se groups não contém admin.",
    match: (e) => e.type === "transform_encode",
  },
  {
    id: "tool",
    title: "Tool MCP executada",
    role: "user-mcp runtime",
    responsibility: "SQL com a credencial de um hop: list / search / create / update / delete.",
    match: (e) => ["tool_invoked", "scoped_tool_invoke"].includes(e.type),
  },
  {
    id: "response",
    title: "Resposta ao usuário",
    role: "ai-agent",
    responsibility: "Fecha o request_id e devolve o texto (PII pode estar mascarado).",
    match: (e) => e.type === "response_sent",
  },
];

function allEvents() {
  const out = [];
  for (const s of STATE.sessions) out.push(...s.events);
  out.push(...STATE.system);
  return out;
}

function hopPolicyBlock(ev) {
  if (!ev) return "";
  const j = ev.json || {};
  const rows = [
    ["PEP", j.pep],
    ["enforce", j.enforce],
    ["PDP", j.pdp],
    ["package", j.pdp_package],
    ["path PDP", j.pdp_path],
    ["política", j.pdp_policy],
    ["decisão", j.PDP_Decision || j.decision],
    ["reason", j.reason],
    ["tool", j.tool],
    ["caller", j.caller],
    ["scopes exigidos", Array.isArray(j.required_scopes) ? j.required_scopes.join(", ") : j.required_scopes],
    ["scopes no JWT", Array.isArray(j.granted_scopes) ? j.granted_scopes.join(", ") : j.granted_scopes],
    ["catálogo", j.catalog_source && j.catalog_dest ? `${j.catalog_source} → ${j.catalog_dest}` : null],
    ["ciba_required", j.ciba_required == null || j.ciba_required === "" ? null : String(j.ciba_required)],
    ["LoA", j.LoA_Level != null ? `${j.LoA_Level}/${j.Required_LoA || j.LoA_Level}` : null],
  ].filter(([, v]) => v != null && v !== "" && v !== "-");
  if (!rows.length) return "";
  return `<dl class="hop-policy">${rows.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd><code>${escapeHtml(String(v))}</code></dd></div>`).join("")}</dl>`;
}

function renderHops() {
  const events = allEvents();
  const hops = HOP_DEFS.map((hop) => {
    const hits = events.filter(hop.match).sort((a, b) => (a.ts || 0) - (b.ts || 0));
    return { ...hop, hits };
  });
  const seen = hops.filter((h) => h.hits.length).length;
  const tools = [...new Set(events.map((e) => e.json?.tool).filter(Boolean))];
  const callers = [...new Set(events.filter((e) => e.type === "pdp_decision" && e.json?.caller).map((e) => e.json.caller))];
  const snap = STATE.snapshot;

  let identHtml = "";
  if (snap && Array.isArray(snap.identities) && snap.identities.length) {
    identHtml = `
      <div class="audit-summary hop-ident">
        <h3>Identidades mintadas (Consul Connect SVID)</h3>
        <p class="hop-note">Trust domain <code>${escapeHtml(snap.trust_domain)}</code> · ${escapeHtml(snap.collected_at || "")}</p>
        <table class="u-table">
          <thead><tr><th>Serviço</th><th>SPIFFE</th><th>Quem minta</th></tr></thead>
          <tbody>
            ${snap.identities.map((id) => `
              <tr>
                <td><code>${escapeHtml(id.namespace)}/${escapeHtml(id.service)}</code></td>
                <td><code>${escapeHtml(id.spiffe_id)}</code></td>
                <td>${escapeHtml(id.mint || "Consul Connect")}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>`;
  }

  const hopCards = hops.map((h) => {
    const seenCls = h.hits.length ? "seen" : h.optional ? "skip" : "miss";
    const evidence = h.hits.slice(-3).map((ev) => {
      const t = ev.ts ? fmtTime(ev.ts) : "";
      return `<li><span class="hop-ev-time">${t}</span> ${eventDescription(ev)}</li>`;
    }).join("");
    const status = h.hits.length
      ? h.hits.length + " evidência(s)"
      : h.optional
        ? (h.optionalNote || "não se aplica neste fluxo")
        : "ainda não visto";
    const lastPep = [...h.hits].reverse().find((ev) => ev.json && (ev.json.pep || ev.json.pdp_policy || ev.json.enforce));
    return `
      <article class="hop ${seenCls}">
        <div class="hop-top">
          <span class="hop-role">${escapeHtml(h.role)}</span>
          <span class="hop-count">${escapeHtml(status)}</span>
        </div>
        <h3>${escapeHtml(h.title)}</h3>
        <p>${escapeHtml(h.responsibility)}</p>
        ${hopPolicyBlock(lastPep || h.hits[h.hits.length - 1])}
        ${evidence ? `<ul class="hop-evidence">${evidence}</ul>` : ""}
      </article>`;
  }).join("");

  return `
    <div class="hop-legend">
      <div class="stat-card" style="--bar:var(--c-obo)"><div class="stat-value">${seen}/${hops.length}</div><div class="stat-label">Hops com evidência</div><div class="stat-sub">stream ao vivo</div></div>
      <div class="stat-card" style="--bar:var(--c-tool)"><div class="stat-value">${tools.length || "—"}</div><div class="stat-label">Tools vistas</div><div class="stat-sub">${escapeHtml(tools.join(", ") || "use o chat em :8080")}</div></div>
      <div class="stat-card" style="--bar:var(--c-request)"><div class="stat-value">${callers.length || "—"}</div><div class="stat-label">Callers SPIFFE</div><div class="stat-sub">${escapeHtml(callers.join(", ") || "aguardando pdp_auth")}</div></div>
    </div>
    ${identHtml}
    <div class="hop-grid">${hopCards}</div>`;
}

/* ----------------------------- Stats -------------------------------------- */

function renderStats() {
  const { sessions, system } = STATE;
  const users = new Set(sessions.map((s) => s.user));
  const BROKER_TYPES = new Set(["identity_broker_call", "verify_obo_token_exchange", "verify_obo_http_error"]);
  const brokerCalls = sessions.reduce((n, s) => n + s.events.filter((e) => BROKER_TYPES.has(e.type)).length, 0);
  const issued = sessions.filter((s) => s.tokenOutcome === "issued").length;
  const cached = sessions.filter((s) => s.tokenOutcome === "cached").length;
  const denied = sessions.filter((s) => s.tokenOutcome === "denied").length;
  const polls = sessions.reduce((n, s) => n + s.events.filter((e) => e.type === "token_poll").length, 0) +
    system.filter((e) => e.type === "token_poll").length;
  const oboRetries = sessions.reduce((n, s) => n + s.events.filter((e) => e.type === "verify_obo_retry").length, 0);
  const webRetries = sessions.reduce((n, s) => n + s.events.filter((e) => e.type === "web_agent_retry").length, 0);
  const retries = oboRetries + webRetries;

  const cards = [
    { value: sessions.length, label: "Requisições / trocas OBO", sub: `${users.size} identidades distintas`, bar: "var(--c-request)" },
    { value: brokerCalls, label: "Chamadas ao provedor OBO", sub: "trocas de token solicitadas", bar: "var(--c-broker)" },
    { value: issued, label: "Tokens OBO emitidos", sub: "credencial nova gerada", bar: "var(--c-obo)" },
    { value: cached, label: "Tokens reutilizados", sub: "cache, sem ir ao provedor", bar: "var(--c-tool)" },
    { value: denied, label: "Acessos negados", sub: "token recusado / falha interna", bar: "var(--c-fail)" },
    { value: retries || polls,
      label: oboRetries ? "Retries ao IBM Verify" : webRetries ? "Retries no agent service" : "Consultas de token",
      sub: oboRetries ? "reenvios OBO por falha transitória" : webRetries ? "falhas de conectividade (web→agent)" : "polling de tokens",
      bar: "var(--c-http)" },
  ];

  document.getElementById("stats").innerHTML = cards
    .map(
      (c) => `<div class="stat-card" style="--bar:${c.bar}">
        <div class="stat-value">${escapeHtml(c.value)}</div>
        <div class="stat-label">${c.label}</div>
        <div class="stat-sub">${escapeHtml(c.sub)}</div>
      </div>`
    )
    .join("");
}

function getAllSources() {
  const sources = new Set();
  for (const s of STATE.sessions)
    for (const ev of s.events) if (ev.source) sources.add(ev.source);
  for (const ev of STATE.system) if (ev.source) sources.add(ev.source);
  return [...sources];
}

function renderUserFilters() {
  const users = [...new Set(STATE.sessions.map((s) => s.user))];
  const chips = [`<button class="chip ${STATE.userFilter === "all" ? "active" : ""}" data-user="all">Todos</button>`];
  for (const u of users) {
    chips.push(
      `<button class="chip ${STATE.userFilter === u ? "active" : ""}" data-user="${escapeHtml(u)}"><span class="dot" style="background:var(--c-broker)"></span>${escapeHtml(u.split("@")[0])}</button>`
    );
  }
  document.getElementById("userFilters").innerHTML = chips.join("");
}

function renderSourceFilters() {
  const el = document.getElementById("sourceFilters");
  if (!el) return;
  const sources = getAllSources();
  if (sources.length <= 1) { el.hidden = true; return; }
  el.hidden = false;
  const chips = [`<span class="filter-label">Arquivo:</span><button class="chip ${STATE.sourceFilter === "all" ? "active" : ""}" data-source="all">Todos</button>`];
  sources.forEach((src, i) => {
    const label = src.replace(/\.(rtf|txt|log|jsonl|json)$/i, "");
    const color = SOURCE_COLORS[i % SOURCE_COLORS.length];
    chips.push(
      `<button class="chip ${STATE.sourceFilter === src ? "active" : ""}" data-source="${escapeHtml(src)}" title="${escapeHtml(src)}"><span class="dot" style="background:${color}"></span>${escapeHtml(label)}</button>`
    );
  });
  el.innerHTML = chips.join("");
}

/* --------------------------- Main render ---------------------------------- */

function matchesSearch(s, q) {
  if (!q) return true;
  const hay = [
    s.user,
    s.query,
    s.requestId,
    s.tool,
    (s.scopes || []).join(" "),
    s.events.map((e) => e.json && (e.json.message || e.json.response_text || "")).join(" "),
  ].join(" ").toLowerCase();
  return hay.includes(q.toLowerCase());
}

function render() {
  const hasData = STATE.sessions.length > 0 || STATE.system.length > 0;
  const clearBtn = document.getElementById("clearBtn");
  if (clearBtn) clearBtn.hidden = !hasData;

  renderStats();
  renderUserFilters();
  renderSourceFilters();

  const tl = document.getElementById("timeline");
  const prevScroll = tl.scrollTop;
  const nearBottom = tl.scrollHeight - tl.clientHeight - prevScroll < 80;
  const collapsedIds = new Set(
    [...tl.querySelectorAll(".session.collapsed")].map((el) => el.dataset.requestId || el.dataset.user)
  );
  const maskWrap = document.getElementById("maskToggle").closest(".toggle");
  const statsEl = document.getElementById("stats");

  if (STATE.view === "hops") {
    tl.classList.add("vault-view");
    if (maskWrap) maskWrap.style.display = "none";
    if (statsEl) statsEl.style.display = "none";
    tl.innerHTML = renderHops();
    document.getElementById("emptyState").hidden = true;
    restoreLiveScroll(tl, prevScroll, nearBottom, collapsedIds);
    return;
  }
  if (statsEl) statsEl.style.display = "";

  if (STATE.view === "vault") {
    tl.classList.add("vault-view");
    if (maskWrap) maskWrap.style.display = "none";
    tl.innerHTML = renderVaultAudit();
    document.getElementById("emptyState").hidden = tl.querySelector(".audit-row") != null;
    restoreLiveScroll(tl, prevScroll, nearBottom, collapsedIds);
    return;
  }

  tl.classList.remove("vault-view");
  if (maskWrap) maskWrap.style.display = "";

  const q = STATE.search.trim();
  const visible = STATE.sessions.filter(
    (s) =>
      (STATE.userFilter === "all" || s.user === STATE.userFilter) &&
      (STATE.sourceFilter === "all" || s.events.some((e) => e.source === STATE.sourceFilter)) &&
      matchesSearch(s, q)
  );

  let html = visible.map((s) => renderSession(s, STATE.mask)).join("");
  const sysVisible = STATE.sourceFilter === "all"
    ? STATE.system
    : STATE.system.filter((e) => e.source === STATE.sourceFilter);
  if (STATE.userFilter === "all" && !q) html += renderSystemSession(sysVisible, STATE.mask);

  tl.innerHTML = html;
  restoreLiveScroll(tl, prevScroll, nearBottom, collapsedIds);
  const isEmpty = visible.length === 0 && !STATE.system.length;
  document.getElementById("emptyState").hidden = !isEmpty;
  const emptyMsg = document.getElementById("emptyMsg");
  if (emptyMsg) {
    emptyMsg.textContent = !hasData
      ? "O stream ao vivo está ligado — use o chat em http://localhost:8080."
      : "Nenhum evento corresponde aos filtros atuais.";
  }
}

function restoreLiveScroll(tl, prevScroll, nearBottom, collapsedIds) {
  tl.querySelectorAll(".session").forEach((el) => {
    const id = el.dataset.requestId || el.dataset.user;
    if (collapsedIds.has(id)) el.classList.add("collapsed");
  });
  tl.scrollTop = nearBottom ? tl.scrollHeight : prevScroll;
}

/* ---------------------------- Events / wiring ----------------------------- */

function recomputeOutcome(evs) {
  const has = (t) => evs.some((e) => e.type === t);
  if (has("scoped_tool_token_exchange_failed") || has("obo_token_exchange_internal_error") || has("obo_token_exchange_authz_denied") || has("ciba_denied")) return "denied";
  if (evs.some((e) => e.type === "pdp_decision" && e.json?.PDP_Decision === "DENY")) return "denied";
  if (has("obo_token_exchange_completed") || has("verify_obo_token_exchange") || has("vault_db_creds_issued")) return "issued";
  if (has("scoped_tool_invoke") && !has("identity_broker_call")) return "cached";
  return "n/a";
}

function eventKey(ev) {
  const j = ev.json || {};
  return [ev.tsRaw || "", ev.type, ev.source || "", j.path || "", j.tool || "", j.enforce || j.PDP_Decision || ev.path || ""].join("|");
}

function loadData(raw, source) {
  const { sessions, system, snapshot } = parseLogs(raw, source);
  STATE.sessions = sessions;
  STATE.system = system;
  STATE.snapshot = snapshot || STATE.snapshot;
  STATE.sourceFilter = "all";
  render();
  return sessions.length;
}

function appendData(raw, source) {
  const { sessions: newSessions, system: newSystem, snapshot } = parseLogs(raw, source);
  if (snapshot) STATE.snapshot = snapshot;
  const existingMap = new Map(STATE.sessions.map((s) => [s.requestId, s]));
  let merged = 0, added = 0;

  for (const ns of newSessions) {
    if (existingMap.has(ns.requestId)) {
      const es = existingMap.get(ns.requestId);
      const seen = new Set(es.events.map(eventKey));
      for (const ev of ns.events) {
        if (!seen.has(eventKey(ev))) {
          es.events.push(ev);
          seen.add(eventKey(ev));
        }
      }
      es.events.sort((a, b) => a.ts - b.ts);
      es.start = es.events[0].ts;
      es.end = es.events[es.events.length - 1].ts;
      es.durationMs = es.end - es.start;
      if (es.user === "desconhecido" && ns.user !== "desconhecido") es.user = ns.user;
      if (!es.query && ns.query) es.query = ns.query;
      if (!es.scopes && ns.scopes) es.scopes = ns.scopes;
      if (!es.tool && ns.tool) es.tool = ns.tool;
      es.tokenOutcome = recomputeOutcome(es.events);
      merged++;
    } else {
      existingMap.set(ns.requestId, ns);
      added++;
    }
  }

  STATE.sessions = [...existingMap.values()].sort((a, b) => a.start - b.start);
  const seenSys = new Set(STATE.system.map(eventKey));
  for (const ev of newSystem) {
    if (!seenSys.has(eventKey(ev))) {
      STATE.system.push(ev);
      seenSys.add(eventKey(ev));
    }
  }
  STATE.system.sort((a, b) => (a.ts || 0) - (b.ts || 0));
  render();
  return { merged, added };
}

function clearData() {
  STATE.sessions = [];
  STATE.system = [];
  STATE.snapshot = null;
  STATE.search = "";
  STATE.userFilter = "all";
  STATE.sourceFilter = "all";
  document.getElementById("search").value = "";
  render();
  showToast("Logs apagados");
}

function showToast(msg, isError) {
  const t = document.createElement("div");
  t.className = "toast" + (isError ? " toast-err" : "");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.classList.add("toast-show"), 10);
  setTimeout(() => { t.classList.remove("toast-show"); setTimeout(() => t.remove(), 300); }, 3000);
}

function wire() {
  document.getElementById("search").addEventListener("input", (e) => {
    STATE.search = e.target.value;
    render();
  });

  document.getElementById("userFilters").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    STATE.userFilter = chip.dataset.user;
    render();
  });

  document.getElementById("sourceFilters").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    STATE.sourceFilter = chip.dataset.source;
    render();
  });

  document.getElementById("viewTabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".view-tab");
    if (!tab) return;
    STATE.view = tab.dataset.view;
    document.querySelectorAll(".view-tab").forEach((t) => t.classList.toggle("active", t === tab));
    render();
  });

  document.getElementById("maskToggle").addEventListener("change", (e) => {
    STATE.mask = e.target.checked;
    render();
  });

  document.getElementById("timeline").addEventListener("click", (e) => {
    const head = e.target.closest(".session-head");
    if (head && !e.target.closest("details")) head.parentElement.classList.toggle("collapsed");
  });

  const modal = document.getElementById("loadModal");
  const open = () => { modal.hidden = false; };
  const close = () => { modal.hidden = true; };
  document.getElementById("clearBtn").addEventListener("click", clearData);
  document.getElementById("loadBtn").addEventListener("click", open);
  document.getElementById("closeModal").addEventListener("click", close);
  modal.addEventListener("click", (e) => { if (e.target === modal) close(); });

  document.getElementById("useSample").addEventListener("click", () => {
    document.getElementById("logInput").value = SAMPLE_LOGS;
  });

  const fileInput = document.getElementById("fileInput");

  fileInput.addEventListener("change", (e) => {
    const files = [...e.target.files];
    if (!files.length) return;
    if (files.length === 1) {
      const reader = new FileReader();
      reader.onload = () => {
        document.getElementById("logInput").value = reader.result;
        fileInput.dataset.source = files[0].name;
      };
      reader.readAsText(files[0]);
    } else {
      // Multiple files: auto-process in sequence (first=replace, rest=append)
      let pending = files.length;
      const results = new Array(files.length);
      files.forEach((f, i) => {
        const r = new FileReader();
        r.onload = () => {
          results[i] = { content: r.result, name: f.name };
          if (--pending !== 0) return;
          const n = loadData(results[0].content, results[0].name);
          let mergedTotal = 0, addedTotal = 0;
          for (let j = 1; j < results.length; j++) {
            const res = appendData(results[j].content, results[j].name);
            mergedTotal += res.merged; addedTotal += res.added;
          }
          close();
          showToast(`${files.length} arquivos: ${n + addedTotal} sessão(ões)${mergedTotal ? `, ${mergedTotal} mesclada(s)` : ""}`);
        };
        r.readAsText(f);
      });
    }
  });

  document.getElementById("applyLogs").addEventListener("click", () => {
    const raw = document.getElementById("logInput").value.trim();
    if (!raw) { close(); return; }
    try {
      const source = fileInput.dataset.source || null;
      fileInput.dataset.source = "";
      const n = loadData(raw, source);
      close();
      showToast(n > 0 ? `${n} sessão(ões) carregada(s)` : "Nenhuma sessão encontrada nos logs");
    } catch (err) {
      console.error("Erro ao processar logs:", err);
      showToast("Erro ao processar logs: " + err.message, true);
    }
  });

  document.getElementById("appendLogs").addEventListener("click", () => {
    const raw = document.getElementById("logInput").value.trim();
    if (!raw) { close(); return; }
    try {
      const source = fileInput.dataset.source || null;
      fileInput.dataset.source = "";
      const { merged, added } = appendData(raw, source);
      close();
      const msg = [
        added > 0 && `${added} sessão(ões) nova(s)`,
        merged > 0 && `${merged} mesclada(s) por request_id`,
      ].filter(Boolean).join(", ");
      showToast(msg || "Nenhuma sessão encontrada");
    } catch (err) {
      console.error("Erro ao processar logs:", err);
      showToast("Erro ao processar logs: " + err.message, true);
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  wire();
  const refresh = document.getElementById("refreshDump");
  if (refresh) refresh.hidden = true;
  setLiveStatus("connecting");
  render();
  connectLive();
});

function setLiveStatus(state) {
  const el = document.getElementById("liveStatus");
  if (!el) return;
  el.hidden = false;
  el.classList.remove("ok", "bad", "wait");
  if (state === "live") {
    el.classList.add("ok");
    el.textContent = "ao vivo";
  } else if (state === "connecting") {
    el.classList.add("wait");
    el.textContent = "ligando stream…";
  } else {
    el.classList.add("bad");
    el.textContent = "stream caído — make hop-logs";
  }
}

const _liveBuf = [];
let _liveFlush = null;

function enqueueLive(source, line) {
  _liveBuf.push({ source, line });
  if (_liveFlush) return;
  _liveFlush = setTimeout(() => {
    _liveFlush = null;
    const bySource = new Map();
    for (const item of _liveBuf.splice(0)) {
      bySource.set(item.source, (bySource.get(item.source) || "") + item.line + "\n");
    }
    for (const [src, text] of bySource) appendData(text, src);
  }, 250);
}

function connectLive() {
  if (typeof EventSource === "undefined") {
    setLiveStatus("down");
    return;
  }
  const es = new EventSource("/stream");
  es.onopen = () => setLiveStatus("live");
  es.onerror = () => setLiveStatus("down");
  es.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (err) {
      return;
    }
    if (msg.snapshot) {
      STATE.snapshot = msg.snapshot;
      setLiveStatus("live");
      render();
    }
    if (msg.line) enqueueLive(msg.source || "cluster", msg.line);
  };
}
