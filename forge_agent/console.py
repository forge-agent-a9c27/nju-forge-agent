"""Minimal terminal UI; ANSI is optional and no UI dependency is required."""

from __future__ import annotations

import os
import sys
import textwrap
from typing import Optional


class Console:
    COLORS = {
        "cyan": "\033[36m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "red": "\033[31m",
        "dim": "\033[2m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }

    def __init__(self, *, color: Optional[bool] = None) -> None:
        if color is None:
            color = bool(sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
        self.color = color

    def _paint(self, value: str, *styles: str) -> str:
        if not self.color:
            return value
        prefix = "".join(self.COLORS[x] for x in styles)
        return f"{prefix}{value}{self.COLORS['reset']}"

    def banner(self, model: str, workspace: str, approval: str) -> None:
        title = self._paint("FORGE", "bold", "cyan")
        print(f"{title}  transparent coding agent")
        print(self._paint(f"model: {model}", "dim"))
        print(self._paint(f"workspace: {workspace}", "dim"))
        print(self._paint(f"approval: {approval}  |  /help for commands", "dim"))

    def status(self, message: str) -> None:
        print(self._paint(f"  - {message}", "dim"))

    def tool_call(self, name: str, preview: str) -> None:
        preview = " ".join(preview.split())
        if len(preview) > 100:
            preview = preview[:97] + "..."
        print(f"{self._paint('>', 'cyan')} {name} {self._paint(preview, 'dim')}")

    def tool_result(self, ok: bool, summary: str) -> None:
        # ASCII markers remain readable on legacy Windows GBK consoles.
        marker = self._paint("OK", "green") if ok else self._paint("!!", "red")
        first_line = summary.strip().splitlines()[0] if summary.strip() else "done"
        if len(first_line) > 120:
            first_line = first_line[:117] + "..."
        print(f"{marker} {first_line}")

    def assistant(self, message: str) -> None:
        if message.strip():
            print(f"\n{self._paint('Forge', 'bold', 'cyan')}: {message.strip()}\n")

    def warning(self, message: str) -> None:
        print(self._paint(f"warning: {message}", "yellow"), file=sys.stderr)

    def error(self, message: str) -> None:
        print(self._paint(f"error: {message}", "red"), file=sys.stderr)

    def approve(self, command: str, reason: str) -> bool:
        if not sys.stdin.isatty():
            self.warning("review-required command denied because stdin is not interactive")
            return False
        print(self._paint("\nCommand needs approval", "bold", "yellow"))
        print(textwrap.indent(command, "  "))
        print(self._paint(f"  risk: {reason}", "yellow"))
        try:
            answer = input("Run it? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return answer in {"y", "yes"}


HELP = """Commands:
  /help       show this help
  /clear      start a fresh conversation (files are unchanged)
  /status     show context and loop statistics
  /exit       quit Forge

Enter a programming task in natural language. Use Ctrl+C once to stop the
current run, or /exit at the prompt to leave.
"""
