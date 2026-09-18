"""Optional content PDP for LLM calls.

When OPA_GOV_API_URL is set, each prompt is POSTed as text/plain to
opa-gov-api's /evaluate (200 allow / 400 deny). When it is unset the
guardrail allows — local minikube does not deploy opa-gov-api by default.
Unreachable OPA is fail-closed.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal, Optional

from litellm.integrations.custom_guardrail import CustomGuardrail

LOGGER = logging.getLogger("litellm-gateway.pdp")


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
        texts = list(inputs.get("texts") or [])
        if not texts:
            LOGGER.info("event=pdp_decision PDP_Decision=ALLOW path=guardrail/empty")
            return inputs
        combined = "\n".join(text for text in texts if isinstance(text, str))
        if not await self._evaluate(combined):
            LOGGER.info("event=pdp_decision PDP_Decision=DENY path=guardrail/evaluate")
            raise Exception("This content was blocked due to security policy violation")
        LOGGER.info("event=pdp_decision PDP_Decision=ALLOW path=guardrail/evaluate")
        return inputs

    async def _evaluate(self, text: str) -> bool:
        if not self.api_base:
            return True
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
        except Exception:
            LOGGER.warning(
                "event=pdp_decision PDP_Decision=DENY path=guardrail/opa_unreachable"
            )
            return False
        return response.status_code == 200
