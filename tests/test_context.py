import unittest

from forge_agent.context import Conversation


class ContextTests(unittest.TestCase):
    def test_compaction_keeps_tool_bundle_valid(self):
        conversation = Conversation("system", token_budget=2000)
        for index in range(12):
            conversation.add_user(f"request {index} " + "x" * 400)
            conversation.add(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{index}",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                }
            )
            conversation.add(
                {
                    "role": "tool",
                    "tool_call_id": f"call_{index}",
                    "name": "read_file",
                    "content": "result " + "y" * 400,
                }
            )

        compacted = conversation.for_model()
        self.assertGreater(conversation.compactions, 0)
        self.assertLessEqual(conversation.token_estimate, 2100)
        self.assertEqual(compacted[0]["role"], "system")
        self.assertEqual(compacted[1].get("name"), "digest")
        for index, message in enumerate(compacted):
            if message.get("role") == "tool":
                self.assertGreater(index, 0)
                previous_ids = {
                    call["id"]
                    for call in compacted[index - 1].get("tool_calls", [])
                }
                self.assertIn(message["tool_call_id"], previous_ids)

    def test_clear_preserves_system_only(self):
        conversation = Conversation("rules", token_budget=2000)
        conversation.add_user("task")
        conversation.clear()
        self.assertEqual(conversation.messages, [{"role": "system", "content": "rules"}])


if __name__ == "__main__":
    unittest.main()
