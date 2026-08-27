import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from forge_agent.cli import main


class _ScriptedProvider(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        type(self).calls += 1
        if type(self).calls == 1:
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_e2e_write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {
                                    "path": "hello.py",
                                    "content": "print('hello from Forge')\n",
                                    "expected_sha256": "missing",
                                }
                            ),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            self.server.last_request = request
            message = {"content": "Created hello.py through the complete agent loop."}
            finish_reason = "stop"
        body = json.dumps(
            {"choices": [{"message": message, "finish_reason": finish_reason}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class CliEndToEndTests(unittest.TestCase):
    def test_cli_http_agent_tool_and_final_answer(self):
        _ScriptedProvider.calls = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ScriptedProvider)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        output = StringIO()
        error = StringIO()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                env = {"FORGE_API_KEY": "local-test-only", "FORGE_MODEL": "scripted"}
                argv = [
                    "--no-color",
                    "--base-url",
                    f"http://127.0.0.1:{server.server_port}/v1",
                    "--workspace",
                    temporary,
                    "create a hello program",
                ]
                with patch.dict(os.environ, env), redirect_stdout(output), redirect_stderr(error):
                    exit_code = main(argv)
                generated = Path(temporary, "hello.py").read_text(encoding="utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(exit_code, 0, error.getvalue())
        self.assertEqual(generated, "print('hello from Forge')\n")
        self.assertIn("Created hello.py", output.getvalue())
        tool_observation = server.last_request["messages"][-1]
        self.assertEqual(tool_observation["role"], "tool")
        self.assertIn("[OK]", tool_observation["content"])


if __name__ == "__main__":
    unittest.main()

