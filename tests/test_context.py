"""context.py 的测试。

跑法（在 agent-playground 目录下）：
    python -m unittest discover -s tests -t . -v
    python tests/test_context.py

这也是纯函数测试：给一个列表，看返回的列表对不对。不联网、不花钱。

这一组的重点不是「留了几条」，而是「留下来的结构是否合法」。
因为裁剪最容易犯的错是切在工具消息中间 —— 那种列表本地看不出问题，
发出去才会被服务端拒绝，报错信息还很难懂。
"""

import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_playground.context import trim  # noqa: E402


def build_history(turns=6):
    """造一段带工具调用的真实形状历史。

    每一轮长 4 条：
        user → assistant(要调工具) → tool(结果) → assistant(最终回答)
    """
    messages = [{"role": "system", "content": "守则"}]
    for i in range(1, turns + 1):
        call_id = f"call_{i}"
        messages.append({"role": "user", "content": f"问题{i}"})
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": "get_weather", "arguments": "{}"},
            }],
        })
        messages.append({"role": "tool", "tool_call_id": call_id, "content": "{}"})
        messages.append({"role": "assistant", "content": f"回答{i}"})
    return messages


class TestTrim(unittest.TestCase):

    def assert_structure_valid(self, window):
        """检查窗口结构合法：每条 tool 消息都能找到它的来源调用。"""
        call_ids = set()
        for message in window:
            if message.get("role") == "assistant" and message.get("tool_calls"):
                call_ids.update(c["id"] for c in message["tool_calls"])
            elif message.get("role") == "tool":
                self.assertIn(
                    message["tool_call_id"], call_ids,
                    "窗口里出现了没有来源的 tool 消息，发出去会被服务端拒绝",
                )

    def test_short_history_is_returned_as_is(self):
        """没超限就原样返回。"""
        messages = build_history(turns=1)   # 5 条
        self.assertEqual(trim(messages, 20), messages)

    def test_returns_a_copy_not_the_same_object(self):
        """必须返回副本。

        返回同一个列表会造成「别名」问题：上层再 append，窗口会跟着变长，
        于是「本次发送 N 条」这个数字悄悄错掉，而且不报错。
        """
        messages = build_history(turns=6)
        window = trim(messages, 10)
        self.assertIsNot(window, messages)

        before = len(window)
        messages.append({"role": "user", "content": "新的一条"})
        self.assertEqual(len(window), before, "窗口被原列表的 append 影响了")

    def test_system_message_is_kept(self):
        """system 是工作守则，任何情况下都不能丢。"""
        window = trim(build_history(turns=6), 10)
        self.assertEqual(window[0]["role"], "system")

    def test_window_starts_with_user(self):
        """窗口的第一条必须是 user 消息。"""
        for limit in (4, 5, 6, 7, 8, 9, 10):
            window = trim(build_history(turns=6), limit)
            self.assertEqual(
                window[1]["role"], "user",
                f"max_messages={limit} 时窗口从 {window[1]['role']} 开始，结构不合法",
            )

    def test_tool_messages_keep_their_caller(self):
        """★ 最关键：被保留下来的工具结果，必须还能找到调用它的那条 assistant。"""
        for limit in range(4, 22):
            window = trim(build_history(turns=6), limit)
            with self.subTest(max_messages=limit):
                self.assert_structure_valid(window)

    def test_history_length_is_never_increased(self):
        """裁剪只会变短，不会变长；也不许改动传进来的原列表。"""
        messages = build_history(turns=6)
        original = list(messages)
        window = trim(messages, 10)
        self.assertLessEqual(len(window), 10)
        self.assertEqual(messages, original, "trim 改动了传进来的历史")

    def test_handles_empty_and_system_only(self):
        """边界：空列表、只有 system，都不能崩。"""
        self.assertEqual(trim([], 10), [])
        self.assertEqual(trim([{"role": "system", "content": "x"}], 10),
                         [{"role": "system", "content": "x"}])

    def test_zero_means_no_trimming(self):
        """0 或负数当作「不裁剪」，方便临时关掉它排查问题。"""
        messages = build_history(turns=6)
        self.assertEqual(len(trim(messages, 0)), len(messages))

    def test_never_drops_the_last_question(self):
        """余额小到装不下一整轮时，宁可超一点，也必须带上最后一轮的问题。

        这是测试逼出来的一个真 bug：最初的写法在这种极端情况下会返回
        「只有 system」的列表，发出去等于问了个不存在的问题。
        """
        for limit in (1, 2, 3, 4):
            window = trim(build_history(turns=6), limit)
            roles = [m["role"] for m in window]
            self.assertIn("user", roles, f"max_messages={limit} 时窗口里没有 user 消息")


if __name__ == "__main__":
    unittest.main()
