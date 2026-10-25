"""step7：工具调用（tool calling）—— 让模型第一次能「做事」。

跑法：
    python steps/step7_tool_call.py

这一步只有一个新概念，但它是整个 agent 领域的地基：

    模型不会执行任何东西。
    它只会输出一段 JSON，说「我想调用 get_weather，参数是北京」。
    真正去执行的是你的代码。执行完你还得把结果再发它一次，它才会说人话。

整段流程就是三下：
    ① 你带上「工具说明书」发第一次请求
    ② 模型回一句「帮我调这个工具」，你的代码去执行
    ③ 你把执行结果发回去，模型给出最终回答

想换城市，改下面 QUESTION 那行的城市名就行。
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

# 想问的问题。改这一句就能换城市
QUESTION = "北京现在天气怎么样？"


# ── ① 工具说明书 ─────────────────────────────────────────────
# 这段 JSON 就是模型的「能力菜单」。它只能从这里挑。
# 三个字段缺一不可：
#   name        函数名，你代码里要靠它找到真正要执行的函数
#   description 什么时候该用它。这段写得好不好，直接决定模型会不会调错
#   parameters  参数长什么样，用的是 JSON Schema 写法
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的实时天气。凡是问天气、气温、下不下雨，都用这个。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名，中文，例如 北京、上海、深圳",
                    }
                },
                "required": ["city"],
            },
        },
    }
]


# ── ② 工具的真实实现（这里用假数据，不联网）──────────────────
WEATHER_DB = {
    "北京": {"weather": "多云", "temp_c": 18, "wind": "北风 3 级"},
    "上海": {"weather": "小雨", "temp_c": 21, "wind": "东风 2 级"},
    "深圳": {"weather": "晴", "temp_c": 27, "wind": "南风 1 级"},
}


def get_weather(city):
    print(f'    [你的代码在执行] get_weather("{city}")')
    if city not in WEATHER_DB:
        return {"error": f"没有 {city} 的数据"}
    return WEATHER_DB[city]


# 名字 → 函数。模型只会给名字，得靠这张表找到真正的函数。
TOOL_FUNCTIONS = {"get_weather": get_weather}


# ── ③ 发一次请求（非流式）────────────────────────────────────
# 这一步特意不用流式：带工具调用时模型返回的是结构化字段，
# 一次性拿到最好读。流式下 tool_calls 是一段段拼出来的，容易看晕。
def call_model(messages, tools=None):
    body = {"model": MODEL, "messages": messages}
    if tools:
        body["tools"] = tools

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


def main():
    if not API_KEY:
        print("没找到 key。请打开这个文件：")
        print(f"    {ENV_FILE}")
        return

    messages = [{"role": "user", "content": QUESTION}]

    # ── 第一次请求：只带说明书，不带执行结果 ────────────────
    print(f"你：{QUESTION}")
    print("\n① 第一次请求（带上工具说明书）")
    message, finish = call_model(messages, TOOLS)
    print(f"   模型的决定：finish_reason = {finish}")

    # 模型有时觉得自己就能答，不调工具。这不算错。
    if finish != "tool_calls":
        print(f"   它没调工具，直接回答：{message.get('content')}")
        return

    # ★ 这条带 tool_calls 的 assistant 消息，必须原样加回历史。
    #   不加，下一轮模型就不知道「我刚才要求调过什么」。
    messages.append(message)

    print(f"   它要求调用 {len(message['tool_calls'])} 个工具：")
    for i, call in enumerate(message["tool_calls"], 1):
        print(f"     {i}. id={call['id']}")
        print(f"        name={call['function']['name']}")
        # ★ arguments 是**字符串**，不是字典。必须先 json.loads 才能用。
        print(f"        arguments={call['function']['arguments']}   ← 注意这是字符串")

    # ── 执行 + 把结果塞回历史 ──────────────────────────────
    print("\n② 你的代码去执行（模型什么都没做）")
    for call in message["tool_calls"]:
        name = call["function"]["name"]
        args = json.loads(call["function"]["arguments"])  # 字符串 → 字典

        if name not in TOOL_FUNCTIONS:
            result = {"error": f"没有叫 {name} 的工具"}
        else:
            result = TOOL_FUNCTIONS[name](**args)         # 字典 → 关键字参数

        print(f"    执行结果：{result}")

        # ★ role 必须是 tool，而且 tool_call_id 必须和上面那个 id 对上，
        #   否则服务器不知道这条结果是给哪次调用的，直接报错。
        messages.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "content": json.dumps(result, ensure_ascii=False),
        })

    # ── 第二次请求：这次它有全部材料了 ──────────────────────
    print("\n③ 第二次请求（把执行结果发回去）")
    message2, finish2 = call_model(messages, TOOLS)
    print(f"   模型的决定：finish_reason = {finish2}")
    print(f"\nAI：{message2.get('content')}")

    # ── 把最后这份 messages 摊开给你看 ──────────────────────
    messages.append(message2)
    print("\n─────────────────────────────────────────────")
    print("这一轮结束时，messages 里一共这几条：")
    for i, item in enumerate(messages, 1):
        if item["role"] == "assistant" and item.get("tool_calls"):
            summary = f"（要求调用工具 {[c['function']['name'] for c in item['tool_calls']]}）"
        else:
            summary = str(item.get("content"))[:40]
        print(f"  {i}. {item['role']:<9} {summary}")


if __name__ == "__main__":
    main()
