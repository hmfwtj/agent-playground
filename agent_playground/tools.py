"""工具层：告诉模型「有哪些工具可用」，并真正执行它们。

这个文件里有三样东西，顺序就是它们的关系：

    1. TOOLS          说明书 —— 给模型看的菜单（纯文本，模型看不见你的 Python 代码）
    2. get_xxx()      真实函数 —— 真正干活的部分
    3. TOOL_FUNCTIONS 调度表 —— 把「模型给的名字」翻译成「你的函数」

为什么必须分成三份，不能合成一份？
    因为模型能收到的只有文本。它返回给你的也只是一串名字加参数。
    「名字 → 函数」这一步，只能由你在本地完成。这就是工具调用的全部秘密。

注意参数名的纪律：
    说明书里的参数名，必须和真实函数的参数名**一字不差**。
    写成 city_name 而函数收 city，程序会直接崩：
        TypeError: get_weather() got an unexpected keyword argument 'city_name'
    而且模型完全不知道自己错了，它只是照着你的说明书画葫芦。
"""

import json  # 这里用到 loads：模型给的参数是一段 JSON 文本
from datetime import datetime

# ── 1. 说明书：模型看到的菜单 ────────────────────────────────
# 每个工具都是三层：type / function / parameters。
# 第一个 "type": "function" 说的是「这是哪种工具」（不是参数类型！）；
# parameters 里的 "type": "object" 说的是「参数整体是一个字典」。
# 两个 type 只是字面相同，含义完全无关。
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的实时天气。凡是问天气、气温、冷热、下不下雨，都用这个。",
            "parameters": {
                "type": "object",
                "properties": {
                    # 这里的键名 city 必须和 get_weather(city) 的参数名一致
                    "city": {"type": "string", "description": "城市名，中文，如 北京、上海、深圳"},
                },
                # required 里的名字，模型一定会给；没写进去的，它可能直接省掉
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_distance",
            "description": "查询两个城市之间的直线距离，单位公里。问「多远」「距离」时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city_a": {"type": "string", "description": "出发城市"},
                    "city_b": {"type": "string", "description": "目的城市"},
                },
                "required": ["city_a", "city_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前的日期和时间。凡涉及「今天几号」「现在几点」「现在是什么时候」都用这个。",
            "parameters": {
                # 这个工具不需要任何参数，也要写成空对象，
                # 不能省略 parameters —— 省了模型会以为参数格式未知，行为不可预测。
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ── 2. 真实实现 ────────────────────────────────────────────
# 天气和距离用假数据（不联网、不花钱），时间用真的。
# 换成真接口时只改这三个函数，说明书一个字都不用动 —— 这就是分层的好处。
WEATHER_DB = {
    "北京": {"weather": "多云", "temp_c": 18},
    "上海": {"weather": "小雨", "temp_c": 21},
    "深圳": {"weather": "晴", "temp_c": 27},
}

DISTANCE_DB = {
    ("北京", "上海"): 1213,
    ("北京", "深圳"): 2160,
    ("上海", "深圳"): 1435,
}


def get_weather(city):
    """查天气。查不到时**返回**一个错误字典，而不是抛异常。"""
    # 为什么返回错误而不是 raise：
    #   错误也是「工具的结果」的一种。交给模型，它往往会自己换个城市问，
    #   或者如实告诉你查不到。抛异常则会让整个程序挂掉，用户只看到一片红字。
    if city not in WEATHER_DB:
        return {"error": f"没有 {city} 的天气数据"}
    return dict(WEATHER_DB[city])


def get_distance(city_a, city_b):
    if (city_a, city_b) in DISTANCE_DB:
        return {"from": city_a, "to": city_b, "km": DISTANCE_DB[(city_a, city_b)]}
    # 两种顺序都认，省得模型问「上海到北京」就查不到
    if (city_b, city_a) in DISTANCE_DB:
        return {"from": city_a, "to": city_b, "km": DISTANCE_DB[(city_b, city_a)]}
    return {"error": f"没有 {city_a} 到 {city_b} 的距离数据"}


def get_current_time():
    now = datetime.now()
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "weekday": "星期" + "一二三四五六日"[now.weekday()],
    }


# ── 3. 调度表：名字 → 函数 ─────────────────────────────────
# 模型只会给你一个字符串 "get_weather"。
# 只有这张表能把它变成真正可调用的函数。模型永远碰不到你的代码。
TOOL_FUNCTIONS = {
    "get_weather": get_weather,
    "get_distance": get_distance,
    "get_current_time": get_current_time,
}


def run_tool(call):
    """执行模型要求的一次工具调用。

    传入的 call 是模型返回的一整块，长这样：
        {
          "id": "call_00_xxx",
          "type": "function",
          "function": {"name": "get_weather", "arguments": "{\\"city\\": \\"北京\\"}"}
        }

    返回 (工具名, 参数, 结果字典)。
    永远不抛异常 —— 出错也是一种结果，要回传给模型。

    这个文件里不 print 任何东西。要显示过程是 cli 的事，
    所以参数原样带出去，让上层决定怎么展示。
    """
    name = call["function"]["name"]

    # ★ arguments 是**字符串**，不是字典。
    #   它是一段 JSON 文本："{\"city\": \"北京\"}"。必须先 loads 成字典，
    #   才能用 **args 拆开传给函数。九成新手第一次都直接当字典用，然后报 TypeError。
    raw_args = call["function"]["arguments"] or "{}"
    try:
        # 正常情况下 raw_args 是字符串，要 loads 成字典。
        # 但个别网关/框架会直接给一个字典，这里用 isinstance 兼容两种：
        # 是字符串就解析，不是字符串就当成已经是字典。
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
    except (json.JSONDecodeError, TypeError):
        return name, raw_args, {"error": f"参数不是合法 JSON：{raw_args}"}

    if name not in TOOL_FUNCTIONS:
        # 模型偶尔会编一个你没提供的工具名。别崩，告诉它有哪些可用的，它下一轮就改。
        return name, args, {"error": f"没有叫 {name} 的工具，可用的有 {list(TOOL_FUNCTIONS)}"}

    try:
        result = TOOL_FUNCTIONS[name](**args)
    except Exception as error:
        # 参数名对不上、参数个数不对，都会走这里。
        # 仍然当作结果回传，让模型看到原因（它可能会改参数重试）。
        result = {"error": f"工具执行失败：{type(error).__name__}: {error}"}

    return name, args, result
