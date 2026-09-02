"""Small protocol types shared by the model, loop, and tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Any

    def as_openai(self) -> Dict[str, Any]:
        import json

        arguments = self.arguments
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": arguments},
        }


@dataclass
class ModelResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)
    reasoning: str = ""
    transport: str = "native"

    def assistant_message(self) -> Dict[str, Any]:
        message: Dict[str, Any] = {
            "role": "assistant",
            "content": self.content or None,
        }
        if self.tool_calls:
            message["tool_calls"] = [call.as_openai() for call in self.tool_calls]
        return message


@dataclass
class ToolResult:
    ok: bool
    output: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def for_model(self) -> str:
        prefix = "OK" if self.ok else "ERROR"
        return f"[{prefix}] {self.output}"
