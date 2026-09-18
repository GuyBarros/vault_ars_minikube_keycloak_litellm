"""Compatibility helpers for rendering inline HTML/JavaScript snippets."""

import streamlit as st
import streamlit.components.v1 as components


def render_html_fragment(
    body: str,
    *,
    width: str | int = "stretch",
    height: int = 0,
) -> None:
    """Render inline HTML/JS with Streamlit's supported API when available."""
    if hasattr(st, "html"):
        st.html(body, width=width, unsafe_allow_javascript=True)
        return

    legacy_width = width if isinstance(width, int) else None
    components.html(body, height=height, width=legacy_width)
