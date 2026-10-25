"""第 2 步：改成流式输出，一个字一个字往外蹦。

跑法：
    python steps/step2_first_stream.py

和 step1 相比，请求体只多了一个参数，但「读服务器回话」的方式完全变了。
"""

import json
import os
import urllib.error
import urllib.request

# 读项目根目录的 .env，把 key 变成环境变量。
from _load_env import load_env

load_env()

API_KEY = os.environ.get("DEEPSEEK_API_KEY") or ""
URL = "https://api.deepseek.com/chat/completions"


def main() -> None:
    if not API_KEY:
        print("还没有 key。打开这个文件，把 key 填到 API_KEY 那一行。")
        return

    body = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "我叫什么"}],
        "stream": True,      # ← 和 step1 相比，请求体只多了这一行
    }

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
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as error:
        print(f"服务器返回错误 {error.code}：")
        print(error.read().decode("utf-8", errors="ignore"))
        print("\n401 = key 不对  402 = 余额不足  400 = 请求写错了")
        return

    pieces = 0

    # ★ 变化一：step1 用 response.read() 一次性读完整个回复，
    #   现在改成 for 循环，服务器吐一行，我就读一行。
    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()

            # 服务器发来的每一行长这样：  data: {"choices":[...]}
            # 还有空行（分隔用）和 data: [DONE]（结束标记）
            if not line or not line.startswith("data:"):
                continue

            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break                    # 服务器说：没有更多了

            chunk = json.loads(payload)

            # 最后一帧只带用量统计，choices 是空的，跳过避免报错
            if not chunk.get("choices"):
                continue

            # ★ 变化二：step1 取的是 message.content（一整段答案），
            #   现在取的是 delta.content（一小片，可能只有几个字）
            piece = chunk["choices"][0]["delta"].get("content")
            if piece:
                pieces += 1
                # ★ 变化三：end="" 让 print 不换行；flush=True 让它立刻显示，
                #   不加这两个，你会等全部结束才「唰」一下看到全文，白瞎了流式。
                print(piece, end="", flush=True)

    print(f"\n\n[这段回答被切成了 {pieces} 片]")


if __name__ == "__main__":
    main()
