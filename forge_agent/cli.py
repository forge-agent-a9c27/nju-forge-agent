"""Command-line entry point and multi-turn interactive session."""

from __future__ import annotations

import argparse
from typing import List, Optional

from . import __version__
from .agent import CodingAgent
from .config import Config, ConfigError
from .console import HELP, Console
from .model import ModelError, OpenAICompatibleClient
from .tools import ToolRuntime


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Framework-free coding agent for a local workspace",
    )
    parser.add_argument("prompt", nargs="*", help="one-shot task; omit for interactive mode")
    parser.add_argument("-w", "--workspace", default=".", help="workspace root (default: current directory)")
    parser.add_argument("-m", "--model", help="model name; defaults to FORGE_MODEL")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument("--config", help="JSON config path (default: .forge/config.json)")
    parser.add_argument("--max-steps", type=int, help="maximum tool iterations per task")
    parser.add_argument("--context-tokens", type=int, help="approximate context budget")
    parser.add_argument("--timeout", type=int, help="default command timeout in seconds")
    parser.add_argument(
        "--approval",
        choices=["ask", "auto", "read-only"],
        help="command/edit policy (default: ask)",
    )
    parser.add_argument("--show-reasoning", action="store_true", help="show reasoning_content when the provider returns it")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI color")
    parser.add_argument("--version", action="version", version=f"Forge {__version__}")
    return parser


def build_agent(config: Config, console: Console) -> CodingAgent:
    model = OpenAICompatibleClient(
        api_key=config.api_key,
        model=config.model,
        url=config.chat_completions_url,
    )
    tools = ToolRuntime(
        config.workspace,
        approval_mode=config.approval_mode,
        command_timeout=config.command_timeout,
        approve=console.approve,
    )
    return CodingAgent(
        model=model,
        tools=tools,
        console=console,
        workspace=config.workspace,
        max_steps=config.max_steps,
        context_tokens=config.context_tokens,
        show_reasoning=config.show_reasoning,
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = make_parser().parse_args(argv)
    console = Console(color=False if args.no_color else None)
    overrides = {
        "model": args.model,
        "base_url": args.base_url,
        "max_steps": args.max_steps,
        "context_tokens": args.context_tokens,
        "command_timeout": args.timeout,
        "approval_mode": args.approval,
        "show_reasoning": args.show_reasoning or None,
    }
    try:
        config = Config.load(
            workspace=args.workspace,
            config_path=args.config,
            overrides=overrides,
        )
        agent = build_agent(config, console)
    except (ConfigError, ModelError) as exc:
        console.error(str(exc))
        return 2

    console.banner(config.model, str(config.workspace), config.approval_mode)
    if args.prompt:
        result = agent.run(" ".join(args.prompt))
        return 0 if result.status == "completed" else 1

    while True:
        try:
            task = input("\nyou › ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print("\nUse /exit to quit.")
            continue
        if not task:
            continue
        command = task.lower()
        if command in {"/exit", "/quit"}:
            return 0
        if command == "/help":
            print(HELP)
            continue
        if command == "/clear":
            agent.clear()
            console.status("conversation cleared; workspace files were not changed")
            continue
        if command == "/status":
            console.status(agent.status_text())
            continue
        agent.run(task)


if __name__ == "__main__":
    raise SystemExit(main())
