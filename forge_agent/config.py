"""Configuration loaded only from arguments, environment, or an ignored file."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    workspace: Path = Path.cwd()
    max_steps: int = 40
    context_tokens: int = 48_000
    command_timeout: int = 120
    approval_mode: str = "ask"
    show_reasoning: bool = False

    @property
    def chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @classmethod
    def load(
        cls,
        *,
        workspace: Optional[str] = None,
        config_path: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> "Config":
        values: Dict[str, Any] = {}
        path = Path(config_path) if config_path else Path(".forge/config.json")
        if path.is_file():
            try:
                values.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(f"cannot read config {path}: {exc}") from exc

        env_map = {
            "api_key": "FORGE_API_KEY",
            "model": "FORGE_MODEL",
            "base_url": "FORGE_BASE_URL",
            "max_steps": "FORGE_MAX_STEPS",
            "context_tokens": "FORGE_CONTEXT_TOKENS",
            "command_timeout": "FORGE_COMMAND_TIMEOUT",
            "approval_mode": "FORGE_APPROVAL_MODE",
        }
        for key, env_name in env_map.items():
            if os.environ.get(env_name):
                values[key] = os.environ[env_name]
        if workspace:
            values["workspace"] = workspace
        if overrides:
            values.update({k: v for k, v in overrides.items() if v is not None})

        if not values.get("api_key"):
            raise ConfigError(
                "missing API key; set FORGE_API_KEY or put api_key in the ignored "
                ".forge/config.json"
            )
        if not values.get("model"):
            raise ConfigError("missing model; set FORGE_MODEL or pass --model")

        try:
            result = cls(
                api_key=str(values["api_key"]),
                model=str(values["model"]),
                base_url=str(values.get("base_url", cls.base_url)),
                workspace=Path(values.get("workspace", Path.cwd())).resolve(),
                max_steps=int(values.get("max_steps", cls.max_steps)),
                context_tokens=int(values.get("context_tokens", cls.context_tokens)),
                command_timeout=int(
                    values.get("command_timeout", cls.command_timeout)
                ),
                approval_mode=str(
                    values.get("approval_mode", cls.approval_mode)
                ),
                show_reasoning=bool(values.get("show_reasoning", False)),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"invalid configuration: {exc}") from exc

        if not result.workspace.is_dir():
            raise ConfigError(f"workspace is not a directory: {result.workspace}")
        if not 1 <= result.max_steps <= 200:
            raise ConfigError("max_steps must be in [1, 200]")
        if result.context_tokens < 2_000:
            raise ConfigError("context_tokens must be at least 2000")
        if result.approval_mode not in {"ask", "auto", "read-only"}:
            raise ConfigError("approval_mode must be ask, auto, or read-only")
        return result

