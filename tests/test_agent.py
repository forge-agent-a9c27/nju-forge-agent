import tempfile
import unittest
from pathlib import Path

from forge_agent.agent import CodingAgent
from forge_agent.protocol import ModelResponse, ToolCall
from forge_agent.tools import ToolRuntime


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append((messages, tools))
        if not self.responses:
            raise AssertionError("unexpected model request")
        return self.responses.pop(0)


class SilentConsole:
    def status(self, *_args):
        pass

    def tool_call(self, *_args):
        pass

    def tool_result(self, *_args):
        pass

    def assistant(self, *_args):
        pass

    def warning(self, *_args):
        pass

    def error(self, *_args):
        pass


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.console = SilentConsole()

    def tearDown(self):
        self.temporary.cleanup()

    def make_agent(self, model, max_steps=10):
        tools = ToolRuntime(self.root, approval_mode="auto")
        return CodingAgent(
            model=model,
            tools=tools,
            console=self.console,
            workspace=self.root,
            max_steps=max_steps,
            context_tokens=8000,
        )

    def test_end_to_end_tool_loop(self):
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=[
                        ToolCall(
                            "call_write",
                            "write_file",
                            {"path": "answer.py", "content": "print(42)\n", "expected_sha256": "missing"},
                        )
                    ]
                ),
                ModelResponse(content="Implemented and verified the requested file."),
            ]
        )
        agent = self.make_agent(model)
        result = agent.run("create answer.py")
        self.assertEqual(result.status, "completed")
        self.assertEqual((self.root / "answer.py").read_text(encoding="utf-8"), "print(42)\n")
        second_messages = model.requests[1][0]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertIn("[OK]", second_messages[-1]["content"])

    def test_three_identical_batches_trigger_loop_guard(self):
        repeated = ModelResponse(
            tool_calls=[ToolCall("placeholder", "list_files", {"path": "."})]
        )
        model = ScriptedModel(
            [
                ModelResponse(tool_calls=[ToolCall("call_1", "list_files", {"path": "."})]),
                ModelResponse(tool_calls=[ToolCall("call_2", "list_files", {"path": "."})]),
                ModelResponse(tool_calls=[ToolCall("call_3", "list_files", {"path": "."})]),
                ModelResponse(content="Recovered from the repeated-call warning."),
            ]
        )
        agent = self.make_agent(model)
        result = agent.run("inspect")
        self.assertEqual(result.status, "completed")
        fourth_request_messages = model.requests[3][0]
        self.assertIn("loop guard", fourth_request_messages[-1]["content"])

    def test_step_limit_requests_tool_free_summary(self):
        model = ScriptedModel(
            [
                ModelResponse(tool_calls=[ToolCall("call_1", "list_files", {})]),
                ModelResponse(content="Reached the limit after inspecting files."),
            ]
        )
        agent = self.make_agent(model, max_steps=1)
        result = agent.run("inspect forever")
        self.assertEqual(result.status, "step_limit")
        self.assertEqual(model.requests[-1][1], [])


if __name__ == "__main__":
    unittest.main()
