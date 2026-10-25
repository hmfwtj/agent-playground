"""step8：agent 循环 —— 让模型自己决定调几次工具。

跑法：
    python steps/step8_agent_loop.py

这一步的新概念只有一个：**循环**。

step7 是「问一次 → 调一个工具 → 答」，硬编码两次请求。
真实场景里模型经常要连着调好几次：先查北京、再查上海、再算距离，
它自己决定要调几轮、调哪几个。你只负责提供工具，和「一直陪它转到它不调为止」。

所以循环的唯一正常出口是：**模型的 finish_reason 不是 tool_calls 了**。
不是你说的算，是它说的算。

直接回车就用默认问题，也可以自己输一个试试。
"""

import json
import os
import urllib.error
import urllib.request

from _load_env import ENV_FILE, load_env

load_env()

API_KEY = os.environ.get("DEEPSEEK_API_KEY") or ""
URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
TIMEOUT = 60

# ★ 保险：最多转几圈。没有这个数字，模型一旦陷入死循环，你的钱就没了。
MAX_STEPS = 5

DEFAULT_QUESTION = "北京和上海现在哪个更热？两地相距多远？"


# ── 工具说明书：这次给了两个 ────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的实时天气。凡是问天气、气温、冷热、下不下雨，都用这个。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，中文，如 北京、上海、深圳"}
                },
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
]


# ── 两个工具的真实实现（假数据，不联网）─────────────────────
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
    if city not in WEATHER_DB:
        return {"error": f"没有 {city} 的天气数据"}
    return WEATHER_DB[city]


def get_distance(city_a, city_b):
    # 两种顺序都认，省得模型问「上海到北京」就查不到
    if (city_a, city_b) in DISTANCE_DB:
        return {"from": city_a, "to": city_b, "km": DISTANCE_DB[(city_a, city_b)]}
    if (city_b, city_a) in DISTANCE_DB:
        return {"from": city_a, "to": city_b, "km": DISTANCE_DB[(city_b, city_a)]}
    return {"error": f"没有 {city_a} 到 {city_b} 的距离数据"}


TOOL_FUNCTIONS = {"get_weather": get_weather, "get_distance": get_distance}


# ── 发一次请求 ─────────────────────────────────────────────
def call_model(messages):
    body = {"model": MODEL, "messages": messages, "tools": TOOLS}

    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"HTTP {error.code}：{detail}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"网络错误（{getattr(error, 'reason', error)}）") from error

    choice = data["choices"][0]
    return choice["message"], choice.get("finish_reason")


# ── 执行一个工具调用，返回结果字典 ──────────────────────────
def run_tool(call):
    name = call["function"]["name"]
    args = json.loads(call["function"]["arguments"])
    print(f"      → {name}({args})")

    if name not in TOOL_FUNCTIONS:
        # 模型偶尔会编一个不存在的工具名。别崩，把错误告诉它，它会自己改。
        return name, {"error": f"没有叫 {name} 的工具，可用的有 {list(TOOL_FUNCTIONS)}"}

    try:
        result = TOOL_FUNCTIONS[name](**args)
    except Exception as error:
        # ★ 工具内部炸了，也要变成一条「结果」回传，而不是让整个程序挂掉。
        #   模型看到错误信息后，通常会换个参数再试，或者如实告诉你查不到。
        result = {"error": f"工具执行失败：{type(error).__name__}: {error}"}

    return name, result


def main():
    if not API_KEY:
        print("没找到 key。请打开这个文件：")
        print(f"    {ENV_FILE}")
        return

    try:
        question = input(f"你的问题（直接回车用默认：{DEFAULT_QUESTION}）：\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n再见。")
        return

    if not question:
        question = DEFAULT_QUESTION

    print(f"\n问题：{question}\n")

    messages = [{"role": "user", "content": question}]
    step = 0
    finished = False   # 记录「模型自己说完了」，用来区分正常结束和撞上限

    # ★★ 核心就是这一个循环 ★★
    while step < MAX_STEPS:
        step += 1
        print(f"── 第 {step} 轮请求")

        message, finish = call_model(messages)
        print(f"   模型：finish_reason = {finish}，content = {message.get('content') or '（空）'}")

        if finish != "tool_calls":
            # 它不要工具了，说明它已经能回答了。这是唯一的正常出口。
            print(f"\nAI：{message.get('content')}")
            messages.append(message)
            finished = True
            break

        # 要调工具：先把这条 assistant 消息入档，再逐个执行
        messages.append(message)
        print(f"   它要求调用 {len(message['tool_calls'])} 个工具：")

        for call in message["tool_calls"]:
            name, result = run_tool(call)
            print(f"        结果：{result}")
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })

    if not finished:
        print(f"\n⚠ 已经转了 {MAX_STEPS} 轮还没结束，强制停下。")
        print("   真实项目里，这种情况要么是工具一直报错，要么是模型陷进了死循环。")

    print("\n" + "─" * 46)
    print(f"一共 {len(messages)} 条消息，转 {step} 轮。结构：")
    for i, item in enumerate(messages, 1):
        if item["role"] == "assistant" and item.get("tool_calls"):
            names = [c["function"]["name"] for c in item["tool_calls"]]
            summary = f"（要求调用 {names}）"
        elif item["role"] == "tool":
            summary = str(item.get("content"))[:38]
        else:
            summary = str(item.get("content"))[:38]
        print(f"  {i}. {item['role']:<9} {summary}")


if __name__ == "__main__":
    main()
