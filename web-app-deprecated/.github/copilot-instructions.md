# Copilot Instructions

## Running the App

```bash
cd web
cp .env.example .env   # fill in values first
pip install -r requirements.txt
streamlit run app.py --server.port 8501
```

## Project Structure

```
web/
├── app.py                  # Entrypoint: theme injection, OAuth callback, routing
├── pages/
│   └── chat.py             # AI Agent chat page (auth-gated)
├── auth/
│   ├── oauth.py            # IBM Verify OAuth 2.0 Authorization Code Flow
│   └── session.py          # require_auth() page guard
├── components/
│   ├── login_page.py       # Login hero UI
│   ├── chat_ui.py          # Chat interface
│   └── navbar.py           # Top nav bar
├── services/
│   └── agent_api.py        # Remote AI agent HTTP client
└── styles/
    └── theme.css           # Global CSS injected via st.markdown
```

## Architecture

- **`app.py`** is the only unauthenticated route (`/`). It checks `st.query_params` for `?code=` (OAuth callback), exchanges it for tokens, stores them in `st.session_state`, then redirects to `pages/chat.py`. If already authenticated it skips straight to chat; otherwise it renders the login page.
- **`auth/session.py`** exports `require_auth()` — every page except `app.py` must call this as its first statement, followed by `inject_theme()`.
- **OAuth flow** lives entirely in `auth/oauth.py`. IBM Verify OIDC endpoints are at `{IBM_VERIFY_TENANT_URL}/oidc/endpoint/default/{authorize|token|userinfo|jwks}`.
- **Agent calls** go through `services/agent_api.py → invoke_agent(message, history, access_token)`. History is stored as `st.session_state.messages` (`[{"role": "user"|"assistant", "content": "..."}]`).
- **Theme** is injected by calling `inject_theme()` which reads `styles/theme.css` and injects it via `st.markdown(..., unsafe_allow_html=True)`.

## Key Conventions

- All Python files use **snake_case**; UI component functions are prefixed `render_`.
- `st.session_state` keys: `access_token`, `id_token`, `user_info`, `messages`.
- Use `st.rerun()` — never `st.experimental_rerun`.
- Validate the OAuth `state` parameter on callback (CSRF protection).
- Validate the `id_token` JWT signature via the JWKS endpoint using `PyJWT` + `cryptography`.
- Never log access tokens or user PII.
- CSS targets Streamlit's `data-testid` attributes; verify selectors in the DOM before adding overrides.

## Environment Variables

| Variable | Purpose |
|---|---|
| `IBM_VERIFY_CLIENT_ID` | OAuth client ID |
| `IBM_VERIFY_CLIENT_SECRET` | OAuth client secret (server-side only) |
| `IBM_VERIFY_TENANT_URL` | e.g. `https://your-tenant.verify.ibm.com` |
| `IBM_VERIFY_REDIRECT_URI` | e.g. `http://localhost:8501/` |
| `AI_AGENT_API_URL` | Remote agent endpoint |
| `AI_AGENT_API_KEY` | Agent API key (Bearer token) |

## Design System

Uses IBM Carbon Design System conventions:
- **Colors**: `#0f62fe` (primary blue), `#161616` (page bg), `#262626` (card bg), `#393939` (borders)
- **Typography**: IBM Plex Sans (body/UI), IBM Plex Mono (code)
- **Layout**: 8px base grid, sharp corners (`border-radius: 0`), no drop shadows — use `1px solid #393939` borders
- **Buttons**: Flat, primary = `#0f62fe` bg / white text, hover = `#0043ce`

## Dependencies

```
streamlit>=1.35.0
requests>=2.31.0
python-dotenv>=1.0.0
PyJWT>=2.8.0
cryptography>=42.0.0
```
