"""Optional content PDP for LLM calls.

When OPA_GOV_API_URL is set, each prompt is POSTed as text/plain to
opa-gov-api's /evaluate (200 allow / 400 deny). When it is unset the
guardrail allows — local minikube does not deploy opa-gov-api by default.
Only user-role messages are evaluated: the injection patterns are meant for
untrusted input, and replaying earlier assistant/tool output from the chat
history (also when it arrives as a JSON body on a pass-through
endpoint) trips them on ordinary prose ("...in the system:"). Unreachable OPA
is fail-closed.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal, Optional

import httpx
from fastapi import HTTPException
from litellm.integrations.custom_guardrail import CustomGuardrail

LOGGER = logging.getLogger("litellm-gateway.pdp")

BLOCKED_MESSAGE = "This content was blocked due to security policy violation"


def _user_message_texts(messages: list) -> list[str]:
    texts: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or str(message.get("role") or "").lower() != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(
                part["text"]
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
    return texts


def _user_texts(inputs: dict) -> list[str]:
    messages = inputs.get("structured_messages")
    if messages:
        return _user_message_texts(messages)
    texts: list[str] = []
    for text in inputs.get("texts") or []:
        if not isinstance(text, str):
            continue
        # Pass-through endpoints (/v1/agent) hand over the whole request body
        # as one JSON string instead of structured_messages.
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        if isinstance(body, dict) and isinstance(body.get("messages"), list):
            texts.extend(_user_message_texts(body["messages"]))
        else:
            texts.append(text)
    return texts


class OpaPdpGuardrail(CustomGuardrail):
    def __init__(self, api_base: Optional[str] = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.api_base = (api_base or os.getenv("OPA_GOV_API_URL") or "").rstrip("/")

    async def apply_guardrail(
        self,
        inputs: Any,
        request_data: dict,
        input_type: Literal["request", "response"],
        logging_obj: Any = None,
    ) -> Any:
        if not isinstance(inputs, dict) or input_type != "request":
            return inputs
        texts = _user_texts(inputs)
        if not texts:
            LOGGER.info("event=pdp_decision PDP_Decision=ALLOW path=guardrail/empty")
            return inputs
        decision = await self._evaluate("\n".join(texts))
        if decision == "deny":
            LOGGER.info("event=pdp_decision PDP_Decision=DENY path=guardrail/evaluate")
            raise HTTPException(status_code=400, detail=BLOCKED_MESSAGE)
        if decision == "unreachable":
            raise HTTPException(
                status_code=503, detail="Content policy service unavailable"
            )
        LOGGER.info("event=pdp_decision PDP_Decision=ALLOW path=guardrail/evaluate")
        return inputs

    async def _evaluate(self, text: str) -> Literal["allow", "deny", "unreachable"]:
        if not self.api_base:
            return "allow"
        from litellm.llms.custom_httpx.http_handler import (
            get_async_httpx_client,
            httpxSpecialProvider,
        )

        client = get_async_httpx_client(llm_provider=httpxSpecialProvider.LoggingCallback)
        try:
            response = await client.post(
                f"{self.api_base}/evaluate",
                headers={"Content-Type": "text/plain"},
                content=text.encode("utf-8"),
                timeout=5.0,
            )
        except httpx.HTTPStatusError as exc:
            # LiteLLM's client raises on any non-2xx, so opa-gov-api's 400
            # policy block arrives here rather than as a response object.
            if exc.response.status_code == 400:
                return "deny"
            return self._unreachable()
        except Exception:
            return self._unreachable()
        return "allow" if response.status_code == 200 else "deny"

    @staticmethod
    def _unreachable() -> Literal["unreachable"]:
        LOGGER.warning(
            "event=pdp_decision PDP_Decision=DENY path=guardrail/opa_unreachable"
        )
        return "unreachable"
