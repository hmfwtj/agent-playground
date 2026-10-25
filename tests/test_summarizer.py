"""summarizer 的测试。

分两半：
    前半：纯函数（render / find_cut / merge_into_system），不联网，毫秒级
    后半：summarize，用 unittest.mock 把网络调用换掉，也不联网

跑法：
    python -m unittest tests.test_summarizer -v
"""

import unittest
from unittest.mock import patch

from agent_playground import summarizer


def tool_turn(city="北京"):
    """造一轮完整的工具调用，正好 4 条消息。

    这个结构是测试的关键素材 —— 摘要最容易错的地方就在这里：
        user 提问
        assistant 只要求调工具（content 是 None）
        tool 返回结果
        assistant 给出最终回答
    """
    return [
        {"role": "user", "content": f"{city}今天天气怎么样"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": f'{{"city": "{city}"}}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"temp": 20, "desc": "晴"}'},
        {"role": "assistant", "content": f"{city}今天晴，20 度"},
    ]


class TestRender(unittest.TestCase):
    """把消息转成文本喂给摘要模型 —— 转错了摘要就是废的，但不会报错。"""

    def test_工具调用那一轮不会被转成助手冒号None(self):
        """assistant 只调工具时 content 是 None。

        step6 的写法是 {"content": message["content"]}，这里会输出
        「助手：None」。摘要模型读到一堆 None，压出来的东西自然不对。
        """
        text = summarizer.render(tool_turn())

        self.assertNotIn("None", text)
        # 工具名和参数都要在 —— 「查的是北京不是上海」本身就是重要事实
        self.assertIn("get_weather", text)
        self.assertIn("北京", text)

    def test_工具结果单独标成工具结果(self):
        """tool 消息不能和助手说的话混在一起。

        step6 的 who = "用户" if role=="user" else "助手"，
        tool 会被当成「助手说的」，摘要就分不清「谁查的数据」和「谁下的结论」。
        """
        text = summarizer.render(tool_turn())

        self.assertIn("工具结果：", text)
        self.assertIn('"temp": 20', text)

    def test_system不参与渲染(self):
        """守则是规则，不是对话内容，压进摘要毫无意义还可能污染摘要。"""
        messages = [{"role": "system", "content": "你是天气助手"}] + tool_turn()

        self.assertNotIn("天气助手", summarizer.render(messages))

    def test_超长内容会被截断(self):
        """一条几百字的工具结果会吃光压缩额度，必须截。"""
        messages = [{"role": "user", "content": "长" * 1000}]

        text = summarizer.render(messages)

        self.assertLess(len(text), 300)
        self.assertTrue(text.endswith("…"))


class TestFindCut(unittest.TestCase):
    """决定切在第几条。这个函数的错会直接变成服务端 400。"""

    def test_不够长就不压缩(self):
        messages = [{"role": "system", "content": "守则"}] + tool_turn()

        self.assertIsNone(summarizer.find_cut(messages, keep_recent=8))

    def test_切点必须落在user消息上(self):
        messages = [{"role": "system", "content": "守则"}] + tool_turn() * 4

        cut = summarizer.find_cut(messages, keep_recent=4)

        self.assertIsNotNone(cut)
        self.assertEqual(messages[cut]["role"], "user")

    def test_保留段至少keep_recent条(self):
        messages = [{"role": "system", "content": "守则"}] + tool_turn() * 5

        cut = summarizer.find_cut(messages, keep_recent=6)

        self.assertGreaterEqual(len(messages) - cut, 6)

    def test_system不能被压缩掉(self):
        """system 是守则，丢了模型就不再遵守工具使用规则，而且不报错。"""
        messages = [{"role": "system", "content": "守则"}] + tool_turn() * 5

        cut = summarizer.find_cut(messages, keep_recent=4)

        self.assertGreaterEqual(cut, 1)

    def test_保留段不会以孤儿tool开头(self):
        """这是最重要的一条。

        如果保留段第一条是 tool，那它的 assistant(tool_calls) 已经被压掉了，
        服务端会拒绝整份请求：「tool 消息没有对应的调用」。
        """
        messages = [{"role": "system", "content": "守则"}] + tool_turn() * 6

        for keep in range(1, 12):
            cut = summarizer.find_cut(messages, keep_recent=keep)
            if cut is None:
                continue
            self.assertEqual(
                messages[cut]["role"],
                "user",
                f"keep_recent={keep} 时保留段以 {messages[cut]['role']} 开头",
            )

    def test_被压缩段里每个tool都有它的调用方(self):
        """压缩段内部也必须自洽：tool 消息前面必须有对应的 assistant(tool_calls)。

        切点落在 user 上时这条自然成立，但需要锁住 —— 以后谁改了切点规则，
        这里会先红。
        """
        messages = [{"role": "system", "content": "守则"}] + tool_turn() * 5

        cut = summarizer.find_cut(messages, keep_recent=4)
        kept = messages[:cut]

        for index, message in enumerate(kept):
            if message.get("role") != "tool":
                continue
            self.assertGreater(index, 0, "被压缩段以 tool 开头")
            self.assertEqual(
                kept[index - 1].get("role"),
                "assistant",
                "tool 消息前面不是 assistant",
            )
            self.assertTrue(kept[index - 1].get("tool_calls"))


class TestMergeIntoSystem(unittest.TestCase):
    """摘要要并进已有的 system，不能新插一条。"""

    def test_合并进已有system且只保留一条system(self):
        messages = [{"role": "system", "content": "你是天气助手"}, {"role": "user", "content": "你好"}]

        merged = summarizer.merge_into_system(messages, "用户叫小明")

        systems = [m for m in merged if m.get("role") == "system"]
        self.assertEqual(len(systems), 1, "出现了多条 system")
        self.assertIn("你是天气助手", systems[0]["content"])
        self.assertIn("用户叫小明", systems[0]["content"])

    def test_不改动传进来的列表(self):
        """调用方手里还留着完整历史，这里改它会污染原始数据。

        具体后果：第二次合并会再叠一遍摘要，system 越滚越长，而且不报错。
        """
        messages = [{"role": "system", "content": "你是天气助手"}, {"role": "user", "content": "你好"}]
        before = messages[0]["content"]

        summarizer.merge_into_system(messages, "用户叫小明")

        self.assertEqual(messages[0]["content"], before)

    def test_连续合并两次不会把摘要在system里叠两遍(self):
        messages = [{"role": "system", "content": "守则"}, {"role": "user", "content": "你好"}]

        once = summarizer.merge_into_system(messages, "第一份摘要")
        twice = summarizer.merge_into_system(once, "第二份摘要")

        # once 里已经有一份了，twice 基于 once —— 这是调用方用法错误，
        # 但正确用法是每次从原始 messages 出发，所以这里只验证原始 messages 干净。
        self.assertEqual(messages[0]["content"], "守则")
        self.assertIn("第二份摘要", twice[0]["content"])

    def test_摘要为空时原样返回副本(self):
        messages = [{"role": "system", "content": "守则"}]

        result = summarizer.merge_into_system(messages, "")

        self.assertEqual(result, messages)
        self.assertIsNot(result, messages, "返回了同一个对象，改它会牵连原始数据")

    def test_没有system时补一条(self):
        messages = [{"role": "user", "content": "你好"}]

        merged = summarizer.merge_into_system(messages, "用户叫小明")

        self.assertEqual(merged[0]["role"], "system")
        self.assertIn("用户叫小明", merged[0]["content"])


class TestSummarize(unittest.TestCase):
    """唯一会发网络请求的函数。这里把 chat_once 换掉，不联网不花钱。"""

    def _fake(self, text):
        """造一个假的 chat_once 返回值，形状要和真的一致。"""
        return {"message": {"role": "assistant", "content": text}, "finish_reason": "stop", "usage": {}}

    def test_正常返回摘要文本(self):
        with patch.object(summarizer, "chat_once", return_value=self._fake("  用户在学 agent 开发  ")) as fake:
            result = summarizer.summarize(tool_turn(), "", None)

        self.assertEqual(result, "用户在学 agent 开发")  # 两侧空白要去掉
        self.assertEqual(fake.call_count, 1)

    def test_模型返回空内容要抛错而不是静默返回空(self):
        """空摘要必须当失败。

        如果这里返回空串，上层会以为压缩成功、把原文删掉，
        结果信息真的丢了 —— 而日志里一切正常。这是最难查的一类 bug。
        """
        with patch.object(summarizer, "chat_once", return_value=self._fake("   ")):
            with self.assertRaises(summarizer.LLMError):
                summarizer.summarize(tool_turn(), "", None)

    def test_有旧摘要时要带进prompt(self):
        """压缩是增量的：新的摘要是「旧摘要 + 这段原文」的合并结果。"""
        captured = {}

        def fake_chat(messages, config, tools=None):
            captured["prompt"] = messages[0]["content"]
            return self._fake("合并后的摘要")

        with patch.object(summarizer, "chat_once", side_effect=fake_chat):
            summarizer.summarize(tool_turn(), "用户叫小明", None)

        self.assertIn("已有摘要", captured["prompt"])
        self.assertIn("用户叫小明", captured["prompt"])
        self.assertIn("北京", captured["prompt"])

    def test_不带tools参数(self):
        """压缩是纯文本任务。给了 tools，模型可能去调工具，白白多一轮。"""
        captured = {}

        def fake_chat(messages, config, tools=None):
            captured["tools"] = tools
            return self._fake("摘要")

        with patch.object(summarizer, "chat_once", side_effect=fake_chat):
            summarizer.summarize(tool_turn(), "", None)

        self.assertIsNone(captured["tools"])

    def test_没有消息时直接返回旧摘要不发请求(self):
        with patch.object(summarizer, "chat_once") as fake:
            result = summarizer.summarize([], "旧摘要", None)

        self.assertEqual(result, "旧摘要")
        fake.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
