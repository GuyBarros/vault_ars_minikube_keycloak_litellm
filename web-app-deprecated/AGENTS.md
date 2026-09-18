# AGENTS.md — watsonx-style Streamlit Frontend

## Project Overview

This is a proof-of-concept Streamlit web application styled after the [IBM watsonx](https://www.ibm.com/products/watsonx) product site. It provides:

1. **Login with IBM Verify** — OAuth 2.0 Authorization Code Flow for user authentication
2. **AI Agent Chat** — Conversational interface that invokes a remote AI agent via REST API

---

## Design System

Match the visual identity of `ibm.com/products/watsonx` using IBM's Carbon Design System conventions.

### Typography

| Role | Font | Weight |
|---|---|---|
| Body / UI | IBM Plex Sans | 400, 600 |
| Code / Monospace | IBM Plex Mono | 400 |
| Display headings | IBM Plex Sans | 300 (Light) |

Load fonts from Google Fonts or IBM CDN:
```html
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;600;700&family=IBM+Plex+Mono&display=swap" rel="stylesheet">
```

### Color Palette

| Token | Hex | Usage |
|---|---|---|
| `$blue-60` | `#0f62fe` | Primary CTA buttons, links, accents |
| `$blue-70` | `#0043ce` | Button hover state |
| `$gray-100` | `#161616` | Page background (dark theme) |
| `$gray-90` | `#262626` | Card / panel background |
| `$gray-80` | `#393939` | Borders, dividers |
| `$gray-10` | `#f4f4f4` | Subtle light background |
| `$white` | `#ffffff` | Primary text on dark background |
| `$gray-30` | `#c6c6c6` | Secondary / muted text |
| `$green-40` | `#42be65` | Success / online indicator |
| `$red-50` | `#fa4d56` | Error states |

### Layout & Spacing

- Use **8px base grid** (4, 8, 16, 24, 32, 48, 64px spacing scale)
- Max content width: `1584px` (Carbon max shell width)
- Page padding: `16px` mobile, `32px` desktop
- Cards: `border-radius: 0` (Carbon uses sharp corners)
- No drop shadows — use border `1px solid #393939` for separation

### Component Style

- **Buttons**: Flat, no border-radius. Primary = `#0f62fe` bg / white text. Ghost = transparent / `#0f62fe` text with border.
- **Inputs**: Dark bg (`#262626`), bottom border only (`1px solid #8d8d8d`), white text, blue focus ring
- **Chat bubbles**: User messages right-aligned with `#0f62fe` bg; AI responses left-aligned with `#262626` bg
- **Sidebar**: Dark (`#161616`) with navigation links in IBM Plex Sans 14px

---

## Application Architecture

```
web/
├── app.py                  # Streamlit entrypoint
├── pages/
│   └── chat.py             # AI Agent chat page
├── auth/
│   ├── __init__.py
│   ├── oauth.py            # IBM Verify OAuth 2.0 flow
│   └── session.py          # Session state management
├── components/
│   ├── login_page.py       # Login UI component
│   ├── chat_ui.py          # Chat interface component
│   └── navbar.py           # Top navigation bar
├── services/
│   └── agent_api.py        # Remote AI agent API client
├── styles/
│   └── theme.css           # Global CSS injected via st.markdown
├── .env.example            # Environment variable template
├── requirements.txt
└── AGENTS.md               # This file
```

---

## Implementation Guide

### 1. Environment Setup

```bash
pip install streamlit requests python-dotenv
```

**`.env.example`**:
```env
# IBM Verify OAuth 2.0
IBM_VERIFY_CLIENT_ID=your_client_id
IBM_VERIFY_CLIENT_SECRET=your_client_secret
IBM_VERIFY_TENANT_URL=https://your-tenant.verify.ibm.com
IBM_VERIFY_REDIRECT_URI=http://localhost:8501/

# AI Agent API
AI_AGENT_API_URL=https://your-agent-host/api/chat
AI_AGENT_API_KEY=your_api_key
```

### 2. OAuth 2.0 Authorization Code Flow (`auth/oauth.py`)

Implement the standard Authorization Code Flow against IBM Verify (IBM Security Verify):

```
Step 1: Redirect user → IBM Verify /authorize endpoint
        Params: response_type=code, client_id, redirect_uri, scope=openid profile email

Step 2: IBM Verify redirects back → app with ?code=<auth_code>

Step 3: App exchanges code → POST /token endpoint
        Body: grant_type=authorization_code, code, redirect_uri, client_id, client_secret

Step 4: Store access_token + id_token in st.session_state
        Decode id_token (JWT) to get user profile (name, email)

Step 5: On logout, clear session state and redirect to /authorize
```

Key IBM Verify endpoints (replace `{tenant}` with your tenant URL):
- **Authorization**: `{tenant}/oidc/endpoint/default/authorize`
- **Token**: `{tenant}/oidc/endpoint/default/token`
- **UserInfo**: `{tenant}/oidc/endpoint/default/userinfo`
- **JWKS**: `{tenant}/oidc/endpoint/default/jwks`

> Use `st.query_params` to detect the `code` callback parameter on page load.

**`auth/session.py`** must guard every page:
```python
def require_auth():
    if "access_token" not in st.session_state:
        st.switch_page("app.py")   # redirect to login
        st.stop()
```

### 3. AI Agent Chat (`services/agent_api.py`)

Send user messages to the remote AI agent via HTTP POST:

```python
def invoke_agent(message: str, conversation_history: list, access_token: str) -> str:
    headers = {
        "Authorization": f"Bearer {AI_AGENT_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "message": message,
        "history": conversation_history,   # list of {role, content} dicts
        "user_token": access_token,         # optional: pass IBM Verify token
    }
    response = requests.post(AI_AGENT_API_URL, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()["response"]
```

- Store `conversation_history` in `st.session_state.messages` as `[{"role": "user"|"assistant", "content": "..."}]`
- Use `st.chat_message` and `st.chat_input` (Streamlit native chat primitives)
- Stream responses with `st.write_stream` if the agent supports SSE/streaming

### 4. Theme Injection (`styles/theme.css`)

Inject CSS at the top of every page:

```python
def inject_theme():
    with open("styles/theme.css") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
```

Key CSS overrides for Streamlit:

```css
/* Hide default Streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }

/* Root colors */
:root {
  --ibm-blue: #0f62fe;
  --bg-primary: #161616;
  --bg-secondary: #262626;
  --text-primary: #ffffff;
  --text-secondary: #c6c6c6;
  --border: #393939;
}

/* App background */
.stApp { background-color: var(--bg-primary); font-family: 'IBM Plex Sans', sans-serif; }

/* Sidebar */
[data-testid="stSidebar"] { background-color: #161616; border-right: 1px solid var(--border); }

/* Buttons */
.stButton > button {
  background-color: var(--ibm-blue);
  color: white;
  border: none;
  border-radius: 0;
  font-family: 'IBM Plex Sans', sans-serif;
  font-weight: 600;
  padding: 12px 24px;
}
.stButton > button:hover { background-color: #0043ce; }

/* Chat messages */
[data-testid="stChatMessage"] { background-color: var(--bg-secondary); border: 1px solid var(--border); }

/* Text inputs */
.stTextInput > div > div > input, .stTextArea textarea {
  background-color: var(--bg-secondary);
  color: var(--text-primary);
  border: none;
  border-bottom: 1px solid #8d8d8d;
  border-radius: 0;
}
```

### 5. Login Page (`components/login_page.py`)

Layout the login page to mirror watsonx's hero section aesthetic:

- Full-screen dark background (`#161616`)
- Centered card with IBM logo SVG at top
- Headline: "IBM watsonx" in IBM Plex Sans Light 48px
- Subtitle: muted gray text
- Single CTA button: **"Login with IBM Verify"** (blue, full-width)
- Clicking the button redirects to IBM Verify `/authorize` URL

### 6. Chat Page (`pages/chat.py`)

- Call `require_auth()` at the top of the file
- Display user avatar (initials from id_token `name` claim) and **Logout** button in sidebar
- Render conversation using `st.chat_message("user")` / `st.chat_message("assistant")`
- The assistant avatar should use a watsonx-style icon (blue circle with "w")
- Handle API errors gracefully with `st.error()` styled messages
- Add a spinner (`st.spinner("Thinking...")`) while waiting for agent response

---

## Routing & Page Guards

Streamlit's multi-page app model:

| File | Route | Auth required |
|---|---|---|
| `app.py` | `/` | No (login page) |
| `pages/chat.py` | `/chat` | **Yes** |

`app.py` should:
1. Inject theme CSS
2. Check `st.query_params` for `?code=` (OAuth callback)
3. If code present → exchange for token → set `st.session_state` → `st.switch_page("pages/chat.py")`
4. If already authenticated → `st.switch_page("pages/chat.py")`
5. Otherwise → render login page UI

---

## Security Considerations

- **Never** store `client_secret` on the frontend; it must be in a server-side `.env` file
- Validate the `state` parameter in the OAuth callback to prevent CSRF attacks
- Validate the `id_token` JWT signature using IBM Verify JWKS endpoint
- Set `Secure` and `HttpOnly` cookie flags if deploying beyond localhost (use a reverse proxy)
- Do not log access tokens or user PII to stdout
- Rotate `AI_AGENT_API_KEY` regularly and store in a secrets manager in production

---

## Running Locally

```bash
# 1. Clone and enter the web directory
cd web

# 2. Copy and fill in environment variables
cp .env.example .env

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
streamlit run app.py --server.port 8501
```

The app will be available at `http://localhost:8501`.

Configure your IBM Verify application's **Redirect URI** to `http://localhost:8501/`.

---

## Dependencies (`requirements.txt`)

```
streamlit>=1.35.0
requests>=2.31.0
python-dotenv>=1.0.0
PyJWT>=2.8.0
cryptography>=42.0.0
```

---

## Key Conventions for Agents

- All Python files use **snake_case**; components are functions prefixed with `render_`
- Session state keys: `access_token`, `id_token`, `user_info`, `messages`
- Do **not** use `st.experimental_rerun`; use `st.rerun()` (Streamlit ≥ 1.27)
- CSS class targeting uses Streamlit's `data-testid` attributes — check DOM before adding new overrides
- Keep all OAuth logic in `auth/oauth.py`; keep all agent API calls in `services/agent_api.py`
- Each page file must call `inject_theme()` and `require_auth()` (where applicable) as its first two statements
