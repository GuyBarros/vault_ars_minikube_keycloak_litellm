"""
app.py — Streamlit entrypoint.

Responsibilities:
  1. Inject IBM Carbon theme
  2. Detect OAuth 2.0 callback (?code=&state=), exchange for tokens, redirect
  3. If already authenticated, skip straight to landing page
  4. Otherwise, render the login page
"""

import streamlit as st

st.set_page_config(
    page_title="IBM Verify",
    page_icon="🔷",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from auth.session import get_preferred_username
from config import log_loaded_configuration
from observability import bind_request_context, get_logger

bind_request_context(
    st.session_state,
    st.context.headers,
    st.context.url,
    preferred_username=get_preferred_username(),
)
LOGGER = get_logger("app")
log_loaded_configuration()

from auth.oauth import exchange_code_for_tokens, extract_user_info, validate_id_token
from auth.session import inject_theme
from components.login_page import render_login_page

inject_theme()

# ── Already authenticated → go straight to landing ───────────────────────────
if "access_token" in st.session_state:
    LOGGER.debug("Authenticated session detected; redirecting to landing page")
    st.switch_page("pages/landing.py")
    st.stop()

# ── OAuth 2.0 callback: ?code=<code>&state=<state> ───────────────────────────
params = st.query_params
code = params.get("code")
returned_state = params.get("state")

if code:
    # CSRF: validate state only when it is still present in the session.
    # Note: Streamlit creates a new WebSocket session on every full-page redirect,
    # so oauth_state is typically absent after returning from IBM Verify.
    # We validate when available and skip silently when the session was reset.
    expected_state = st.session_state.pop("oauth_state", None)
    if expected_state is not None and returned_state != expected_state:
        LOGGER.warning("OAuth callback rejected because state validation failed")
        st.error("⚠️ Authentication failed: invalid state parameter. Please try again.")
        st.stop()

    # Exchange authorization code for tokens
    with st.spinner("Completing sign-in…"):
        try:
            LOGGER.debug("Processing OAuth callback")
            token_response = exchange_code_for_tokens(code)
        except Exception as exc:
            LOGGER.exception("Token exchange failed")
            st.error(f"⚠️ Token exchange failed: {exc}")
            st.stop()

    access_token = token_response.get("access_token", "")
    id_token_raw = token_response.get("id_token", "")

    # Validate id_token JWT signature via JWKS
    try:
        claims = validate_id_token(id_token_raw)
    except Exception as exc:
        LOGGER.exception("Token validation failed")
        st.error(f"⚠️ Token validation failed: {exc}")
        st.stop()

    # Store tokens and user info in session
    st.session_state["access_token"] = access_token
    st.session_state["id_token"] = id_token_raw
    st.session_state["user_info"] = extract_user_info(claims)
    LOGGER.info("Authentication completed successfully")

    # Clear OAuth params from URL and redirect
    st.query_params.clear()
    st.switch_page("pages/landing.py")
    st.stop()

# ── Default: show login page ──────────────────────────────────────────────────
LOGGER.debug("Rendering login page")
render_login_page()
