"""Tool schemas and their local implementations."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .protocol import ToolCall, ToolResult
from .workspace import Workspace, WorkspaceError


Handler = Callable[[Dict[str, Any]], ToolResult]


@dataclass
class ToolSpec:
    name: str
    description: str
    properties: Dict[str, Any]
    required: Sequence[str]
    handler: Handler
    mutating: bool = False

    def api_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.properties,
                    "required": list(self.required),
                    "additionalProperties": False,
                },
            },
        }


class RiskAnalyzer:
    """Conservative command classifier. It is a guardrail, not a sandbox."""

    BLOCKED = [
        (re.compile(r"\b(?:format|diskpart|mkfs(?:\.[a-z0-9]+)?|fdisk)\b", re.I), "disk modification"),
        (re.compile(r"\b(?:shutdown|reboot|poweroff|halt)\b", re.I), "machine power control"),
        (re.compile(r"\breg\s+delete\s+HKLM", re.I), "system registry deletion"),
        (re.compile(r"rm\s+-[^\n]*r[^\n]*f[^\n]*(?:\s/\s*$|\s~|\$HOME|\s[A-Z]:[\\/]?\s*$)", re.I), "broad recursive deletion"),
        (re.compile(r"Remove-Item[^\n]*-Recurse[^\n]*(?:[A-Z]:\\\s*$|\$HOME|~)", re.I), "broad recursive deletion"),
        (re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;\s*:", re.I), "fork bomb"),
    ]
    REVIEW = [
        (re.compile(r"\b(?:rm|rmdir|del|erase|Remove-Item)\b", re.I), "deletes files"),
        (re.compile(r"\bgit\s+(?:reset|clean|push|commit|rebase)\b", re.I), "changes Git state or remote"),
        (re.compile(r"\b(?:curl|wget|Invoke-WebRequest|Invoke-RestMethod)\b", re.I), "network access"),
        (re.compile(r"\b(?:pip|pip3|npm|pnpm|yarn|gem)\s+(?:install|add|remove|uninstall)\b", re.I), "changes dependencies"),
        (re.compile(r"\b(?:sudo|runas|Start-Process\s+[^\n]*-Verb\s+RunAs)\b", re.I), "elevated privileges"),
        (re.compile(r"(?:^|\s)(?:>|>>|2>|&>)\s*[^=]", re.I), "shell output redirection"),
        (re.compile(r"(?:;|&&|\|\||`|\$\()", re.I), "compound shell execution"),
    ]
    SAFE = [
        re.compile(r"^(?:pwd|ls|dir|Get-ChildItem)(?:\s|$)", re.I),
        re.compile(r"^rg(?:\s|$)", re.I),
        re.compile(r"^git\s+(?:status|diff|log|show|branch)(?:\s|$)", re.I),
        re.compile(r"^(?:python|python3|py)(?:\s+-[A-Za-z]+)*\s+-m\s+(?:unittest|pytest|compileall)(?:\s|$)", re.I),
        re.compile(r"^(?:pytest|node\s+--test|go\s+test|cargo\s+(?:test|check)|dotnet\s+test)(?:\s|$)", re.I),
        re.compile(r"^(?:npm|pnpm|yarn)\s+(?:test|run\s+(?:test|lint|build))(?:\s|$)", re.I),
    ]

    @classmethod
    def classify(cls, command: str) -> Tuple[str, str]:
        normalized = command.strip()
        if not normalized:
            return "blocked", "empty command"
        if len(normalized) > 20_000:
            return "blocked", "command is unreasonably long"
        for pattern, reason in cls.BLOCKED:
            if pattern.search(normalized):
                return "blocked", reason
        for pattern, reason in cls.REVIEW:
            if pattern.search(normalized):
                return "review", reason
        if any(pattern.search(normalized) for pattern in cls.SAFE):
            return "safe", "read-only inspection or test command"
        return "review", "arbitrary code execution"


class ToolRuntime:
    MAX_OUTPUT_CHARS = 16_000
    SKIP_DIRS = {".git", ".forge", "node_modules", "__pycache__", ".venv", "venv"}

    def __init__(
        self,
        workspace: Path,
        *,
        approval_mode: str = "ask",
        command_timeout: int = 120,
        approve: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        self.workspace = Workspace(workspace)
        self.approval_mode = approval_mode
        self.command_timeout = command_timeout
        self.approve = approve or (lambda _command, _reason: False)
        self.plan: List[Dict[str, str]] = []
        self.specs: Dict[str, ToolSpec] = {}
        self._register_all()

    def schemas(self) -> List[Dict[str, Any]]:
        return [spec.api_schema() for spec in self.specs.values()]

    def execute(self, call: ToolCall) -> ToolResult:
        spec = self.specs.get(call.name)
        if spec is None:
            return ToolResult(False, f"unknown tool: {call.name}")
        if spec.mutating and self.approval_mode == "read-only":
            return ToolResult(False, f"{call.name} is disabled in read-only mode")
        try:
            arguments = self._parse_arguments(call.arguments)
            error = self._validate(spec, arguments)
            if error:
                return ToolResult(False, error)
            result = spec.handler(arguments)
        except WorkspaceError as exc:
            result = ToolResult(False, str(exc))
        except Exception as exc:  # Tool errors must become observations, never crash the loop.
            result = ToolResult(False, f"{type(exc).__name__}: {exc}")
        result.output = self._truncate(result.output)
        return result

    def _register(self, spec: ToolSpec) -> None:
        self.specs[spec.name] = spec

    def _register_all(self) -> None:
        string_path = {"type": "string", "description": "Path relative to the workspace"}
        self._register(
            ToolSpec(
                "list_files",
                "List a directory tree. Internal, dependency, and credential paths are hidden.",
                {
                    "path": {**string_path, "default": "."},
                    "max_depth": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
                },
                [],
                self._list_files,
            )
        )
        self._register(
            ToolSpec(
                "read_file",
                "Read a UTF-8 text file with line numbers and a SHA-256 revision for safe edits.",
                {
                    "path": string_path,
                    "start_line": {"type": "integer", "minimum": 1, "default": 1},
                    "end_line": {"type": "integer", "minimum": 1, "description": "Inclusive; at most 500 lines are returned"},
                },
                ["path"],
                self._read_file,
            )
        )
        self._register(
            ToolSpec(
                "search_files",
                "Regex-search UTF-8 files in the workspace without invoking a shell.",
                {
                    "pattern": {"type": "string", "description": "Python regular expression"},
                    "path": {**string_path, "default": "."},
                    "glob": {"type": "string", "default": "*", "description": "File glob such as *.py"},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 300, "default": 100},
                },
                ["pattern"],
                self._search_files,
            )
        )
        self._register(
            ToolSpec(
                "write_file",
                "Atomically create or replace a UTF-8 file. Returns an undo transaction id.",
                {
                    "path": string_path,
                    "content": {"type": "string"},
                    "expected_sha256": {"type": "string", "description": "Revision from read_file, or 'missing' for a new file"},
                },
                ["path", "content"],
                self._write_file,
                mutating=True,
            )
        )
        self._register(
            ToolSpec(
                "edit_file",
                "Exact-text edit with optimistic revision checking. Fails on ambiguity unless replace_all is true.",
                {
                    "path": string_path,
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                    "expected_sha256": {"type": "string", "description": "Optional revision from read_file"},
                },
                ["path", "old_text", "new_text"],
                self._edit_file,
                mutating=True,
            )
        )
        self._register(
            ToolSpec(
                "undo_edit",
                "Undo one write/edit transaction if the target has not changed since.",
                {"transaction_id": {"type": "string"}},
                ["transaction_id"],
                self._undo_edit,
                mutating=True,
            )
        )
        self._register(
            ToolSpec(
                "run_command",
                "Run a command locally in the workspace. Risky commands require user approval; catastrophic commands are blocked.",
                {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "description": "Seconds; defaults to configured timeout"},
                },
                ["command"],
                self._run_command,
                mutating=True,
            )
        )
        plan_item = {
            "type": "object",
            "properties": {
                "step": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            },
            "required": ["step", "status"],
            "additionalProperties": False,
        }
        self._register(
            ToolSpec(
                "update_plan",
                "Publish or revise the task plan. At most one step may be in_progress.",
                {"steps": {"type": "array", "items": plan_item, "minItems": 1, "maxItems": 20}},
                ["steps"],
                self._update_plan,
            )
        )

    @staticmethod
    def _parse_arguments(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            raise WorkspaceError("tool arguments must be a JSON object")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as first_error:
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                raise WorkspaceError(f"invalid tool argument JSON: {first_error}") from first_error
            try:
                value = json.loads(raw[start : end + 1])
            except json.JSONDecodeError as exc:
                raise WorkspaceError(f"invalid tool argument JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkspaceError("tool arguments must decode to an object")
        return value

    @staticmethod
    def _validate(spec: ToolSpec, arguments: Dict[str, Any]) -> Optional[str]:
        missing = [key for key in spec.required if key not in arguments]
        if missing:
            return f"missing required argument(s): {', '.join(missing)}"
        extra = sorted(set(arguments) - set(spec.properties))
        if extra:
            return f"unexpected argument(s): {', '.join(extra)}"
        types = {
            "string": str,
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for key, value in arguments.items():
            schema = spec.properties[key]
            expected = types.get(schema.get("type"))
            if expected and (not isinstance(value, expected) or (expected is int and isinstance(value, bool))):
                return f"argument {key!r} must be {schema['type']}"
            if "enum" in schema and value not in schema["enum"]:
                return f"argument {key!r} must be one of {schema['enum']}"
            if isinstance(value, int):
                if value < schema.get("minimum", value):
                    return f"argument {key!r} is below minimum {schema['minimum']}"
                if value > schema.get("maximum", value):
                    return f"argument {key!r} exceeds maximum {schema['maximum']}"
        return None

    def _iter_files(self, start: Path) -> Iterable[Path]:
        if start.is_file():
            yield start
            return
        for directory, dirs, files in os.walk(start):
            dirs[:] = sorted(d for d in dirs if d not in self.SKIP_DIRS)
            for filename in sorted(files):
                path = Path(directory) / filename
                try:
                    self.workspace.resolve(str(path))
                except WorkspaceError:
                    continue
                yield path

    def _list_files(self, args: Dict[str, Any]) -> ToolResult:
        start = self.workspace.resolve(args.get("path", "."), allow_root=True)
        if not start.exists():
            return ToolResult(False, f"path does not exist: {args.get('path', '.')}")
        if start.is_file():
            return ToolResult(True, f"{self.workspace.relative(start)} ({start.stat().st_size} bytes)")
        max_depth = args.get("max_depth", 4)
        lines: List[str] = []
        truncated = False
        for path in self._iter_files(start):
            try:
                depth = len(path.relative_to(start).parts)
            except ValueError:
                continue
            if depth > max_depth:
                continue
            lines.append(f"{self.workspace.relative(path)} ({path.stat().st_size} bytes)")
            if len(lines) >= 500:
                truncated = True
                break
        if not lines:
            return ToolResult(True, "directory contains no visible files")
        suffix = "\n... list limited to 500 files" if truncated else ""
        return ToolResult(True, "\n".join(lines) + suffix, {"count": len(lines)})

    def _read_file(self, args: Dict[str, Any]) -> ToolResult:
        path = self.workspace.resolve(args["path"])
        text = self.workspace.read_text(path)
        lines = text.splitlines()
        sha = self.workspace.sha256(path)
        if not lines:
            header = f"{self.workspace.relative(path)} | lines 0-0 of 0 | sha256 {sha}"
            return ToolResult(True, header + "\n<empty file>", {"sha256": sha, "lines": 0})
        start = args.get("start_line", 1)
        end = args.get("end_line", min(len(lines), start + 499))
        if end < start:
            return ToolResult(False, "end_line must be greater than or equal to start_line")
        end = min(end, start + 499, len(lines))
        if start > max(1, len(lines)):
            return ToolResult(False, f"start_line {start} exceeds file length {len(lines)}")
        selected = lines[start - 1 : end]
        numbered = "\n".join(f"{number:>5} | {line}" for number, line in enumerate(selected, start))
        header = f"{self.workspace.relative(path)} | lines {start}-{end} of {len(lines)} | sha256 {sha}"
        return ToolResult(True, header + ("\n" + numbered if numbered else "\n<empty file>"), {"sha256": sha, "lines": len(lines)})

    def _search_files(self, args: Dict[str, Any]) -> ToolResult:
        start = self.workspace.resolve(args.get("path", "."), allow_root=True)
        flags = 0 if args.get("case_sensitive", False) else re.IGNORECASE
        try:
            pattern = re.compile(args["pattern"], flags)
        except re.error as exc:
            return ToolResult(False, f"invalid regular expression: {exc}")
        file_glob = args.get("glob", "*")
        limit = args.get("max_results", 100)
        matches: List[str] = []
        skipped = 0
        for path in self._iter_files(start):
            relative = self.workspace.relative(path)
            if not (fnmatch.fnmatch(path.name, file_glob) or fnmatch.fnmatch(relative, file_glob)):
                continue
            try:
                text = self.workspace.read_text(path)
            except WorkspaceError:
                skipped += 1
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    clipped = line if len(line) <= 300 else line[:297] + "..."
                    matches.append(f"{relative}:{number}: {clipped}")
                    if len(matches) >= limit:
                        return ToolResult(True, "\n".join(matches) + f"\n... limited to {limit} matches", {"count": len(matches), "skipped": skipped})
        if not matches:
            return ToolResult(True, f"no matches (skipped {skipped} unreadable/binary files)", {"count": 0, "skipped": skipped})
        return ToolResult(True, "\n".join(matches), {"count": len(matches), "skipped": skipped})

    def _write_file(self, args: Dict[str, Any]) -> ToolResult:
        path = self.workspace.resolve(args["path"])
        transaction, sha = self.workspace.write_text(path, args["content"], args.get("expected_sha256"))
        return ToolResult(True, f"wrote {self.workspace.relative(path)} ({len(args['content'].encode('utf-8'))} bytes) | sha256 {sha} | undo {transaction}", {"transaction_id": transaction, "sha256": sha})

    def _edit_file(self, args: Dict[str, Any]) -> ToolResult:
        if not args["old_text"]:
            return ToolResult(False, "old_text must not be empty")
        path = self.workspace.resolve(args["path"])
        text = self.workspace.read_text(path)
        count = text.count(args["old_text"])
        if count == 0:
            return ToolResult(False, "old_text was not found; re-read the file before editing")
        if count > 1 and not args.get("replace_all", False):
            return ToolResult(False, f"old_text occurs {count} times; provide a larger unique span or set replace_all")
        replacements = count if args.get("replace_all", False) else 1
        updated = text.replace(args["old_text"], args["new_text"], replacements)
        transaction, sha = self.workspace.write_text(path, updated, args.get("expected_sha256"))
        return ToolResult(True, f"edited {self.workspace.relative(path)} ({replacements} replacement(s)) | sha256 {sha} | undo {transaction}", {"transaction_id": transaction, "sha256": sha, "replacements": replacements})

    def _undo_edit(self, args: Dict[str, Any]) -> ToolResult:
        output = self.workspace.journal.undo(args["transaction_id"], self.workspace)
        return ToolResult(True, output)

    def _run_command(self, args: Dict[str, Any]) -> ToolResult:
        command = args["command"]
        risk, reason = RiskAnalyzer.classify(command)
        if risk == "safe" and self._mentions_sensitive_or_external_path(command):
            risk, reason = "review", "may access a sensitive or out-of-workspace path"
        if risk == "blocked":
            return ToolResult(False, f"command permanently blocked: {reason}")
        if self.approval_mode == "read-only":
            return ToolResult(False, "commands are disabled in read-only mode")
        if risk == "review" and self.approval_mode == "ask" and not self.approve(command, reason):
            return ToolResult(False, f"user denied command: {reason}")
        timeout = args.get("timeout", self.command_timeout)
        timeout = min(timeout, 600)
        use_shell = False
        shell_executable = None
        if os.name == "nt":
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if powershell:
                argv = [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
            else:
                command_prompt = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
                # Let subprocess perform cmd.exe's quoting. Passing a nested
                # `python -c "..."` command as an argv element loses quotes on
                # some Windows runtimes.
                argv = command
                use_shell = True
                shell_executable = command_prompt
        else:
            argv = ["/bin/sh", "-lc", command]
        environment = self._scrubbed_environment()
        try:
            completed = subprocess.run(
                argv,
                shell=use_shell,
                executable=shell_executable,
                cwd=str(self.workspace.root),
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            partial = self._combine_output(exc.stdout or "", exc.stderr or "")
            return ToolResult(False, f"command timed out after {timeout}s\n{partial}", {"timed_out": True})
        except OSError as exc:
            return ToolResult(False, f"could not start command: {exc}")
        output = self._combine_output(completed.stdout, completed.stderr)
        status = "succeeded" if completed.returncode == 0 else "failed"
        return ToolResult(completed.returncode == 0, f"command {status} with exit code {completed.returncode}\n{output}", {"exit_code": completed.returncode, "risk": risk})

    def _update_plan(self, args: Dict[str, Any]) -> ToolResult:
        steps = args["steps"]
        if not steps or len(steps) > 20:
            return ToolResult(False, "plan must contain 1-20 steps")
        in_progress = 0
        normalized: List[Dict[str, str]] = []
        for index, item in enumerate(steps):
            if not isinstance(item, dict) or set(item) != {"step", "status"}:
                return ToolResult(False, f"plan item {index + 1} must contain only step and status")
            if not isinstance(item["step"], str) or not item["step"].strip():
                return ToolResult(False, f"plan item {index + 1} has an empty step")
            if item["status"] not in {"pending", "in_progress", "completed"}:
                return ToolResult(False, f"plan item {index + 1} has invalid status")
            in_progress += item["status"] == "in_progress"
            normalized.append({"step": item["step"].strip(), "status": item["status"]})
        if in_progress > 1:
            return ToolResult(False, "at most one plan step may be in_progress")
        self.plan = normalized
        icons = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        rendered = "\n".join(f"{icons[item['status']]} {item['step']}" for item in self.plan)
        return ToolResult(True, "plan updated\n" + rendered, {"steps": len(self.plan)})

    @classmethod
    def _truncate(cls, text: str) -> str:
        if len(text) <= cls.MAX_OUTPUT_CHARS:
            return text
        head = cls.MAX_OUTPUT_CHARS * 2 // 3
        tail = cls.MAX_OUTPUT_CHARS - head
        omitted = len(text) - cls.MAX_OUTPUT_CHARS
        return text[:head] + f"\n... [{omitted} chars omitted] ...\n" + text[-tail:]

    @staticmethod
    def _combine_output(stdout: Any, stderr: Any) -> str:
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        parts = []
        if str(stdout).strip():
            parts.append("STDOUT:\n" + str(stdout).rstrip())
        if str(stderr).strip():
            parts.append("STDERR:\n" + str(stderr).rstrip())
        return "\n".join(parts) if parts else "<no output>"

    @staticmethod
    def _scrubbed_environment() -> Dict[str, str]:
        sensitive = re.compile(r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE[_-]?KEY)", re.I)
        return {key: value for key, value in os.environ.items() if not sensitive.search(key)}

    @staticmethod
    def _mentions_sensitive_or_external_path(command: str) -> bool:
        suspicious = re.compile(
            r"(?:"
            r"(?:^|\s)\.\.(?:[\\/]|\s|$)|"
            r"(?:^|\s)~(?:[\\/]|\s|$)|"
            r"(?:^|\s)[A-Z]:[\\/]|"
            r"(?:^|\s)/(?:etc|home|root|Users|var|opt|usr|System)(?:/|\s|$)|"
            r"\$(?:HOME|USERPROFILE)|%USERPROFILE%|"
            r"(?:^|[\\/\s])\.env(?:\s|$)|"
            r"(?:^|[\\/\s])(?:\.git|\.forge|id_rsa|id_ed25519)(?:[\\/\s]|$)|"
            r"--hidden"
            r")",
            re.IGNORECASE,
        )
        return bool(suspicious.search(command))
