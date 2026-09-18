"""Compatibility route that opens the landing workspace with chat enabled."""

import streamlit as st

st.set_page_config(
    page_title="IBM Verify — Chat",
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
LOGGER = get_logger("pages.chat")
log_loaded_configuration()

from auth.session import require_auth

require_auth()
LOGGER.debug("Opening compatibility chat route and redirecting to landing page")
st.session_state["chat_open"] = True
st.switch_page("pages/landing.py")
st.stop()
