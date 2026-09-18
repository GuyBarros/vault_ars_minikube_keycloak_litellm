"""Login page component."""

import streamlit as st

from auth.oauth import generate_state, get_authorization_url


def render_login_page() -> None:
    """Render the full-screen login hero page."""
    # Use columns to center the card
    _, card, _ = st.columns([1, 2, 1])

    with card:
        st.markdown("<div style='padding-top: 80px;'></div>", unsafe_allow_html=True)

        # IBM wordmark
        st.markdown(
            "<p style='font-family:\"IBM Plex Sans\",sans-serif; font-size:13px; font-weight:600;"
            " letter-spacing:2px; text-transform:uppercase; color:#c6c6c6; text-align:center;"
            " margin-bottom:8px;'>IBM</p>",
            unsafe_allow_html=True,
        )

        # Headline
        st.markdown(
            "<h1 style='font-family:\"IBM Plex Sans\",sans-serif; font-size:52px; font-weight:300;"
            " color:#ffffff; text-align:center; line-height:1.15; margin:0 0 16px 0;'>AI Runtime Security</h1>",
            unsafe_allow_html=True,
        )

        # Subtitle
        st.markdown(
            "<p style='font-family:\"IBM Plex Sans\",sans-serif; font-size:16px; color:#c6c6c6;"
            " text-align:center; line-height:1.6; margin:0 0 40px 0;'>"
            "Your AI. Your data.<br>Sign in to start working with your AI agent.</p>",
            unsafe_allow_html=True,
        )

        # Login button
        if st.button("Login with IBM Verify", use_container_width=True, key="login_btn"):
            state = generate_state()
            st.session_state["oauth_state"] = state
            auth_url = get_authorization_url(state)
            st.markdown(
                f'<meta http-equiv="refresh" content="0; url={auth_url}">',
                unsafe_allow_html=True,
            )
            st.stop()

        # Footer note
        st.markdown(
            "<p style='font-family:\"IBM Plex Sans\",sans-serif; font-size:12px; color:#8d8d8d;"
            " text-align:center; margin-top:20px;'>Secured with IBM Verify, IBM Watsonx Governance, HashiCorp Consul &amp; Vault</p>",
            unsafe_allow_html=True,
        )
