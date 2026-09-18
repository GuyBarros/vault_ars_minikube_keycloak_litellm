"""Shared authenticated navbar component."""

import base64
import json
import uuid

import streamlit as st

from auth.session import get_access_token, get_user_info, logout
from components.html_embed import render_html_fragment


def _copy_to_clipboard_button(text: str) -> None:
    """Render an IBM Carbon-styled copy-to-clipboard button."""
    button_id = f"copy-btn-{uuid.uuid4().hex}"
    render_html_fragment(
        f"""
        <button id="{button_id}"
            type="button"
            style="
                background-color: transparent;
                color: #0f62fe;
                border: 1px solid #0f62fe;
                border-radius: 0;
                padding: 6px 14px;
                font-family: 'IBM Plex Sans', sans-serif;
                font-size: 12px;
                font-weight: 600;
                cursor: pointer;
            "
        >📋 Copy to clipboard</button>
        <script>
            (() => {{
                const token = {json.dumps(text)};
                const button = document.getElementById({json.dumps(button_id)});
                if (!button || button.dataset.clipboardBound === "true") {{
                    return;
                }}

                button.dataset.clipboardBound = "true";

                const done = () => {{
                    button.innerText = '✅ Copied!';
                    window.setTimeout(() => {{
                        button.innerText = '📋 Copy to clipboard';
                    }}, 2000);
                }};

                const fallback = (value) => {{
                    const textarea = document.createElement('textarea');
                    textarea.value = value;
                    textarea.style.position = 'fixed';
                    textarea.style.opacity = '0';
                    document.body.appendChild(textarea);
                    textarea.focus();
                    textarea.select();
                    document.execCommand('copy');
                    document.body.removeChild(textarea);
                    done();
                }};

                button.addEventListener('click', () => {{
                    if (navigator.clipboard) {{
                        navigator.clipboard.writeText(token)
                            .then(done)
                            .catch(() => fallback(token));
                    }} else {{
                        fallback(token);
                    }}
                }});
            }})();
        </script>
        """,
        height=44,
        width="content",
    )


def _decode_token_value(token: str) -> str:
    """Decode a JWT payload or base64/base64url token into a display-friendly string."""
    if not token:
        return ""

    try:
        if token.count(".") >= 2:
            payload_b64 = token.split(".")[1]
            padding = "=" * ((4 - len(payload_b64) % 4) % 4)
            decoded_payload = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
        else:
            padding = "=" * ((4 - len(token) % 4) % 4)
            decoded_payload = base64.urlsafe_b64decode(token + padding).decode("utf-8")

        parsed = json.loads(decoded_payload)
        return json.dumps(parsed, indent=2)
    except Exception:
        return "Decoded token payload is unavailable."


def render_token_contents(
    title: str,
    token: str,
    *,
    empty_message: str,
    show_title: bool = True,
) -> None:
    """Render the common encoded and decoded token views."""
    if show_title:
        st.markdown(f"**{title}**")

    if not token:
        st.info(empty_message)
        return

    st.markdown("**Base64 Encoded Token**")
    _copy_to_clipboard_button(token)
    st.code(token, language="text")
    st.markdown("**Base64 Decoded JSON**")
    st.code(_decode_token_value(token), language="json")


def render_access_token_expander(*, expanded: bool = False) -> None:
    """Render the access token in an expander for the token side panel."""
    access_token = get_access_token()
    with st.expander("Subject Token", expanded=expanded):
        render_token_contents(
            "OAuth 2.0 JWT Access Token",
            access_token,
            empty_message="No subject token is available for this session.",
        )


def render_navbar() -> None:
    """
    Render the top navigation bar on all authenticated pages.

    Layout: [App title + user info] | [Theme toggle] | [Logout]
    """
    user_info = get_user_info()
    display_name = user_info.get("name", "User")
    initials = user_info.get("initials", "?")
    theme = st.session_state.get("theme", "dark")

    # Theme-aware colors for inline HTML
    text_color = "#161616" if theme == "light" else "#ffffff"
    muted_color = "#525252" if theme == "light" else "#c6c6c6"
    border_color = "#e0e0e0" if theme == "light" else "#393939"

    st.markdown(
        f"<h1 style='font-family:\"IBM Plex Sans\",sans-serif;font-size:28px;"
        f"font-weight:600;color:{text_color};letter-spacing:-0.5px;"
        f"margin:0 0 20px 0;'>AI Agent Runtime Protection Demo</h1>",
        unsafe_allow_html=True,
    )

    title_col, theme_col, logout_col = st.columns([6, 1.2, 1])

    with title_col:
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:16px;padding:4px 0;'>"
            f"<span style='font-family:\"IBM Plex Sans\",sans-serif;font-size:20px;"
            f"font-weight:300;color:{text_color};letter-spacing:-0.5px;'>"
            f"<span style='color:#0f62fe;font-weight:600;'>IBM</span> Verify</span>"
            f"<span style='font-family:\"IBM Plex Sans\",sans-serif;font-size:13px;"
            f"color:{muted_color};border-left:1px solid {border_color};padding-left:16px;'>"
            f"<span style='display:inline-flex;align-items:center;justify-content:center;"
            f"width:24px;height:24px;background-color:#0f62fe;color:#ffffff;font-size:11px;"
            f"font-weight:600;border-radius:50%;margin-right:8px;vertical-align:middle;'>"
            f"{initials}</span>{display_name}</span></div>",
            unsafe_allow_html=True,
        )

    with theme_col:
        dark_label = "🌙 Dark"
        light_label = "☀️ Light"
        options = [dark_label, light_label]
        selected = st.selectbox(
            "Theme",
            options,
            index=0 if theme == "dark" else 1,
            label_visibility="collapsed",
            key="theme_select",
        )
        new_theme = "dark" if selected == dark_label else "light"
        if new_theme != theme:
            st.session_state["theme"] = new_theme
            st.rerun()

    with logout_col:
        if st.button("Logout", use_container_width=True, key="navbar_logout", type="secondary"):
            logout()

    st.markdown(
        f'<hr style="border-color:{border_color}; margin: 0 0 32px 0;">',
        unsafe_allow_html=True,
    )
