"""Premium split workspace for landing and chat interactions."""
import re

import streamlit as st

from auth.session import get_access_token, get_preferred_username, get_user_info
from components.html_embed import render_html_fragment
from components.navbar import render_access_token_expander, render_token_contents
from observability import get_logger, rotate_request_id
from services.agent_api import get_agent_tokens, stream_agent_response
from services.response_normalization import normalize_message_content

LOGGER = get_logger("components.chat_workspace")
_CHAT_VIEWPORT_HEIGHT = 560
_EMOJI_SHORTCODE_RE = re.compile(r"(?<!\\):([a-zA-Z0-9_+\-]+):")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_MARKDOWN_BLOCK_RE = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+\.\s|>\s|```|~~~|\|)")


def _escape_emoji_shortcodes(text: str) -> str:
    """Prevent Markdown emoji shortcodes from mutating colon-delimited content."""
    return _EMOJI_SHORTCODE_RE.sub(r"\\:\1\\:", text)


def _is_list_item(line: str) -> bool:
    """Return whether the line is a Markdown list item."""
    return bool(_LIST_ITEM_RE.match(line))


def _collapse_markdown_blank_lines(text: str) -> str:
    """Trim excess empty lines while preserving code fences and list structure."""
    lines = text.replace("\r\n", "\n").split("\n")
    collapsed: list[str] = []
    in_code_fence = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_fence = not in_code_fence
            collapsed.append(line.rstrip())
            continue

        if in_code_fence:
            collapsed.append(line.rstrip())
            continue

        if stripped:
            collapsed.append(line.rstrip())
            continue

        previous_non_empty = next((item for item in reversed(collapsed) if item.strip()), "")
        next_non_empty = next(
            (candidate for candidate in lines[idx + 1 :] if candidate.strip()),
            "",
        )
        if previous_non_empty and next_non_empty:
            if _is_list_item(previous_non_empty) and _is_list_item(next_non_empty):
                continue

        if collapsed and collapsed[-1] == "":
            continue

        collapsed.append("")

    return "\n".join(collapsed).strip()


def _looks_like_markdown_block(lines: list[str]) -> bool:
    """Return whether the block already uses Markdown structure."""
    return any(_MARKDOWN_BLOCK_RE.match(line) for line in lines)


def _looks_like_structured_text(lines: list[str]) -> bool:
    """Return whether the block should be preserved as a literal text block."""
    if len(lines) < 2:
        return False

    delimiter_count = sum(
        1
        for line in lines
        if line.count(":") >= 2 or line.count("|") >= 2 or "\t" in line
    )
    return delimiter_count >= max(2, len(lines) // 2)


def _format_message_markdown(content: str) -> str:
    """Normalize assistant text into Markdown with preserved readable newlines."""
    normalized_content = _collapse_markdown_blank_lines(normalize_message_content(content))
    if not normalized_content:
        return ""

    blocks: list[str] = []
    for raw_block in normalized_content.split("\n\n"):
        block = raw_block.strip("\n")
        if not block:
            continue

        lines = [line for line in block.split("\n") if line.strip()]
        escaped_block = _escape_emoji_shortcodes(block)

        if block.startswith("```") and block.endswith("```"):
            blocks.append(block)
        elif _looks_like_markdown_block(lines):
            blocks.append(escaped_block)
        elif _looks_like_structured_text(lines):
            blocks.append(f"```text\n{block}\n```")
        elif len(lines) > 1:
            blocks.append("  \n".join(_escape_emoji_shortcodes(line) for line in block.split("\n")))
        else:
            blocks.append(escaped_block)

    return "\n\n".join(blocks)


def _render_message_content(role: str, content: str) -> None:
    """Render chat content with Markdown formatting and preserved plaintext newlines."""
    if role == "assistant":
        st.markdown(_format_message_markdown(content))
        return

    st.markdown(normalize_message_content(content))


def _render_thinking_indicator(placeholder) -> None:
    """Render an animated thinking state while the assistant response is pending."""
    placeholder.markdown(
        """
        <div class="premium-thinking" role="status" aria-live="polite" aria-label="Assistant is thinking">
            <span class="premium-thinking__spinner" aria-hidden="true">
                <span>⠋</span>
                <span>⠙</span>
                <span>⠹</span>
                <span>⠸</span>
                <span>⠼</span>
                <span>⠴</span>
                <span>⠦</span>
                <span>⠧</span>
                <span>⠇</span>
                <span>⠏</span>
            </span>
            <span class="premium-thinking__label">Thinking</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _mount_enter_to_send_handler() -> None:
    """Bind Enter-to-send on the chat textarea while preserving Shift+Enter for newlines."""
    render_html_fragment(
        """
        <div aria-hidden="true" style="height:0;overflow:hidden"></div>
        <script>
        (() => {
          const bindEnterToSend = () => {
          const doc = window.document;
          const textarea = [...doc.querySelectorAll("textarea")].find(
            (node) => node.getAttribute("placeholder") === "Message your AI agent…"
          );

          if (!textarea || textarea.dataset.enterBound === "true") {
            return;
          }

          textarea.dataset.enterBound = "true";
          textarea.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" || event.shiftKey) {
              return;
            }

            event.preventDefault();
            const sendButton = [...doc.querySelectorAll("button")].find(
              (button) => button.textContent && button.textContent.trim() === "Send"
            );
            if (sendButton) {
              sendButton.click();
            }
          });
          };

          bindEnterToSend();
          if (!window.__premiumEnterToSendObserver) {
            window.__premiumEnterToSendObserver = new MutationObserver(bindEnterToSend);
            window.__premiumEnterToSendObserver.observe(document.body, {
              childList: true,
              subtree: true
            });
          }
        })();
        </script>
        """,
        height=0,
    )


def _ensure_workspace_state() -> None:
    """Initialize session state used by the landing/chat workspace."""
    st.session_state.setdefault("chat_open", False)
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("agent_tokens", {})
    st.session_state.setdefault("agent_tokens_error", "")
    st.session_state.setdefault("agent_tokens_loaded", False)


def _start_chat() -> None:
    """Open the chat panel."""
    st.session_state["chat_open"] = True


def _refresh_agent_tokens() -> None:
    """Fetch and persist the latest agent tokens for the current session."""
    try:
        agent_tokens = get_agent_tokens(get_access_token())
        st.session_state["agent_tokens"] = agent_tokens
        st.session_state["agent_tokens_error"] = ""
        st.session_state["agent_tokens_loaded"] = True
    except RuntimeError as exc:
        LOGGER.error("Agent token retrieval failed: %s", exc)
        st.session_state["agent_tokens"] = {}
        st.session_state["agent_tokens_error"] = str(exc)
        st.session_state["agent_tokens_loaded"] = False


def _clear_chat() -> None:
    """Reset conversation history while keeping the chat panel open."""
    st.session_state["messages"] = []
    st.session_state.pop("obo_token", None)
    st.session_state["agent_tokens"] = {}
    st.session_state["agent_tokens_error"] = ""
    st.session_state["agent_tokens_loaded"] = False
    st.session_state["chat_open"] = True
    new_request_id = rotate_request_id(st.session_state)
    LOGGER.info(
        "Started new chat conversation with request_id=%s preferred_username=%s",
        new_request_id,
        get_preferred_username(),
    )


def _render_left_panel() -> None:
    """Render the editorial left panel with controls and context."""
    user_info = get_user_info()
    first_name = user_info.get("name", "").split()[0] if user_info.get("name") else "there"
    message_count = len(st.session_state.get("messages", []))

    st.markdown(
        """
        <h1 class="premium-hero-title">
            Secure conversations for your AI runtime.
        </h1>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <p class="premium-hero-copy">
            Welcome, {first_name}. Launch a governed conversation, inspect the delegated identity,
            and keep the agent workspace anchored in one focused control surface.
        </p>
        """,
        unsafe_allow_html=True,
    )

    start_col, _ = st.columns([1.25, 1.05], gap="small")
    with start_col:
        st.button(
            "Start Chatting →",
            use_container_width=True,
            key="workspace_start_chat",
            on_click=_start_chat,
        )

    st.markdown(
        f"""
        <div class="premium-stat-grid">
            <div class="premium-stat-card">
                <span class="premium-stat-label">Conversation</span>
                <strong>{message_count} messages</strong>
                <span class="premium-stat-copy">History stays in-session for a continuous operator flow.</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_empty_state() -> None:
    """Render the pre-chat hero for the right panel."""
    st.markdown(
        """
        <div class="premium-chat-stage">
            <div class="premium-chat-stage__badge">Awaiting conversation</div>
            <h2>Open the chat workspace from the left rail.</h2>
            <p>
                The right panel becomes a live assistant canvas once you start chatting.
                You can keep the operator context on the left while the live chat and token
                inspection panels stay visible on the right.
            </p>
            <div class="premium-orbit premium-orbit--one"></div>
            <div class="premium-orbit premium-orbit--two"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _stream_assistant_response(user_input: str) -> None:
    """Stream a message to the agent and render the reply progressively."""
    history = st.session_state["messages"][:]
    st.session_state["messages"].append({"role": "user", "content": user_input})
    st.session_state.pop("obo_token", None)
    LOGGER.debug("Submitting chat message with %s prior messages", len(history))

    with st.chat_message("user", avatar="🧑"):
        _render_message_content("user", user_input)

    with st.chat_message("assistant", avatar="🔷"):
        response_placeholder = st.empty()
        response_chunks: list[str] = []
        _render_thinking_indicator(response_placeholder)

        try:
            for chunk in stream_agent_response(
                message=user_input,
                history=history,
                access_token=get_access_token(),
            ):
                response_chunks.append(chunk)
                response_placeholder.markdown(
                    _format_message_markdown("".join(response_chunks))
                )

            response_text = normalize_message_content("".join(response_chunks)).strip()
            st.session_state["messages"].append(
                {"role": "assistant", "content": response_text}
            )
            if history or user_input.strip():
                _refresh_agent_tokens()
            LOGGER.debug("Received streamed assistant response from agent API")
        except RuntimeError as exc:
            LOGGER.error("Chat submission failed: %s", exc)
            partial_response = normalize_message_content("".join(response_chunks)).strip()
            if partial_response:
                LOGGER.debug(
                    "Preserving partial streamed assistant response after late error"
                )
                response_placeholder.markdown(_format_message_markdown(partial_response))
                st.session_state["messages"].append(
                    {"role": "assistant", "content": partial_response}
                )
            else:
                error_msg = f"⚠️ {exc}"
                response_placeholder.markdown(_format_message_markdown(error_msg))
                st.session_state["messages"].append(
                    {"role": "assistant", "content": error_msg}
                )


def _render_active_chat() -> None:
    """Render the live chat panel in the right column."""
    st.markdown('<div class="premium-chat-shell">', unsafe_allow_html=True)
    with st.container(height=_CHAT_VIEWPORT_HEIGHT, border=False):
        if not st.session_state["messages"]:
            st.markdown(
                """
                <div class="premium-chat-hint">
                    Your helpful AI Assistant
                </div>
                """,
                unsafe_allow_html=True,
            )

        for msg in st.session_state["messages"]:
            role = msg["role"]
            avatar = "🧑" if role == "user" else "🔷"
            with st.chat_message(role, avatar=avatar):
                _render_message_content(role, msg["content"])

        pending_user_input = st.session_state.pop("pending_user_input", "")
        if pending_user_input:
            _stream_assistant_response(pending_user_input)

    with st.form("premium_chat_form", clear_on_submit=True):
        user_input = st.text_area(
            "Message your AI agent",
            key="premium_chat_draft",
            placeholder="Message your AI agent…",
            label_visibility="collapsed",
            height=88,
        )
        clear_col, send_col = st.columns([1, 1.6], gap="small")
        with clear_col:
            clear_clicked = st.form_submit_button(
                "Clear Conversation",
                use_container_width=True,
                type="secondary",
            )
        with send_col:
            send_clicked = st.form_submit_button("Send", use_container_width=True)

    if clear_clicked:
        _clear_chat()
        st.rerun()

    if send_clicked:
        if user_input.strip():
            st.session_state["pending_user_input"] = user_input.strip()
            st.rerun()
        else:
            st.warning("Enter a message before sending.")

    _mount_enter_to_send_handler()
    st.markdown("</div>", unsafe_allow_html=True)

def _render_token_panel() -> None:
    """Render the right-side token inspection panel."""
    agent_tokens = st.session_state.get("agent_tokens", {})
    agent_tokens_error = st.session_state.get("agent_tokens_error", "")
    agent_tokens_loaded = st.session_state.get("agent_tokens_loaded", False)
    has_user_message = any(msg.get("role") == "user" for msg in st.session_state.get("messages", []))

    st.markdown(
        """
        <div class="premium-token-panel">
            <div class="premium-token-panel__eyebrow">Identity inspector</div>
            <h3>Token context</h3>
            <p>Inspect the subject token and the delegated agent tokens without leaving the workspace.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_access_token_expander(expanded=False)

    if agent_tokens_error:
        st.error(agent_tokens_error)

    if not has_user_message:
        st.info("Agent tokens will appear after you send your first message to the AI agent.")
        return

    if not agent_tokens_loaded and not agent_tokens:
        st.info("Agent tokens are not available yet.")
        return

    with st.expander("Agent - Actor Token", expanded=False):
        render_token_contents(
            "Actor Token",
            agent_tokens.get("actor_token", ""),
            empty_message="No actor token was returned by the agent tokens endpoint.",
            show_title=False,
        )

    with st.expander("Agent - OBO Token", expanded=False):
        render_token_contents(
            "OBO Token",
            agent_tokens.get("obo_token", ""),
            empty_message="No OBO token was returned by the agent tokens endpoint.",
            show_title=False,
        )


def render_chat_workspace(open_chat: bool = False) -> None:
    """Render the premium split layout for the landing page."""
    _ensure_workspace_state()

    if open_chat:
        st.session_state["chat_open"] = True

    left_col, right_col = st.columns([0.72, 1.63], gap="large")

    with left_col:
        _render_left_panel()

    with right_col:
        chat_col, token_col = st.columns([1.55, 0.85], gap="medium")

        with chat_col:
            if st.session_state["chat_open"]:
                _render_active_chat()
            else:
                _render_empty_state()

        with token_col:
            _render_token_panel()
