# web-app

A Next.js (App Router) rebuild of the original Streamlit app (archived at `../web-app-deprecated/`), styled with the IBM Carbon Design System per the `specs/design/` handoff. Identical functionality — Keycloak OAuth login, AI agent chat with streaming, token-context inspector (subject / actor / OBO), theme toggle, logout — wired through the same `.env` contract.

## Stack

- **Next.js 15** (App Router) + **React 19** + **TypeScript**
- **@carbon/react**, **@carbon/styles** (IBM Carbon v11 tokens, IBM Plex via `next/font`)
- **jose** for JWKS-backed id_token verification
- **iron-session** for HttpOnly + sealed cookies (state, PKCE verifier, session)
- **pino** for structured JSON logs (same field shape as the Streamlit app's `observability.py`)
- **zod** for env + request body validation
- **Vitest** unit tests, **Playwright** E2E (optional)

## Prerequisites

- Node.js **20.18+** (`.nvmrc` provided)
- A Keycloak realm with an OAuth 2.0 application
- (Optional) An AI agent backend exposing `/v1/agent/query` and `/v1/agent/tokens`

## Setup

```bash
cd web-app
cp .env.example .env
# fill in KEYCLOAK_*, AI_AGENT_API_URL, generate SESSION_PASSWORD:
openssl rand -base64 32  # paste into SESSION_PASSWORD

npm install
npm run dev
```

App runs at <http://localhost:8080>.

> **Keycloak config:** add `http://localhost:8080/api/auth/callback` (and your production equivalent) to the application's allowed redirect URIs. The new app's OAuth callback path is `/api/auth/callback`, not `/`.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `KEYCLOAK_CLIENT_ID` | yes | — | OAuth client ID |
| `KEYCLOAK_CLIENT_SECRET` | yes | — | OAuth client secret |
| `KEYCLOAK_TENANT_URL` | yes | — | e.g. `https://your-keycloak.example.com` |
| `KEYCLOAK_REDIRECT_URI` | yes | — | e.g. `http://localhost:8080/api/auth/callback` |
| `KEYCLOAK_SCOPES` | no | `openid profile email Agent.Invoke` | OIDC scopes |
| `AI_AGENT_API_URL` | no | `""` | base URL for the agent backend |
| `AI_AGENT_DNS_RETRY_ATTEMPTS` | no | `3` | max attempts for transient DNS/connect failures to agent upstream |
| `AI_AGENT_DNS_RETRY_BASE_DELAY_MS` | no | `150` | initial retry backoff delay in milliseconds |
| `AI_AGENT_DNS_RETRY_MAX_DELAY_MS` | no | `1000` | cap for exponential retry backoff delay in milliseconds |
| `LOG_LEVEL` | no | `info` | pino log level |
| `LOG_SERVICE_NAME` | no | `verify-vault-web-app` | `service` field in logs |
| `LOG_ENVIRONMENT` | no | `development` | `environment` field in logs |
| `SESSION_PASSWORD` | yes | — | 32+ random chars; seals all auth cookies |

## Scripts

```bash
npm run dev         # next dev on port 8080
npm run build       # production build (output: standalone)
npm run start       # next start on port 8080
npm run lint        # eslint
npm run typecheck   # tsc --noEmit
npm run test        # vitest run
npm run test:e2e    # playwright
npm run format      # prettier write
```

## Architecture

```
Browser ──► /                       Login page (server component)
       └─► /api/auth/login          PKCE + state in HttpOnly cookies, 302 to Keycloak
              └─► Keycloak        user authenticates
                     └─► /api/auth/callback   verify state+PKCE, exchange code, verify id_token,
                                             set sealed session cookie, 302 to /landing
       ──► /landing                 Header + ChatWorkspace (server component, auth-gated)
       ──► POST /api/agent/query    streaming proxy → AI_AGENT_API_URL/v1/agent/query
       ──► GET  /api/agent/tokens   proxy → AI_AGENT_API_URL/v1/agent/tokens
       ──► GET  /api/auth/claims    decoded id_token (Subject token panel)
       ──► GET  /api/auth/me        minimal user info for header
       ──► GET  /api/auth/logout    clear cookies → Keycloak end_session
```

`access_token`, `id_token`, and `client_secret` never reach the browser. The chat client posts `{message, history}` to `/api/agent/query`; the route handler attaches the Bearer token from the sealed cookie before talking to the agent.

## Authentication & token flow

1. User clicks **Login with Keycloak**.
2. `/api/auth/login` generates `state` and a PKCE code verifier, stores both in HttpOnly cookies scoped to `/api/auth/callback`, and redirects to `${TENANT_URL}/oidc/endpoint/default/authorize` with `code_challenge=S256` and `prompt=login`.
3. Keycloak authenticates the user and redirects to `/api/auth/callback?code=…&state=…`.
4. The callback validates `state` (constant-time compare), exchanges the code for tokens (POSTs `client_secret` + `code_verifier` form-encoded), and verifies the `id_token` against the JWKS at `${TENANT_URL}/oidc/endpoint/default/jwks` (RS256, audience = client_id, exp).
5. Tokens + decoded id-token claims + extracted user info are written to a single sealed `verify_session` cookie (HttpOnly, Secure in prod, SameSite=Lax). Expires at the id_token's `exp` (capped at 8h).
6. **Logout** redirects to `${TENANT_URL}/oidc/endpoint/default/logout?post_logout_redirect_uri=…&id_token_hint=…` after clearing local cookies.

## AI agent integration

- Server proxy. Browser → `/api/agent/query` (streaming `text/plain`) → server → `AI_AGENT_API_URL/v1/agent/query` with Bearer `access_token` and `X-Request-ID`.
- Streaming uses Web `ReadableStream`. Both `application/json` (single yield) and `text/plain` (incremental, escape-buffered) responses are normalized exactly like the Streamlit app's `services/response_normalization.py`.
- Token panel calls `GET /api/agent/tokens` after each successful send to pull `actor_token` and `obo_token`; nested payloads under `data|result|response|payload|body` are unwrapped.

## Observability

- All logs are JSON, one per line, written to stdout via `pino`.
- Field shape matches `web-app/observability.py`: `timestamp`, `service`, `environment`, `host`, `hostname`, `host_ip`, `request_id`, `client_ip`, `request_path`, `message`, `level`, `severity`, `logger`, `module`, `process`, `process_name`, `thread`, `thread_name`.
- Request context is propagated via Node's `AsyncLocalStorage`. The wrapper `withRequestContext()` on every route handler binds `request_id` (from `X-Request-ID` header or generated UUID), `client_ip`, `request_path`, and `preferred_username` (from session cookie). Outbound calls re-emit the request id via `buildOutboundHeaders()`.
- A pino mixin redacts sensitive keys (`access_token`, `id_token`, `client_secret`, `code`, `code_verifier`, `authorization`, `cookie`, `set-cookie`) from any logged object.

## Security posture

- HttpOnly + sealed cookies (iron-session, AES-GCM) for all auth state. State and PKCE cookies are scoped to `/api/auth/callback`.
- PKCE S256 + state on every OAuth flow.
- Same-origin guard on `POST /api/agent/*` via middleware.
- HTTP headers in `next.config.mjs`: HSTS (prod), CSP (`default-src 'self'`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, restrictive `Permissions-Policy`.
- zod validates env (fail-fast) and `/api/agent/query` body (`message ≤ 1000 chars`, history ≤ 200 messages).
- Rate limiting is a deliberate TODO (see `app/api/agent/query/route.ts`). Add an upstream limiter (Upstash / Cloudflare) before production.

## Docker

```bash
# single-arch local
docker build -t web-app:dev .
docker run --rm -p 8080:8080 --env-file .env web-app:dev

# multi-arch
docker buildx build --platform linux/amd64,linux/arm64 \
  -t <registry>/web-app:<tag> --push .
```

## Testing

```bash
npm run test           # vitest unit tests
npm run test:e2e       # playwright (boots dev server on 8080)
```

Unit tests cover: response normalization parity, stream-split escape buffer, PKCE verifier/challenge, OAuth URL building, `extractUserInfo`, JWT decode helper, and outbound request-id propagation.

## Specs

- [`specs/architecture.md`](./specs/architecture.md)
- [`specs/auth-flow.md`](./specs/auth-flow.md)
- [`specs/agent-integration.md`](./specs/agent-integration.md)
- [`specs/observability.md`](./specs/observability.md)

## Notes

- The previous Streamlit implementation is archived at `../web-app-deprecated/` for reference; this Next.js app replaces it.
- The app does **not** implement refresh tokens (matching the Streamlit app's behavior); users re-log in when the id_token expires.
