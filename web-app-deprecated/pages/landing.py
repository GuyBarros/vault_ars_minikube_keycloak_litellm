"""Authenticated landing page with a premium split chat workspace."""

import streamlit as st

st.set_page_config(
    page_title="IBM Verify — Home",
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
LOGGER = get_logger("pages.landing")
log_loaded_configuration()

from auth.session import inject_theme, require_auth
from components.chat_workspace import render_chat_workspace
from components.navbar import render_navbar

require_auth()
inject_theme()

LOGGER.debug("Rendering landing page")
render_navbar()

render_chat_workspace()
