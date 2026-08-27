import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge_agent.protocol import ToolCall
from forge_agent.tools import RiskAnalyzer, ToolRuntime


def call(name, arguments):
    return ToolCall("call_test", name, arguments)


class ToolRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text(
            "def greet(name):\n    return f'hello {name}'\n", encoding="utf-8"
        )
        (self.root / "empty.txt").write_text("", encoding="utf-8")
        self.runtime = ToolRuntime(self.root, approval_mode="auto", command_timeout=10)

    def tearDown(self):
        self.temporary.cleanup()

    def test_list_read_and_search(self):
        listed = self.runtime.execute(call("list_files", {}))
        self.assertTrue(listed.ok)
        self.assertIn("src/app.py", listed.output)

        read = self.runtime.execute(call("read_file", {"path": "src/app.py"}))
        self.assertTrue(read.ok)
        self.assertIn("sha256", read.output)
        self.assertIn("2 |     return", read.output)

        searched = self.runtime.execute(
            call("search_files", {"pattern": "GREET", "glob": "*.py"})
        )
        self.assertTrue(searched.ok)
        self.assertIn("src/app.py:1", searched.output)

    def test_empty_file_is_readable(self):
        result = self.runtime.execute(call("read_file", {"path": "empty.txt"}))
        self.assertTrue(result.ok, result.output)
        self.assertIn("<empty file>", result.output)

    def test_write_edit_ambiguity_and_undo(self):
        written = self.runtime.execute(
            call(
                "write_file",
                {"path": "new.txt", "content": "same same", "expected_sha256": "missing"},
            )
        )
        self.assertTrue(written.ok, written.output)
        transaction = written.metadata["transaction_id"]

        ambiguous = self.runtime.execute(
            call(
                "edit_file",
                {"path": "new.txt", "old_text": "same", "new_text": "new"},
            )
        )
        self.assertFalse(ambiguous.ok)
        self.assertIn("occurs 2 times", ambiguous.output)

        undone = self.runtime.execute(
            call("undo_edit", {"transaction_id": transaction})
        )
        self.assertTrue(undone.ok, undone.output)
        self.assertFalse((self.root / "new.txt").exists())

    def test_bad_arguments_become_tool_error(self):
        malformed = self.runtime.execute(call("read_file", "not json"))
        self.assertFalse(malformed.ok)
        extra = self.runtime.execute(
            call("read_file", {"path": "src/app.py", "surprise": True})
        )
        self.assertFalse(extra.ok)
        self.assertIn("unexpected", extra.output)

    def test_read_only_mode_blocks_mutation(self):
        runtime = ToolRuntime(self.root, approval_mode="read-only")
        result = runtime.execute(
            call("write_file", {"path": "blocked.txt", "content": "no"})
        )
        self.assertFalse(result.ok)
        self.assertFalse((self.root / "blocked.txt").exists())

    def test_plan_invariant(self):
        result = self.runtime.execute(
            call(
                "update_plan",
                {
                    "steps": [
                        {"step": "one", "status": "in_progress"},
                        {"step": "two", "status": "in_progress"},
                    ]
                },
            )
        )
        self.assertFalse(result.ok)
        self.assertIn("at most one", result.output)

    def test_command_risk_classification(self):
        self.assertEqual(RiskAnalyzer.classify("python -m unittest")[0], "safe")
        self.assertEqual(RiskAnalyzer.classify("git push origin main")[0], "review")
        self.assertEqual(RiskAnalyzer.classify("shutdown /s")[0], "blocked")
        self.assertEqual(RiskAnalyzer.classify("rm -rf C:\\")[0], "blocked")
        self.assertTrue(
            ToolRuntime._mentions_sensitive_or_external_path("rg token ../private")
        )
        self.assertTrue(
            ToolRuntime._mentions_sensitive_or_external_path("rg token .env")
        )

    def test_command_environment_does_not_receive_secret(self):
        command = (
            "python -c \"import os; print(os.environ.get('FORGE_API_KEY', 'scrubbed'))\""
        )
        with patch.dict(os.environ, {"FORGE_API_KEY": "do-not-leak"}):
            result = self.runtime.execute(call("run_command", {"command": command}))
        self.assertTrue(result.ok, result.output)
        self.assertIn("scrubbed", result.output)
        self.assertNotIn("do-not-leak", result.output)


if __name__ == "__main__":
    unittest.main()
