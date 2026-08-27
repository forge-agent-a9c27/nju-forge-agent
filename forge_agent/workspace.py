"""Workspace boundary, atomic writes, and an optimistic edit journal."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


class WorkspaceError(RuntimeError):
    pass


class Workspace:
    MAX_FILE_BYTES = 2 * 1024 * 1024
    INTERNAL_PARTS = {".git", ".forge"}
    SECRET_NAMES = {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "id_rsa",
        "id_ed25519",
        "credentials.json",
    }
    SECRET_SUFFIXES = {".pem", ".p12", ".pfx"}

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.journal = EditJournal(self.root)

    def resolve(self, user_path: str, *, allow_root: bool = False) -> Path:
        if not isinstance(user_path, str) or not user_path.strip():
            raise WorkspaceError("path must be a non-empty string")
        supplied = Path(user_path).expanduser()
        candidate = supplied if supplied.is_absolute() else self.root / supplied
        try:
            resolved = candidate.resolve(strict=False)
            relative = resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise WorkspaceError(f"path escapes workspace: {user_path}") from exc
        if not allow_root and resolved == self.root:
            raise WorkspaceError("this operation requires a file or subdirectory path")
        self._protect(relative)
        return resolved

    def relative(self, path: Path) -> str:
        value = path.resolve(strict=False).relative_to(self.root)
        text = value.as_posix()
        return text or "."

    def _protect(self, relative: Path) -> None:
        lowered_parts = [part.lower() for part in relative.parts]
        if any(part in self.INTERNAL_PARTS for part in lowered_parts):
            raise WorkspaceError("internal .git/.forge paths are protected")
        if not relative.parts:
            return
        name = relative.name.lower()
        if name in self.SECRET_NAMES or Path(name).suffix in self.SECRET_SUFFIXES:
            raise WorkspaceError(f"sensitive file is protected: {relative.as_posix()}")
        if re.fullmatch(r"\.env\..+", name) and name != ".env.example":
            raise WorkspaceError(f"sensitive environment file is protected: {name}")

    def read_bytes(self, path: Path) -> bytes:
        if not path.is_file():
            raise WorkspaceError(f"file does not exist: {self.relative(path)}")
        size = path.stat().st_size
        if size > self.MAX_FILE_BYTES:
            raise WorkspaceError(
                f"file is too large ({size} bytes; limit {self.MAX_FILE_BYTES})"
            )
        try:
            return path.read_bytes()
        except OSError as exc:
            raise WorkspaceError(f"cannot read {self.relative(path)}: {exc}") from exc

    def read_text(self, path: Path) -> str:
        data = self.read_bytes(path)
        if b"\x00" in data[:8192]:
            raise WorkspaceError(f"binary file cannot be read as text: {self.relative(path)}")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError(
                f"file is not valid UTF-8: {self.relative(path)}"
            ) from exc

    def sha256(self, path: Path) -> str:
        return hashlib.sha256(self.read_bytes(path)).hexdigest()

    def write_text(
        self, path: Path, content: str, expected_sha256: Optional[str] = None
    ) -> Tuple[str, str]:
        if not isinstance(content, str):
            raise WorkspaceError("content must be a string")
        encoded = content.encode("utf-8")
        if len(encoded) > self.MAX_FILE_BYTES:
            raise WorkspaceError(
                f"new content is too large ({len(encoded)} bytes; "
                f"limit {self.MAX_FILE_BYTES})"
            )
        existed = path.is_file()
        before = self.read_bytes(path) if existed else b""
        before_hash = hashlib.sha256(before).hexdigest() if existed else "missing"
        if expected_sha256 is not None and expected_sha256 != before_hash:
            raise WorkspaceError(
                "precondition failed: file changed since it was read "
                f"(expected {expected_sha256}, actual {before_hash})"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_replace(path, encoded)
        after_hash = hashlib.sha256(encoded).hexdigest()
        try:
            transaction = self.journal.record(
                path=path,
                existed=existed,
                before=before,
                before_hash=before_hash,
                after_hash=after_hash,
            )
        except WorkspaceError:
            # A successful write without its promised undo record is not a
            # successful transaction. Best-effort restore the exact prior state.
            if existed:
                self._atomic_replace(path, before)
            else:
                try:
                    path.unlink()
                except OSError:
                    pass
            raise
        return transaction, after_hash

    @staticmethod
    def _atomic_replace(path: Path, data: bytes) -> None:
        previous_mode = path.stat().st_mode if path.exists() else None
        temp_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=str(path.parent), prefix=".forge-write-", delete=False
            ) as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
                temp_name = temporary.name
            if previous_mode is not None:
                os.chmod(temp_name, previous_mode)
            os.replace(temp_name, path)
        except OSError as exc:
            raise WorkspaceError(f"atomic write failed for {path.name}: {exc}") from exc
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass


class EditJournal:
    """Stores local undo data outside model-visible paths."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / ".forge" / "journal"

    def record(
        self,
        *,
        path: Path,
        existed: bool,
        before: bytes,
        before_hash: str,
        after_hash: str,
    ) -> str:
        transaction = f"edit_{int(time.time())}_{uuid.uuid4().hex[:10]}"
        payload = {
            "version": 1,
            "transaction": transaction,
            "path": path.resolve().relative_to(self.root).as_posix(),
            "existed": existed,
            "before_base64": base64.b64encode(before).decode("ascii"),
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "created_at": int(time.time()),
            "undone": False,
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            journal_path = self.directory / f"{transaction}.json"
            journal_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise WorkspaceError(f"could not persist edit journal: {exc}") from exc
        return transaction

    def undo(self, transaction: str, workspace: Workspace) -> str:
        if not re.fullmatch(r"edit_[0-9]+_[0-9a-f]{10}", transaction):
            raise WorkspaceError("invalid transaction id")
        journal_path = self.directory / f"{transaction}.json"
        try:
            payload: Dict[str, Any] = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError(f"unknown or corrupt transaction: {transaction}") from exc
        if payload.get("undone"):
            raise WorkspaceError(f"transaction was already undone: {transaction}")
        target = workspace.resolve(str(payload["path"]))
        if not target.is_file():
            current_hash = "missing"
        else:
            current_hash = workspace.sha256(target)
        if current_hash != payload.get("after_sha256"):
            raise WorkspaceError(
                "undo refused because the file changed after this transaction"
            )
        before = base64.b64decode(payload.get("before_base64", ""), validate=True)
        if payload.get("existed"):
            Workspace._atomic_replace(target, before)
            action = "restored previous content"
        else:
            try:
                target.unlink()
            except OSError as exc:
                raise WorkspaceError(f"could not remove newly-created file: {exc}") from exc
            action = "removed newly-created file"
        payload["undone"] = True
        try:
            journal_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise WorkspaceError(f"undo succeeded but journal update failed: {exc}") from exc
        return f"{action}: {payload['path']}"
