"""agent 主循环的测试 —— 重点验证「摘要压缩」接入后没把原有流程弄坏。

做法：把两个会联网的函数换掉
    chat_once  → 返回预设好的回答
    summarize  → 返回预设好的摘要（或者抛错，用来测降级）

这样不用真调 API，也不花钱，而且能精确控制「模型第几次说什么」。

跑法：
    python -m unittest tests.test_agent -v
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_playground import agent
from agent_playground.llm import LLMError

SYSTEM = {"role": "system", "content": "你是助手"}


def make_config(max_messages=20, keep_recent=8):
    """造一个配置对象。

    故意不用真的 Config，因为这个测试不该去读 .env ——
    测的是循环逻辑，不是配置加载。
    """
    return SimpleNamespace(
        model="test-model",
        temperature=0.0,
        url="http://127.0.0.1:1/chat/completions",
        api_key="sk-fake",
        timeout=1.0,
        max_retries=0,
        max_context_messages=max_messages,
        summary_keep_recent=keep_recent,
        price_in=0.0,
        price_out=0.0,
    )


def reply(text="好的"):
    """模型正常回答一次。"""
    return {
        "message": {"role": "assistant", "content": text},
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def ask_tool(call_id="call_1", name="get_weather", arguments='{"city": "北京"}'):
    """模型要求调一次工具（content 是 None，这是真实结构）。"""
    return {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": call_id, "type": "function",
                 "function": {"name": name, "arguments": arguments}}
            ],
        },
        "finish_reason": "tool_calls",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def long_history(turns=5):
    """造一段足够长的纯聊天历史，每条 user 都带编号方便追踪谁被删了。"""
    messages = [SYSTEM]
    for index in range(turns):
        messages.append({"role": "user", "content": f"第{index}个问题"})
        messages.append({"role": "assistant", "content": f"第{index}个回答"})
    return messages


class TestBasicFlow(unittest.TestCase):
    """先锁住原有行为 —— 加功能之前，得先保证没弄坏旧的。"""

    def test_正常回答直接返回(self):
        messages = [SYSTEM, {"role": "user", "content": "你好"}]

        with patch.object(agent, "chat_once", return_value=reply("你好呀")):
            result = agent.run_agent(messages, make_config())

        self.assertEqual(result["text"], "你好呀")
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(result["steps"], 1)

    def test_工具调用会继续循环(self):
        """模型要工具 → 执行 → 再问一次 → 这次它给出答案。"""
        messages = [SYSTEM, {"role": "user", "content": "北京天气"}]
        script = [ask_tool(), reply("北京今天多云")]

        with patch.object(agent, "chat_once", side_effect=script):
            result = agent.run_agent(messages, make_config())

        self.assertEqual(result["text"], "北京今天多云")
        self.assertEqual(result["steps"], 2)
        # 工具结果必须进了历史，否则下一轮模型接不上
        self.assertTrue(any(m["role"] == "tool" for m in messages))

    def test_转满上限就停(self):
        """模型一直要工具，必须在 MAX_STEPS 处硬停，不能无限请求。"""
        messages = [SYSTEM, {"role": "user", "content": "北京天气"}]
        # 每次都给一个要工具的回答
        script = [ask_tool() for _ in range(agent.MAX_STEPS + 3)]

        with patch.object(agent, "chat_once", side_effect=script):
            result = agent.run_agent(messages, make_config())

        self.assertEqual(result["stop_reason"], "max_steps")
        self.assertEqual(result["steps"], agent.MAX_STEPS)


class TestCompression(unittest.TestCase):
    """摘要压缩接入后的行为。"""

    def test_历史不长时不压缩(self):
        messages = long_history(turns=2)  # 5 条，远小于 20

        with patch.object(agent, "chat_once", return_value=reply()):
            with patch.object(agent, "summarize") as fake_summary:
                result = agent.run_agent(messages, make_config())

        fake_summary.assert_not_called()
        self.assertFalse(result["summarized"])

    def test_超长时压缩并删掉早期消息(self):
        messages = long_history(turns=5)  # 11 条
        before = len(messages)
        config = make_config(max_messages=6, keep_recent=4)

        with patch.object(agent, "chat_once", return_value=reply()):
            with patch.object(agent, "summarize", return_value="用户问了五个问题"):
                result = agent.run_agent(messages, config)

        self.assertTrue(result["summarized"])
        self.assertEqual(result["summary"], "用户问了五个问题")
        # 压缩后历史变短（最后还会 append 一条 assistant，所以不能直接比 before）
        self.assertLess(len(messages), before + 1)

    def test_压缩后system必须还在(self):
        """这是最危险的一处：cut 是相对整个列表的下标，写错会把守则删掉。

        删掉 system 的后果不是崩溃，是模型从下一轮起不再遵守工具使用规则 ——
        没有任何报错，只是行为慢慢变得不对。
        """
        messages = long_history(turns=5)
        config = make_config(max_messages=6, keep_recent=4)

        with patch.object(agent, "chat_once", return_value=reply()):
            with patch.object(agent, "summarize", return_value="摘要"):
                agent.run_agent(messages, config)

        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "你是助手")

    def test_压缩失败时不删任何消息(self):
        """压缩挂了就退回「直接裁剪」，不能因为摘要失败整轮不能用。"""
        messages = long_history(turns=5)
        config = make_config(max_messages=6, keep_recent=4)
        original = [m for m in messages]

        with patch.object(agent, "chat_once", return_value=reply()):
            with patch.object(agent, "summarize", side_effect=LLMError("503")):
                result = agent.run_agent(messages, config)

        self.assertFalse(result["summarized"])
        # 最早那条问题必须还在 —— 说明什么都没删
        self.assertIn({"role": "user", "content": "第0个问题"}, messages)
        self.assertEqual(messages[:len(original)], original)

    def test_摘要会被合并进发出去的system(self):
        """摘要不能是「内部状态」，必须有办法真的送到模型眼前。"""
        messages = long_history(turns=5)
        config = make_config(max_messages=6, keep_recent=4)
        captured = {}

        def capture(window, config_, tools=None):
            captured["window"] = [dict(m) for m in window]
            return reply()

        with patch.object(agent, "chat_once", side_effect=capture):
            with patch.object(agent, "summarize", return_value="用户叫小明"):
                agent.run_agent(messages, config)

        window = captured["window"]
        systems = [m for m in window if m.get("role") == "system"]
        self.assertEqual(len(systems), 1, "发出了多条 system，某些兼容接口会 400")
        self.assertIn("用户叫小明", systems[0]["content"])
        self.assertIn("你是助手", systems[0]["content"])

    def test_keep_recent为零时彻底关闭摘要(self):
        """用户想退回老行为时，一个配置项就该关掉。"""
        messages = long_history(turns=5)
        config = make_config(max_messages=6, keep_recent=0)

        with patch.object(agent, "chat_once", return_value=reply()):
            with patch.object(agent, "summarize") as fake_summary:
                result = agent.run_agent(messages, config)

        fake_summary.assert_not_called()
        self.assertFalse(result["summarized"])

    def test_上一轮的摘要会传进这一轮(self):
        """压缩是增量的：旧摘要要带进下一次压缩，否则压过的内容会被忘掉。"""
        messages = long_history(turns=5)
        config = make_config(max_messages=6, keep_recent=4)
        captured = {}

        def fake_summarize(msgs, previous, config_):
            captured["previous"] = previous
            return "合并后的摘要"

        with patch.object(agent, "chat_once", return_value=reply()):
            with patch.object(agent, "summarize", side_effect=fake_summarize):
                result = agent.run_agent(messages, config, summary="上次的摘要")

        self.assertEqual(captured["previous"], "上次的摘要")
        self.assertEqual(result["summary"], "合并后的摘要")

    def test_返回值一定带summary键(self):
        """cli 每次都要靠这个键接住摘要。缺了会 KeyError，用 get 也会悄悄清空。"""
        messages = [SYSTEM, {"role": "user", "content": "你好"}]

        with patch.object(agent, "chat_once", return_value=reply()):
            result = agent.run_agent(messages, make_config(), summary="已有摘要")

        self.assertIn("summary", result)
        self.assertEqual(result["summary"], "已有摘要")


if __name__ == "__main__":
    unittest.main(verbosity=2)
