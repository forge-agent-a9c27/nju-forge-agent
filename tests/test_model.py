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


if __name__ == "__main__":
    unittest.main()

