"""第 1 步：一次最小的大模型调用。零第三方依赖，只用 Python 自带的库。

跑法：
    python steps/step1_first_call.py

你现在只需要改一个地方：下面 API_KEY 那一行。
"""

import json
import os
import urllib.error
import urllib.request

# 读项目根目录的 .env 文件，把里面的 key 变成环境变量。
# 这一行必须在下面读取 API_KEY 之前执行。
from _load_env import ENV_FILE, load_env

load_env()

# key 现在只存在 .env 里，不进代码，也不进仓库。
API_KEY = os.environ.get("DEEPSEEK_API_KEY") or ""

URL = "https://api.deepseek.com/chat/completions"


def main() -> None:
    if not API_KEY:
        print("没找到 key。请打开这个文件：")
        print(f"    {ENV_FILE}")
        print("找到 DEEPSEEK_API_KEY= 开头的那一行，把等号后面换成你的 key。")
        return

    # ① 要发给服务器的数据。这里是个 Python 字典，等会儿会变成 JSON 文本
    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "用一句话解释什么是 HTTP 请求"}
        ],
    }

    # ② 把字典打包成一个 HTTP POST 请求
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode("utf-8"),   # 字典 -> JSON 字符串 -> 字节
        headers={
            "Authorization": f"Bearer {API_KEY}",  # 身份凭证，服务器靠它认出是你
            "Content-Type": "application/json",    # 声明：我发过去的是 JSON
        },
        method="POST",
    )

    # ③ 发出去，等服务器回话
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")   # 字节 -> 字符串
    except urllib.error.HTTPError as error:
        print(f"服务器返回错误 {error.code}：")
        print(error.read().decode("utf-8", errors="ignore"))
        print("\n401 = key 不对  402 = 余额不足  400 = 请求写错了")
        return
    piecs=0


    # ④ 把回来的字符串变回字典，再从固定位置把回答取出来
    data = json.loads(raw)
    print(data["choices"][0]["message"]["content"])


if __name__ == "__main__":
    main()
