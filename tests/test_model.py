import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from forge_agent.model import ModelError, OpenAICompatibleClient


class ModelParsingTests(unittest.TestCase):
    def make_client(self):
        return OpenAICompatibleClient(
            api_key="secret", model="mock", url="http://unused", max_retries=0
        )

    def test_parses_native_tool_call(self):
        payload = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"a.py"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
        result = self.make_client()._parse_response(json.dumps(payload).encode())
        self.assertEqual(result.tool_calls[0].name, "read_file")
        self.assertEqual(result.usage["prompt_tokens"], 10)

    def test_parses_tagged_fallback(self):
        payload = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": 'thinking <tool_call>{"name":"list_files","arguments":{}}</tool_call>'
                    },
                }
            ]
        }
        result = self.make_client()._parse_response(json.dumps(payload).encode())
        self.assertEqual(result.content, "thinking")
        self.assertEqual(result.tool_calls[0].name, "list_files")

    def test_parses_legacy_function_call(self):
        payload = {
            "choices": [
                {
                    "finish_reason": "function_call",
                    "message": {
                        "content": None,
                        "function_call": {
                            "name": "read_file",
                            "arguments": '{"path":"legacy.py"}',
                        },
                    },
                }
            ]
        }
        result = self.make_client()._parse_response(json.dumps(payload).encode())
        self.assertEqual(result.tool_calls[0].name, "read_file")
        self.assertIn("legacy.py", result.tool_calls[0].arguments)

    def test_rejects_malformed_response(self):
        with self.assertRaises(ModelError):
            self.make_client()._parse_response(b"{}")


class _Handler(BaseHTTPRequestHandler):
    request_json = None
    authorization = None

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        type(self).request_json = json.loads(self.rfile.read(length))
        type(self).authorization = self.headers.get("Authorization")
        response = json.dumps(
            {"choices": [{"finish_reason": "stop", "message": {"content": "done"}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_args):
        pass


class ModelHttpTests(unittest.TestCase):
    def test_direct_http_request(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OpenAICompatibleClient(
                api_key="local-secret",
                model="local-model",
                url=f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                max_retries=0,
            )
            result = client.complete([{"role": "user", "content": "hi"}], [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(result.content, "done")
        self.assertEqual(_Handler.request_json["model"], "local-model")
        self.assertNotIn("tools", _Handler.request_json)
        self.assertEqual(_Handler.authorization, "Bearer local-secret")


class _FallbackHandler(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        type(self).requests.append(request)
        if "tools" in request:
            message = {
                "content": None,
                "reasoning_content": "I should call list_files.",
            }
            finish_reason = "stop"
        elif any(
            item.get("role") == "user"
            and str(item.get("content", "")).lstrip().startswith("<tool_result name=")
            for item in request["messages"]
        ):
            message = {"content": "Finished after observing the directory."}
            finish_reason = "stop"
        else:
            message = {
                "content": '<tool_call>{"name":"list_files","arguments":{"path":"."}}</tool_call>'
            }
            finish_reason = "stop"
        response = json.dumps(
            {"choices": [{"finish_reason": finish_reason, "message": message}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_args):
        pass


class _EmptyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        response = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": None,
                            "reasoning_content": "unfinished thought",
                        },
                    }
                ],
                "usage": {"completion_tokens": 3},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_args):
        pass


class CompatibilityFallbackTests(unittest.TestCase):
    @staticmethod
    def _start(handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    @staticmethod
    def _stop(server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    def test_dropped_native_calls_switch_to_persistent_text_protocol(self):
        _FallbackHandler.requests = []
        server, thread = self._start(_FallbackHandler)
        client = OpenAICompatibleClient(
            api_key="secret",
            model="mock",
            url=f"http://127.0.0.1:{server.server_port}/chat/completions",
            max_retries=0,
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List files",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            }
        ]
        try:
            user = {"role": "user", "content": "inspect"}
            first = client.complete([user], tools)
            history = [
                user,
                first.assistant_message(),
                {
                    "role": "tool",
                    "tool_call_id": first.tool_calls[0].id,
                    "name": "list_files",
                    "content": "[OK] app.py",
                },
            ]
            # Tool-free finalization must still translate earlier native-form
            # assistant/tool history after the gateway fallback is active.
            second = client.complete(history, [])
        finally:
            self._stop(server, thread)

        self.assertEqual(first.transport, "text-fallback")
        self.assertEqual(first.tool_calls[0].name, "list_files")
        self.assertEqual(second.content, "Finished after observing the directory.")
        self.assertEqual(len(_FallbackHandler.requests), 3)
        self.assertIn("tools", _FallbackHandler.requests[0])
        self.assertNotIn("tools", _FallbackHandler.requests[1])
        self.assertNotIn("tools", _FallbackHandler.requests[2])
        self.assertTrue(
            any(
                "text tool protocol" in str(message.get("content", ""))
                for message in _FallbackHandler.requests[1]["messages"]
            )
        )
        self.assertNotIn(
            "tool", [item["role"] for item in _FallbackHandler.requests[2]["messages"]]
        )

    def test_empty_tool_free_response_is_an_error_with_diagnostics(self):
        server, thread = self._start(_EmptyHandler)
        try:
            client = OpenAICompatibleClient(
                api_key="secret",
                model="mock",
                url=f"http://127.0.0.1:{server.server_port}/chat/completions",
                max_retries=0,
            )
            with self.assertRaisesRegex(ModelError, "finish_reason='stop'"):
                client.complete([{"role": "user", "content": "hi"}], [])
        finally:
            self._stop(server, thread)


if __name__ == "__main__":
    unittest.main()
