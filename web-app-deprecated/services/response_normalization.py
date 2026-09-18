"""Helpers for normalizing agent responses before rendering."""

import json


def decode_escaped_plaintext(content: str) -> str:
    """Decode JSON-style escaped plain text returned inside text/plain streams."""
    escape_markers = ("\\r\\n", "\\n", "\\r", "\\t", '\\"', "\\/")
    if not any(marker in content for marker in escape_markers):
        return content

    stripped = content.strip()
    looks_wrapped = stripped.startswith('"') and (
        stripped.endswith('"') or "\\n" in stripped or '\\"' in stripped
    )
    looks_escaped_multiline = "\\n" in content and "\n" not in content
    looks_escaped_quotes = '\\"' in content and '"' not in content
    if not (looks_wrapped or looks_escaped_multiline or looks_escaped_quotes):
        return content

    prefix_length = len(content) - len(content.lstrip())
    suffix_length = len(content) - len(content.rstrip())
    prefix = content[:prefix_length]
    suffix = content[len(content) - suffix_length :] if suffix_length else ""
    body = stripped

    if body.startswith('"'):
        body = body[1:]
    if body.endswith('"') and not body.endswith('\\"'):
        body = body[:-1]

    body = (
        body.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\/", "/")
    )
    return f"{prefix}{body}{suffix}"


def normalize_message_content(content: str) -> str:
    """Extract a human-readable reply from JSON envelopes or escaped plain text."""
    stripped_content = content.strip()
    if not stripped_content.startswith("{"):
        return decode_escaped_plaintext(content)

    try:
        payload = json.loads(stripped_content)
    except json.JSONDecodeError:
        return decode_escaped_plaintext(content)

    if not isinstance(payload, dict):
        return decode_escaped_plaintext(content)

    normalized = (
        payload.get("response_text")
        or payload.get("response")
        or payload.get("result")
        or payload.get("message")
        or content
    )
    return decode_escaped_plaintext(normalized)
