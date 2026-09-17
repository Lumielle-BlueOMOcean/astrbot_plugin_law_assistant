from __future__ import annotations

import json
import logging
import re
from typing import Any


def parse_json_response(text: str) -> dict[str, Any]:
    """Parse a JSON object from a provider response without executing content."""
    candidate = str(text or "").strip()
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        candidate,
        re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM response does not contain a JSON object") from None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("LLM response contains invalid JSON") from exc
    if not isinstance(value, dict):
        raise TypeError("LLM response JSON must be an object")
    return value


class LLMService:
    """Small compatibility wrapper around AstrBot's public Context LLM API."""

    def __init__(
        self,
        context: Any,
        provider_id: str = "",
        *,
        logger: Any | None = None,
    ) -> None:
        self.context = context
        self.provider_id = provider_id.strip()
        self.logger = logger or logging.getLogger(__name__)

    async def generate_json(
        self,
        prompt: str,
        *,
        session_origin: str | None = None,
    ) -> dict[str, Any] | None:
        provider_id = await self._resolve_provider_id(session_origin)
        if not provider_id:
            return None
        try:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=(
                    "你是信息抽取辅助器。只返回合法 JSON，不要补充原文没有的事实。"
                ),
            )
            completion = getattr(response, "completion_text", None)
            if not completion:
                return None
            return parse_json_response(completion)
        except Exception as exc:  # noqa: BLE001 - provider errors are fail-soft
            self.logger.warning("Law Assistant LLM extraction unavailable: %s", exc)
            return None

    async def _resolve_provider_id(self, session_origin: str | None) -> str:
        if session_origin and hasattr(self.context, "get_current_chat_provider_id"):
            try:
                value = await self.context.get_current_chat_provider_id(session_origin)
                if value:
                    return str(value)
            except Exception as exc:  # noqa: BLE001 - provider resolution is optional
                self.logger.debug("Interactive LLM provider unavailable: %s", exc)
        if self.provider_id:
            return self.provider_id
        try:
            provider = self.context.get_using_provider(session_origin)
            if provider is None:
                return ""
            meta = provider.meta()
            return str(getattr(meta, "id", "") or "")
        except Exception as exc:  # noqa: BLE001 - provider resolution is optional
            self.logger.debug("Default LLM provider unavailable: %s", exc)
            return ""


__all__ = ["LLMService", "parse_json_response"]
