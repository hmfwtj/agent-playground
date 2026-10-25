"""tools.py 的自动化测试。

跑法（任选一种，都在 agent-playground 目录下）：
    python -m unittest discover -s tests -t . -v     推荐
    python tests/test_tools.py                       单跑这一个文件

这些测试**不联网、不花一分钱、不到 0.1 秒跑完**。
因为它们只测纯函数：给一个输入，看输出对不对。

为什么先测这个文件：
    整个项目里，只有 tools.py 是「纯逻辑」——不碰网络、不读键盘、不打印。
    纯函数最好测，也最容易测出真问题。网络那层难测得多，下次再处理。

unittest 只有三个东西要记：
    1. 写一个类，继承 unittest.TestCase
    2. 类里每个 test_ 开头的方法，都是一个测试，互相独立
    3. 用 self.assertEqual(实际值, 预期值) 判断；不相等就算失败
"""

import inspect
import json
import sys
import unittest
from pathlib import Path

# 允许直接 `python tests/test_tools.py` 跑。
# 直接跑文件时，Python 只把 tests/ 放进搜索路径，找不到 agent_playground 包，
# 所以手动把项目根补进去。用 -m unittest 跑就不需要这几行，但留着无害。
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_playground import tools  # noqa: E402  —— 必须放在补路径之后


def make_call(name, args):
    """造一条「模型返回的工具调用」，结构照着真实的抄。

    真实返回长这样（这是整个项目最该记住的数据结构之一）：
        {"id": "call_00_xxx",
         "type": "function",
         "function": {"name": "get_weather",
                      "arguments": "{\\"city\\": \\"北京\\"}"}}
    注意 arguments 是**字符串**，不是字典。
    """
    # 如果传进来已经是 dict，就转成字符串，模拟真实情况
    raw = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return {
        "id": "call_test_1",
        "type": "function",
        "function": {"name": name, "arguments": raw},
    }


class TestDeclaration(unittest.TestCase):
    """检查「说明书」和「真实函数」是否一致。

    这一组是整个测试文件里最有价值的部分。
    因为它们防的不是「计算结果算错了」，而是「说明书和代码悄悄对不上」——
    那种错误不会在测试时暴露，只会在真实对话时才崩。
    """

    def test_every_tool_has_implementation(self):
        """说明书里写了的工具，必须有对应函数。"""
        for item in tools.TOOLS:
            name = item["function"]["name"]
            self.assertIn(name, tools.TOOL_FUNCTIONS, f"{name} 只写了说明书，没有实现")

    def test_every_function_is_declared(self):
        """写了函数却没告诉模型，等于白写。"""
        declared = {item["function"]["name"] for item in tools.TOOLS}
        for name in tools.TOOL_FUNCTIONS:
            self.assertIn(name, declared, f"{name} 有实现但没写进说明书，模型看不到")

    def test_parameters_match_signature(self):
        """★ 最关键的测试：说明书里的参数名，必须和函数签名逐字一致。

        inspect.signature(func).parameters 能拿到函数的真实参数名。
        拿它跟 properties 的键名对比，一个都不能差。

        为什么重要：如果说明书写 city_name、函数收 city，模型会老老实实传
        city_name，然后报
            TypeError: get_weather() got an unexpected keyword argument 'city_name'
        而模型完全不知道自己错了 —— 它只是照着你的说明书画葫芦。
        这种 bug 只能靠这个测试提前发现，光看代码看不出来。
        """
        for item in tools.TOOLS:
            spec = item["function"]
            name = spec["name"]
            func = tools.TOOL_FUNCTIONS[name]

            declared = set(spec["parameters"]["properties"])
            actual = set(inspect.signature(func).parameters)

            self.assertEqual(
                declared, actual,
                f"{name} 的说明书参数 {sorted(declared)} 和函数签名 {sorted(actual)} 不一致",
            )

    def test_required_matches_reality(self):
        """required 里写的，必须真的出现在 properties 里。"""
        for item in tools.TOOLS:
            spec = item["function"]
            props = set(spec["parameters"]["properties"])
            required = set(spec["parameters"]["required"])
            self.assertTrue(
                required <= props,
                f"{spec['name']} 的 required 里有 properties 中不存在的键：{required - props}",
            )

    def test_every_tool_explains_itself(self):
        """description 不能是空的。

        模型就是靠这段文字决定「多个工具里选哪个」。
        空描述不会报错，但会让它在该选一个的时候把几个全调一遍。
        """
        for item in tools.TOOLS:
            spec = item["function"]
            self.assertTrue(spec["description"].strip(), f"{spec['name']} 没有描述")


class TestRunTool(unittest.TestCase):
    """测 run_tool：各种奇怪的输入都不能让程序崩。"""

    def test_normal_call(self):
        """正常调用：参数正确，返回数据。"""
        name, args, result = tools.run_tool(make_call("get_weather", {"city": "北京"}))
        self.assertEqual(name, "get_weather")
        self.assertEqual(args, {"city": "北京"})
        self.assertEqual(result["weather"], "多云")

    def test_arguments_as_dict(self):
        """容错：个别网关会把 arguments 直接给成字典而不是字符串。

        以前的写法 json.loads(字典) 会抛 TypeError，而那个异常没被捕获，
        整个程序就挂了。所以现在用 isinstance 判断一下。
        """
        call = make_call("get_weather", {"city": "上海"})
        call["function"]["arguments"] = {"city": "上海"}
        _, _, result = tools.run_tool(call)
        self.assertEqual(result["weather"], "小雨")

    def test_bad_json_returns_error(self):
        """参数不是合法 JSON：返回错误，不抛异常。"""
        _, _, result = tools.run_tool(make_call("get_weather", "{city: 北京}"))
        self.assertIn("error", result)

    def test_unknown_tool(self):
        """模型编了一个不存在的工具：告诉它有哪些可用的，别崩。"""
        _, _, result = tools.run_tool(make_call("get_stock_price", {"code": "600519"}))
        self.assertIn("error", result)
        self.assertIn("get_weather", result["error"])

    def test_wrong_argument_name_is_caught(self):
        """参数名对不上：TypeError 被捕获，变成一条结果回传。

        这一条是在模拟「说明书写错」之后会发生什么。
        注意它断言的是「不崩」，而不是「查到了」——
        程序要的是不崩，正确性靠上面 test_parameters_match_signature 保证。
        """
        _, _, result = tools.run_tool(make_call("get_weather", {"city_name": "北京"}))
        self.assertIn("error", result)
        self.assertIn("TypeError", result["error"])

    def test_no_argument_tool(self):
        """不需要参数的工具，传空字典也能跑。"""
        _, _, result = tools.run_tool(make_call("get_current_time", {}))
        self.assertIn("date", result)

    def test_missing_data_returns_error(self):
        """查得到函数、但查不到数据：仍然是结果，不是异常。"""
        _, _, result = tools.run_tool(make_call("get_weather", {"city": "广州"}))
        self.assertEqual(result, {"error": "没有 广州 的天气数据"})

    def test_distance_accepts_both_orders(self):
        """距离要认两种顺序，否则「上海到北京」就查不到。"""
        _, _, forward = tools.run_tool(make_call("get_distance", {"city_a": "北京", "city_b": "上海"}))
        _, _, backward = tools.run_tool(make_call("get_distance", {"city_a": "上海", "city_b": "北京"}))
        self.assertEqual(forward["km"], 1213)
        self.assertEqual(backward["km"], 1213)

    def test_never_raises(self):
        """最后一道保险：乱七八糟的输入，一个都不许抛异常。

        run_tool 的契约是「永远返回三个值」。一旦它抛异常，
        agent 循环就会中断，用户看到一片红色报错。
        所以这里把最恶劣的几种输入全试一遍。
        """
        nasty_calls = [
            make_call("get_weather", {}),                    # 少了必填参数
            make_call("get_weather", '{"city": null}'),       # 值是 null
            make_call("", {}),                                # 空工具名
            make_call("get_weather", "[1, 2, 3]"),            # 参数量是列表
        ]
        for call in nasty_calls:
            try:
                name, args, result = tools.run_tool(call)
            except Exception as error:  # noqa: BLE001 —— 这里就是要抓到任何异常
                self.fail(f"run_tool 不该抛异常，但抛了：{type(error).__name__}: {error}")
            self.assertIsInstance(result, dict)


if __name__ == "__main__":
    # 加上这一句，就可以直接 python tests/test_tools.py 跑，
    # 不用记 discover 那一长串。两种跑法等价。
    unittest.main()
