"""Direct OpenAI-compatible HTTP client—no model or agent SDK involved."""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence

from .protocol import ModelResponse, ToolCall


class ModelError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        url: str,
        timeout: int = 180,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.url = url
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep
        self.extra_headers = self._extra_headers()

    @staticmethod
    def _extra_headers() -> Dict[str, str]:
        raw = os.environ.get("FORGE_EXTRA_HEADERS_JSON", "")
        if not raw:
            return {}
        try:
            headers = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelError(f"FORGE_EXTRA_HEADERS_JSON is invalid JSON: {exc}") from exc
        if not isinstance(headers, dict):
            raise ModelError("FORGE_EXTRA_HEADERS_JSON must be a JSON object")
        return {str(key): str(value) for key, value in headers.items()}

    def complete(
        self, messages: Sequence[Dict[str, Any]], tools: Sequence[Dict[str, Any]]
    ) -> ModelResponse:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Forge-Agent/1.0",
            **self.extra_headers,
        }

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                self.url, data=encoded, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                return self._parse_response(raw)
            except urllib.error.HTTPError as exc:
                body = exc.read(4096).decode("utf-8", errors="replace")
                retryable = exc.code in {408, 409, 429} or 500 <= exc.code <= 599
                if not retryable or attempt >= self.max_retries:
                    raise ModelError(
                        self._redact(f"model API returned HTTP {exc.code}: {body}")
                    ) from exc
                retry_after = exc.headers.get("Retry-After")
                delay = self._retry_delay(attempt, retry_after)
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                if attempt >= self.max_retries:
                    raise ModelError(self._redact(f"model API unavailable: {exc}")) from exc
                delay = self._retry_delay(attempt, None)
            self.sleep(delay)
        raise AssertionError("retry loop did not terminate")

    def _parse_response(self, raw: bytes) -> ModelResponse:
        try:
            data = json.loads(raw.decode("utf-8"))
            choice = data["choices"][0]
            message = choice["message"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ModelError(f"malformed model response: {exc}") from exc

        content = self._content_to_text(message.get("content"))
        calls: List[ToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            try:
                function = raw_call["function"]
                calls.append(
                    ToolCall(
                        id=str(raw_call.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                        name=str(function["name"]),
                        arguments=function.get("arguments", "{}"),
                    )
                )
            except (KeyError, TypeError) as exc:
                raise ModelError(f"malformed tool call: {raw_call!r}") from exc

        if not calls:
            content, calls = self._parse_tagged_calls(content)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ModelResponse(
            content=content,
            tool_calls=calls,
            finish_reason=choice.get("finish_reason"),
            usage={str(k): int(v) for k, v in usage.items() if isinstance(v, int)},
            reasoning=str(message.get("reasoning_content") or ""),
        )

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            pieces = []
            for block in content:
                if isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                    pieces.append(str(block.get("text", "")))
            return "\n".join(pieces)
        return str(content)

    @staticmethod
    def _parse_tagged_calls(content: str) -> tuple:
        """Fallback for compatible servers that serialize calls into text."""
        pattern = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
        calls: List[ToolCall] = []
        for match in pattern.finditer(content):
            try:
                value = json.loads(match.group(1))
                calls.append(
                    ToolCall(
                        id=f"call_{uuid.uuid4().hex[:12]}",
                        name=str(value["name"]),
                        arguments=value.get("arguments", {}),
                    )
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        cleaned = pattern.sub("", content).strip() if calls else content
        return cleaned, calls

    @staticmethod
    def _retry_delay(attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                return min(20.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return min(8.0, 0.75 * (2**attempt))

    def _redact(self, message: str) -> str:
        return message.replace(self.api_key, "***") if self.api_key else message
