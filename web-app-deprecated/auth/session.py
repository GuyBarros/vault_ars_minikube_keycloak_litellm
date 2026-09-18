"""Session management: auth guard and theme injection."""

import os
import jwt
import streamlit as st

from observability import get_logger

LOGGER = get_logger("auth.session")


_LIGHT = """
:root {
  --ibm-blue:       #2358ff;
  --ibm-blue-hover: #3d70ff;
  --bg-primary:     #f5f7ff;
  --bg-secondary:   rgba(255, 255, 255, 0.8);
  --bg-tertiary:    rgba(232, 239, 255, 0.92);
  --surface-strong: rgba(255, 255, 255, 0.95);
  --surface-soft:   rgba(35, 88, 255, 0.06);
  --surface-glow:   rgba(35, 88, 255, 0.14);
  --panel-bg:       linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(242, 247, 255, 0.94));
  --input-bg:       rgba(255, 255, 255, 0.98);
  --chat-input-shell: rgba(255, 255, 255, 0.96);
  --chat-input-shell-border: rgba(35, 88, 255, 0.14);
  --chat-input-focus: rgba(35, 88, 255, 0.12);
  --chat-input-text: #0c1630;
  --chat-input-placeholder: #667799;
  --control-bg:     rgba(235, 241, 255, 0.82);
  --accent-pill-bg: rgba(35, 88, 255, 0.08);
  --accent-pill-border: rgba(35, 88, 255, 0.14);
  --accent-pill-text: #2358ff;
  --button-text:    #ffffff;
  --text-primary:   #0c1630;
  --text-secondary: #465374;
  --text-muted:     #667799;
  --border:         rgba(35, 88, 255, 0.14);
  --success:        #2f9d6a;
  --error:          #cc4856;
  --shadow-lg:      0 24px 80px rgba(32, 56, 105, 0.16);
}
.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
[data-testid="stBottomBlockContainer"] {
  background:
    radial-gradient(circle at top left, rgba(79, 122, 255, 0.14), transparent 34%),
    radial-gradient(circle at 85% 18%, rgba(120, 140, 255, 0.12), transparent 28%),
    linear-gradient(180deg, #f8fbff 0%, #eef4ff 100%) !important;
  color: #0c1630 !important;
}
[data-testid="stChatMessage"] {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.94), rgba(245, 248, 255, 0.92)) !important;
}
"""


def inject_theme() -> None:
    """Inject IBM Carbon theme CSS. Reads theme preference from session state."""
    css_path = os.path.join(os.path.dirname(__file__), "..", "styles", "theme.css")
    with open(css_path, encoding="utf-8") as f:
        base_css = f.read()
    override = _LIGHT if st.session_state.get("theme", "dark") == "light" else ""
    st.markdown(f"<style>{base_css}{override}</style>", unsafe_allow_html=True)


def require_auth() -> None:
    """
    Guard: redirect unauthenticated users to the login page.

    Call this as the very first statement in every authenticated page.
    """
    if "access_token" not in st.session_state:
        LOGGER.debug("Unauthenticated user redirected to login page")
        st.switch_page("app.py")
        st.stop()


def get_access_token() -> str:
    """Return the current session's access token."""
    return st.session_state.get("access_token", "")


def get_user_info() -> dict:
    """Return the current session's user info dict."""
    return st.session_state.get("user_info", {})


def get_preferred_username() -> str:
    """Return the preferred_username claim from the session's access token."""
    access_token = get_access_token()
    if not access_token:
        return ""
    try:
        claims = jwt.decode(access_token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return ""
    return claims.get("preferred_username", "") or ""


def logout() -> None:
    """
    Clear session state and terminate the IBM Verify SSO session.

    Redirects the browser to IBM Verify's end-session endpoint with the
    id_token_hint so the SSO cookie is revoked. IBM Verify then redirects
    back to REDIRECT_URI (the app login page).
    """
    from auth.oauth import get_logout_url  # local import to avoid circular deps

    id_token = st.session_state.get("id_token", "")
    logout_url = get_logout_url(id_token)
    LOGGER.info("Logging out current session")

    # Clear all local session state before redirecting
    for key in list(st.session_state.keys()):
        del st.session_state[key]

    # Redirect browser to IBM Verify logout endpoint
    st.markdown(
        f'<meta http-equiv="refresh" content="0; url={logout_url}">',
        unsafe_allow_html=True,
    )
    st.stop()
