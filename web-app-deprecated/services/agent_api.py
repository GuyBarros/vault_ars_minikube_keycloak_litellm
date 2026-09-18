"""Remote AI agent HTTP client."""

import json
from collections.abc import Iterator

import requests

from config import AI_AGENT_API_URL
from observability import build_outbound_headers, get_logger
from services.response_normalization import normalize_message_content

_LOGGER = get_logger("services.agent_api")
_AGENT_BASE_URL = AI_AGENT_API_URL
_AGENT_URL = f"{_AGENT_BASE_URL}/v1/agent/query"
_AGENT_TOKENS_URL = f"{_AGENT_BASE_URL}/v1/agent/tokens"


def _build_headers(access_token: str) -> dict[str, str]:
    """Build outbound headers for the agent request."""
    return build_outbound_headers(
        {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
    )


def _build_payload(message: str, history: list, stream: bool) -> dict:
    """Build the agent request payload."""
    return {
        "messages": [*history, {"role": "user", "content": message}],
        "stream": stream,
    }


def _extract_agent_response(response: requests.Response) -> dict:
    """Normalize JSON and plain-text agent responses into the UI contract."""
    try:
        data = response.json()
    except requests.JSONDecodeError:
        response_text = normalize_message_content(response.text).strip()
        if not response_text:
            raise RuntimeError("Agent API returned an empty response.")

        _LOGGER.debug(
            "Agent API returned non-JSON content-type=%s",
            response.headers.get("Content-Type", "<missing>"),
        )
        return {"response": response_text, "obo_token": ""}

    if isinstance(data, dict):
        response_text = (
            data.get("response")
            or data.get("response_text")
            or data.get("result")
            or data.get("message")
            or ""
        )
        if not response_text:
            response_text = str(data)
        return {
            "response": normalize_message_content(str(response_text)),
            "obo_token": data.get("obo_token", ""),
        }

    return {"response": normalize_message_content(str(data)), "obo_token": ""}


def _normalize_agent_tokens_payload(data: object) -> dict[str, str] | None:
    """Return normalized agent tokens from direct, wrapped, or stringified payloads."""
    if isinstance(data, str):
        stripped_data = data.strip()
        if not stripped_data:
            return None

        try:
            return _normalize_agent_tokens_payload(json.loads(stripped_data))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None

    actor_token = data.get("actor_token")
    obo_token = data.get("obo_token")
    if actor_token is not None or obo_token is not None:
        return {
            "actor_token": str(actor_token or ""),
            "obo_token": str(obo_token or ""),
        }

    for wrapper_key in ("data", "result", "response", "payload", "body"):
        nested_payload = data.get(wrapper_key)
        normalized = _normalize_agent_tokens_payload(nested_payload)
        if normalized is not None:
            return normalized

    return None


def _extract_agent_tokens(response: requests.Response) -> dict[str, str]:
    """Normalize the agent token response into actor/obo token fields."""
    try:
        data = response.json()
    except requests.JSONDecodeError:
        data = None

    normalized = _normalize_agent_tokens_payload(data)
    if normalized is not None:
        return normalized

    normalized_from_text = _normalize_agent_tokens_payload(response.text)
    if normalized_from_text is not None:
        return normalized_from_text

    raise RuntimeError(
        "Agent tokens API returned an unexpected response shape."
    )


def _extract_error_body(response: requests.Response) -> str:
    """Extract a readable error message from JSON or plain-text responses."""
    content_type = response.headers.get("Content-Type", "")

    if "application/json" in content_type.lower():
        try:
            data = response.json()
        except requests.JSONDecodeError:
            data = None

        if isinstance(data, dict):
            for key in ("error_description", "detail", "message", "error", "response"):
                value = data.get(key)
                if value:
                    return str(value).strip()

            return str(data).strip()

        if data is not None:
            return str(data).strip()

    return response.text.strip()


def _raise_agent_http_error(response: requests.Response, log_message: str) -> None:
    """Raise a RuntimeError that includes the response body when available."""
    error_body = _extract_error_body(response)
    _LOGGER.error(log_message, response.status_code)

    if error_body:
        raise RuntimeError(f"Agent API error {response.status_code}: {error_body}")

    raise RuntimeError(f"Agent API error {response.status_code}")


def _is_premature_stream_end(exc: requests.RequestException) -> bool:
    """Return whether the streaming transport closed after sending partial content."""
    return "Response ended prematurely" in str(exc)


def _split_stream_buffer(buffer: str) -> tuple[str, str]:
    """Return the decodable prefix and any trailing partial escape sequence."""
    trailing_backslashes = len(buffer) - len(buffer.rstrip("\\"))
    if trailing_backslashes % 2 == 0:
        return buffer, ""

    return buffer[:-1], buffer[-1]


def invoke_agent(message: str, history: list, access_token: str = "") -> dict:
    """
    Send a message to the remote AI agent and return the full response payload.

    Args:
        message:      The user's latest message.
        history:      Conversation history as list of {"role": ..., "content": ...} dicts.
        access_token: The user's IBM Verify access token (sent as Bearer token in Authorization header).

    Returns:
        A dict with keys:
          - "response"  (str): The agent's reply text.
          - "obo_token" (str): On-Behalf-Of JWT returned by the agent (may be empty string).

    Raises:
        RuntimeError: If the API call fails or is not configured.
    """
    if not _AGENT_BASE_URL:
        raise RuntimeError("AI_AGENT_API_URL is not configured.")

    headers = _build_headers(access_token)
    payload = _build_payload(message, history, stream=False)

    try:
        _LOGGER.debug("Invoking agent API at %s", _AGENT_URL)
        response = requests.post(_AGENT_URL, json=payload, headers=headers, timeout=60)
        if not response.ok:
            _raise_agent_http_error(response, "Agent API returned HTTP %s")
        _LOGGER.debug("Agent API completed with status %s", response.status_code)
        return _extract_agent_response(response)
    except requests.RequestException as exc:
        _LOGGER.error("Agent API request failed: %s", exc)
        raise RuntimeError(f"Agent API request failed: {exc}") from exc


def get_agent_tokens(access_token: str = "") -> dict[str, str]:
    """Fetch actor and OBO tokens for the authenticated user session."""
    if not _AGENT_BASE_URL:
        raise RuntimeError("AI_AGENT_API_URL is not configured.")

    headers = _build_headers(access_token)

    try:
        _LOGGER.debug("Fetching agent tokens from %s", _AGENT_TOKENS_URL)
        response = requests.get(_AGENT_TOKENS_URL, headers=headers, timeout=30)
        if not response.ok:
            _raise_agent_http_error(response, "Agent tokens API returned HTTP %s")
        _LOGGER.debug("Agent tokens API completed with status %s", response.status_code)
        return _extract_agent_tokens(response)
    except requests.RequestException as exc:
        _LOGGER.error("Agent tokens API request failed: %s", exc)
        raise RuntimeError(f"Agent tokens API request failed: {exc}") from exc


def stream_agent_response(
    message: str, history: list, access_token: str = ""
) -> Iterator[str]:
    """Stream the agent response as plain-text chunks with JSON fallback support."""
    if not _AGENT_BASE_URL:
        raise RuntimeError("AI_AGENT_API_URL is not configured.")

    headers = _build_headers(access_token)
    payload = _build_payload(message, history, stream=True)

    try:
        _LOGGER.debug("Invoking streaming agent API at %s", _AGENT_URL)
        with requests.post(
            _AGENT_URL,
            json=payload,
            headers=headers,
            timeout=(10, 300),
            stream=True,
        ) as response:
            if not response.ok:
                _raise_agent_http_error(response, "Streaming agent API returned HTTP %s")
            content_type = response.headers.get("Content-Type", "")

            if "application/json" in content_type.lower():
                yield _extract_agent_response(response)["response"]
                return

            streamed_chunks = 0
            pending_escape = ""
            try:
                for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                    if chunk:
                        streamed_chunks += 1
                        pending_escape += chunk
                        decodable_text, pending_escape = _split_stream_buffer(pending_escape)
                        if not decodable_text:
                            continue

                        yield normalize_message_content(decodable_text)

                if pending_escape:
                    yield normalize_message_content(pending_escape)
            except requests.RequestException as exc:
                if streamed_chunks and _is_premature_stream_end(exc):
                    _LOGGER.debug(
                        "Streaming agent API ended prematurely after %s chunks; preserving received content",
                        streamed_chunks,
                    )
                    if pending_escape:
                        yield normalize_message_content(pending_escape)
                    return
                raise
    except requests.RequestException as exc:
        _LOGGER.error("Streaming agent API request failed: %s", exc)
        raise RuntimeError(f"Agent API request failed: {exc}") from exc
