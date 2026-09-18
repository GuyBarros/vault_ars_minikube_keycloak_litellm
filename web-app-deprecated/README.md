# web-app (DEPRECATED)

> ⚠️ **DEPRECATED:** This Streamlit application is no longer maintained. It has been superseded by the Next.js web app located at [`../web-app/`](../web-app/). The contents of this directory are retained for historical reference only and should not be used for new deployments.

A proof-of-concept Streamlit web application that demonstrates:

1. **Login with IBM Verify** — OAuth 2.0 Authorization Code Flow with PKCE-ready state validation and JWT signature verification
2. **AI Agent Chat** — Conversational interface that invokes a remote AI agent via REST API

---

## Table of Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Environment Variables](#environment-variables)
- [Running Locally](#running-locally)
- [Docker](#docker)
- [Kubernetes Base Deployment](#kubernetes-base-deployment)
- [OAuth 2.0 Flow](#oauth-20-flow)
- [AI Agent Integration](#ai-agent-integration)
- [Observability](#observability)
- [Design System](#design-system)
- [Security Considerations](#security-considerations)
- [Dependencies](#dependencies)

---

## Architecture

```
web/
├── app.py                  # Entrypoint: theme injection, OAuth callback, routing
├── pages/
│   ├── landing.py          # Post-login landing page
│   └── chat.py             # AI Agent chat page (auth-gated)
├── auth/
│   ├── oauth.py            # IBM Verify OAuth 2.0 Authorization Code Flow
│   └── session.py          # Session helpers & require_auth() page guard
├── components/
│   ├── login_page.py       # Login hero UI
│   ├── chat_ui.py          # Chat interface
│   └── navbar.py           # Top nav bar (theme toggle, Access Token viewer, Logout)
├── services/
│   └── agent_api.py        # Remote AI agent HTTP client
└── styles/
    └── theme.css           # Global CSS injected via st.markdown
```

### Request Flow

```
Browser
  │
  ├─[GET /] ──────────────────► app.py
  │                                │
  │                          Has ?code= param?
  │                          ┌─── Yes ──► Exchange code for token (auth/oauth.py)
  │                          │            Validate id_token via JWKS
  │                          │            Store in st.session_state
  │                          │            Redirect → pages/landing.py
  │                          │
  │                          └─── No ───► Already authenticated?
  │                                       ├─ Yes ──► Redirect → pages/landing.py
  │                                       └─ No  ──► Render login page
  │
  ├─[GET /chat] ──────────────► pages/chat.py
  │                                │
  │                          require_auth() → check session
  │                          │
  │                          User sends message
  │                                │
  │                          services/agent_api.py ──► Remote AI Agent API
  │                                                         │
  │◄──────────────────────────────────────────────── Response
```

---

## Prerequisites

- Python 3.10+
- An [IBM Security Verify](https://www.ibm.com/products/verify-identity) tenant with an OAuth 2.0 application configured
- A running AI agent endpoint (or a mock server for local testing)

---

## Setup

### 1. Clone and navigate to the web directory

```bash
git clone <repo-url>
cd verify-vault-demo/web
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in all values — see Environment Variables below
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `IBM_VERIFY_CLIENT_ID` | ✅ | OAuth 2.0 client ID from IBM Verify |
| `IBM_VERIFY_CLIENT_SECRET` | ✅ | OAuth 2.0 client secret (never expose client-side) |
| `IBM_VERIFY_TENANT_URL` | ✅ | e.g. `https://your-tenant.verify.ibm.com` |
| `IBM_VERIFY_REDIRECT_URI` | ✅ | Must match IBM Verify app config, e.g. `http://localhost:8501/` |
| `AI_AGENT_API_URL` | ✅ | Remote agent base URL, e.g. `https://your-agent-host` (requests go to `/v1/agent/query`) |
| `LOG_LEVEL` | Optional | Python log level for app and framework logs; defaults to `INFO` |
| `LOG_SERVICE_NAME` | Optional | Service name emitted in structured logs; defaults to `verify-vault-web-app` |
| `LOG_ENVIRONMENT` | Optional | Environment field emitted in structured logs; defaults to `development` |

### IBM Verify Application Configuration

In your IBM Verify tenant:
1. Create a new **Native / Single-Page Application** (or Web application)
2. Set the **Redirect URI** to `http://localhost:8501/` (for local dev)
3. Enable scopes: `openid`, `profile`, `email`
4. Copy the **Client ID** and **Client Secret** to your `.env`

---

## Running Locally

```bash
streamlit run app.py --server.port 8501
```

The app will be available at [http://localhost:8501](http://localhost:8501).

---

## Docker

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) 24+
- [Docker Buildx](https://docs.docker.com/buildx/working-with-buildx/) (included with Docker Desktop; enable on Linux with `docker buildx install`)

### Build for a single platform (local)

```bash
docker build -t agentguard-web-app .
```

### Multi-arch build (linux/amd64 + linux/arm64)

Multi-arch images require a `buildx` builder that supports multiple platforms. Create one if you don't already have one:

```bash
docker buildx create --name multiarch --driver docker-container --use
docker buildx inspect --bootstrap
```

Build and push to a registry in one step:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t <registry>/agentguard-web-app:<tag> \
  --push \
  .
```

To build locally without pushing (useful for testing):

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t agentguard-web-app:latest \
  --load \
  .
```

> **Note:** `--load` only supports a single platform at a time. Use `--push` to produce a true multi-arch manifest in a registry.

### Run the container

Pass environment variables via a file or individual `-e` flags:

```bash
docker run --rm -p 8501:8501 \
  --env-file .env \
  agentguard-web-app:latest
```

The app will be available at [http://localhost:8501](http://localhost:8501).

> **IBM Verify redirect URI:** When running in Docker, ensure `IBM_VERIFY_REDIRECT_URI` in your `.env` matches the URL you use to access the container (e.g. `http://localhost:8501/`).

---

## Kubernetes Base Deployment

Use a Kubernetes `Secret` to carry the application `.env` and mount it into the container as `/app/.env`. This matches the container `WORKDIR` and allows `python-dotenv` to load the file at startup without baking secrets into the image.

### 1. Create the Secret from `.env`

```bash
kubectl create namespace agentguard-web-app

kubectl -n agentguard-web-app create secret generic agentguard-web-app-env \
  --from-file=.env=.env
```

### 2. Create a ServiceAccount for Consul-enabled deployments

When deploying through Consul-integrated platform components, the workload needs a Kubernetes `ServiceAccount`. Create it first and reference it from the deployment:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: agentguard-web-app
  namespace: agentguard-web-app
```

### 3. Deploy the application

This base deployment mounts the secret as a file and binds the pod to the service account created above:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentguard-web-app
  namespace: agentguard-web-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: agentguard-web-app
  template:
    metadata:
      labels:
        app: agentguard-web-app
    spec:
      serviceAccountName: agentguard-web-app
      containers:
        - name: agentguard-web-app
          image: <registry>/<image>:<tag>
          ports:
            - containerPort: 8501
          volumeMounts:
            - name: app-env
              mountPath: /app/.env
              subPath: .env
              readOnly: true
      volumes:
        - name: app-env
          secret:
            secretName: agentguard-web-app-env
            items:
              - key: .env
                path: .env
---
apiVersion: v1
kind: Service
metadata:
  name: agentguard-web-app
  namespace: agentguard-web-app
spec:
  selector:
    app: agentguard-web-app
  ports:
    - name: http
      port: 8501
      targetPort: 8501
```

If your deployment platform uses a higher-level base deployment spec with `volumeMapping`, configure the same secret-backed file mount there:

```yaml
serviceAccountName: agentguard-web-app
volumes:
  - name: app-env
    secret:
      secretName: agentguard-web-app-env
      items:
        - key: .env
          path: .env
volumeMapping:
  - name: app-env
    mountPath: /app/.env
    subPath: .env
    readOnly: true
```

Save the `ServiceAccount` plus the `Deployment` and `Service` manifests to `agentguard-web-app-k8s.yaml`, then apply them:

```bash
kubectl apply -f agentguard-web-app-k8s.yaml
```

> **Redirect URI reminder:** Set `IBM_VERIFY_REDIRECT_URI` in the mounted `.env` to the externally reachable URL for your Kubernetes deployment.

---

## OAuth 2.0 Flow

This app implements the **Authorization Code Flow** as defined in [RFC 6749](https://datatracker.ietf.org/doc/html/rfc6749).

```
Step 1  User clicks "Login with IBM Verify"
        → App builds authorization URL with:
          response_type=code, client_id, redirect_uri, scope=openid profile email
          state=<random CSRF token stored in session>

Step 2  IBM Verify authenticates the user and redirects back:
        http://localhost:8501/?code=<auth_code>&state=<state>

Step 3  app.py detects ?code= in query params
        → Validates state parameter (CSRF check)
        → POST to /oidc/endpoint/default/token with:
          grant_type=authorization_code, code, redirect_uri, client_id, client_secret

Step 4  IBM Verify responds with:
        { access_token, id_token, token_type, expires_in, ... }

Step 5  App validates id_token JWT signature using JWKS endpoint
        Decodes claims: sub, name, email, exp, iat, ...
        Stores in st.session_state: access_token, id_token, user_info

Step 6  Redirects to pages/landing.py (authenticated session)
```

### IBM Verify OIDC Endpoints

| Purpose | URL |
|---|---|
| Authorization | `{IBM_VERIFY_TENANT_URL}/oidc/endpoint/default/authorize` |
| Token exchange | `{IBM_VERIFY_TENANT_URL}/oidc/endpoint/default/token` |
| User info | `{IBM_VERIFY_TENANT_URL}/oidc/endpoint/default/userinfo` |
| JWKS (key verification) | `{IBM_VERIFY_TENANT_URL}/oidc/endpoint/default/jwks` |

### Viewing the Access Token

The navbar includes a **🔑 Access Token** button. Clicking it shows a popover with:
- **Raw Encoded Token** — the full JWT string as issued by IBM Verify
- **Decoded Payload** — the JWT claims as pretty-printed JSON (header and signature are not shown)

---

## AI Agent Integration

Agent calls are handled by `services/agent_api.py`:

```python
invoke_agent(message: str, conversation_history: list, access_token: str) -> dict
```

**Endpoint:** `POST /v1/agent/query`

**Request payload:**
```json
{
  "messages": [
    {"role": "user", "content": "User message text"}
  ],
  "stream": true
}
```

**Authentication:** The user's IBM Verify `access_token` is forwarded as the HTTP header `Authorization: Bearer <access_token>`.

**Response format:** The preferred mode is a streamed `text/plain` response, rendered progressively in the chat UI. The client also accepts non-streaming JSON fallbacks where the reply is returned under `response`, `response_text`, `result`, or `message`.

The function returns a dict with:
- `response` — the agent's reply text, displayed in the chat bubble
- `obo_token` — a delegated On-Behalf-Of JWT (RFC 8693) issued by the agent; displayed in a collapsible **🔗 OBO Token** expander beneath the reply when present

Conversation history is stored in `st.session_state.messages` as a list of `{"role", "content"}` dicts (only the `response` text is stored, not the OBO token) and sent with every request for multi-turn context.

### On-Behalf-Of Token (RFC 8693)

When the AI agent performs downstream calls on behalf of the authenticated user, it returns an `obo_token` — a delegated JWT scoped to the agent. The chat page surfaces this token in a collapsible expander under each agent response so users and developers can inspect it. Only the current turn's token is shown; tokens are not persisted in conversation history.

---

## Observability

Application logs and Streamlit/framework logs are emitted as single-line JSON so they can be shipped directly to Loki or another log aggregator.

### Structured log fields

- `timestamp`
- `service`
- `environment`
- `level` / `severity`
- `logger`
- `module`
- `method` / `function`
- `line`
- `host` / `hostname`
- `host_ip`
- `process`, `process_name`
- `thread`, `thread_name`
- `request_id`
- `client_ip`
- `request_path`
- `message`

Any custom `logging` extras are preserved under `extra`.

### Example log line

```json
{
  "timestamp": "2026-04-03T04:31:52.841613+00:00",
  "service": "verify-vault-web-app",
  "environment": "development",
  "host": "verify-vault-7c98f7b9bc-fx8nm",
  "host_ip": "10.42.1.17",
  "request_id": "4f2d50d8-3552-4d47-b5d7-d07fca5d4b31",
  "client_ip": "203.0.113.24",
  "request_path": "/",
  "level": "INFO",
  "logger": "verify_vault.app",
  "module": "app",
  "method": "<module>",
  "line": 35,
  "message": "Authenticated session detected; redirecting to landing page"
}
```

Query parameters are intentionally excluded from `request_path` so OAuth codes and other sensitive values are not written to logs.

---

## Design System

The UI follows the [IBM Carbon Design System](https://carbondesignsystem.com/) conventions to match the watsonx brand.

### Colors

| Token | Hex | Usage |
|---|---|---|
| `$blue-60` | `#0f62fe` | Primary buttons, links, accents |
| `$blue-70` | `#0043ce` | Button hover state |
| `$gray-100` | `#161616` | Page background (dark theme) |
| `$gray-90` | `#262626` | Card / panel background |
| `$gray-80` | `#393939` | Borders and dividers |
| `$white` | `#ffffff` | Primary text on dark |
| `$gray-30` | `#c6c6c6` | Secondary / muted text |
| `$green-40` | `#42be65` | Success / online indicator |
| `$red-50` | `#fa4d56` | Error states |

### Typography

| Role | Font | Weight |
|---|---|---|
| Body / UI | IBM Plex Sans | 400, 600 |
| Code / Monospace | IBM Plex Mono | 400 |
| Display headings | IBM Plex Sans | 300 (Light) |

### Layout

- **8px base grid** — spacing: 4, 8, 16, 24, 32, 48, 64px
- **Sharp corners** — `border-radius: 0` throughout
- **No drop shadows** — use `1px solid #393939` borders for separation
- **Chat bubbles** — user messages right-aligned (`#0f62fe` bg), AI responses left-aligned (`#262626` bg)

---

## Security Considerations

| Concern | Mitigation |
|---|---|
| CSRF on OAuth callback | `state` parameter validated on every callback |
| JWT tampering | `id_token` signature verified via JWKS using `PyJWT` + `cryptography` |
| Client secret exposure | Stored server-side in `.env` only; never sent to browser |
| Token logging | Access tokens and user PII are never logged to stdout |
| Agent API key | Stored in `.env`; rotated regularly in production via a secrets manager |
| Production deployment | Use a reverse proxy (nginx/Caddy) to set `Secure` + `HttpOnly` cookie flags |

---

## Dependencies

```
streamlit>=1.35.0
requests>=2.31.0
python-dotenv>=1.0.0
PyJWT>=2.8.0
cryptography>=42.0.0
```

Install with:
```bash
pip install -r requirements.txt
```

---

## License

This project is a proof-of-concept demo. Refer to the repository root for licensing details.
