import tempfile
import unittest
from pathlib import Path

from forge_agent.workspace import Workspace, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = Workspace(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_rejects_escape_and_sensitive_paths(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.resolve("../outside.txt")
        with self.assertRaises(WorkspaceError):
            self.workspace.resolve(".git/config")
        with self.assertRaises(WorkspaceError):
            self.workspace.resolve(".env")
        self.assertEqual(
            self.workspace.resolve(".env.example"), self.root / ".env.example"
        )

    def test_write_revision_and_undo(self):
        target = self.workspace.resolve("src/hello.txt")
        transaction, first_hash = self.workspace.write_text(
            target, "hello\n", expected_sha256="missing"
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "hello\n")
        self.assertEqual(self.workspace.sha256(target), first_hash)
        result = self.workspace.journal.undo(transaction, self.workspace)
        self.assertIn("removed", result)
        self.assertFalse(target.exists())

    def test_undo_refuses_to_clobber_newer_change(self):
        target = self.workspace.resolve("note.txt")
        transaction, _ = self.workspace.write_text(target, "version one")
        target.write_text("external change", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "changed after"):
            self.workspace.journal.undo(transaction, self.workspace)
        self.assertEqual(target.read_text(encoding="utf-8"), "external change")

    def test_optimistic_revision_detects_conflict(self):
        target = self.workspace.resolve("note.txt")
        self.workspace.write_text(target, "one")
        with self.assertRaisesRegex(WorkspaceError, "precondition failed"):
            self.workspace.write_text(target, "two", expected_sha256="incorrect")
        self.assertEqual(target.read_text(encoding="utf-8"), "one")


if __name__ == "__main__":
    unittest.main()

