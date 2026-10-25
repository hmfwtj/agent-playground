"""第 3 步：加超时和重试。

跑法：
    python steps/step3_retry.py

在 step2 的基础上只做一件事：请求失败时，判断「值不值得再试一次」，值得就等一会儿再试。
"""

import json
import os
import time
import urllib.error
import urllib.request

# 读项目根目录的 .env，把 key 变成环境变量。
from _load_env import load_env

load_env()

API_KEY = os.environ.get("DEEPSEEK_API_KEY") or ""
URL = "https://api.deepseek.com/chat/completions"

MAX_RETRIES = 2      # 最多再试 2 次，也就是总共最多 3 次
TIMEOUT = 60         # 秒。任何联网调用都必须有超时，否则可能永远卡住

# ★ 核心知识点：只有这些状态码值得重试。
#   4xx 是你自己的请求写错了（key 错、参数错、余额不足），重试一万次结果一样，只是白烧配额。
#   5xx 和 429 是「服务器现在忙」，过一会儿可能就好了。
RETRYABLE_CODES = {408, 409, 429, 500, 502, 503, 504}


def send_with_retry(body: dict, url: str | None = None, api_key: str | None = None):
    """发请求，失败时按需重试。成功返回响应对象，彻底失败抛 RuntimeError。

    后两个参数平时不用传，默认用文件开头的 URL / API_KEY。
    留成参数是为了让测试脚本能指向本地假服务器——这叫「依赖注入」，
    意思是别把地址写死在函数里，让调用方决定。
    """
    target = url or URL
    key = api_key or API_KEY

    payload = json.dumps(body).encode("utf-8")
    attempts = MAX_RETRIES + 1
    last_reason = "未知错误"

    for attempt in range(attempts):
        # 每次重试都要重新构造 request，因为请求体是一次性的，发过就消耗掉了
        request = urllib.request.Request(
            target,
            data=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            return urllib.request.urlopen(request, timeout=TIMEOUT)

        # ★ 这两个 except 的顺序不能反。
        #   HTTPError 是 URLError 的子类，Python 从上往下匹配，
        #   如果把 URLError 写在前面，HTTPError 永远轮不到它，状态码就丢了。
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="ignore")[:200]
            if error.code not in RETRYABLE_CODES:
                # 不可重试：直接放弃，不要浪费时间
                raise RuntimeError(
                    f"HTTP {error.code}，重试也没用：{detail}（请求发往 {target}）"
                ) from error
            last_reason = f"HTTP {error.code}"

        except (urllib.error.URLError, TimeoutError) as error:
            # 网络层问题：断网、DNS 失败、超时、连不上。这类通常值得重试
            last_reason = f"网络错误（{getattr(error, 'reason', error)}）发往 {target}"

        # 已经是最后一次了，等也没意义
        if attempt == attempts - 1:
            break

        # ★ 指数退避：等 0.5 秒、1 秒、2 秒……每次都翻倍。
        #   为什么不用固定的 1 秒？因为如果服务器刚被打挂，一万个客户端
        #   同时每秒重试一次，它永远缓不过来。翻倍等待能把请求摊开。
        wait = 0.5 * (2 ** attempt)
        print(f"  [第 {attempt + 1} 次失败 · {last_reason} · {wait:.1f} 秒后重试]")
        time.sleep(wait)

    raise RuntimeError(f"重试 {0} 次仍然失败：{last_reason}")


def print_stream(response) -> None:
    """把流式响应打印出来。和 step2 一样，只是搬进了函数里。"""
    pieces = 0
    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line or not line.startswith("data:"):
                continue

            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break

            chunk = json.loads(payload)
            if not chunk.get("choices"):
                continue

            piece = chunk["choices"][0]["delta"].get("content")
            if piece:
                pieces += 1
                print(piece, end="", flush=True)

    print(f"\n\n[切成 {pieces} 片]")


def main() -> None:
    if not API_KEY:
        print("还没有 key。打开这个文件，把 key 填到 API_KEY 那一行。")
        return

    body = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "用三句话解释什么是重试机制"}],
        "stream": True,
    }

    try:
        response = send_with_retry(body)
    except RuntimeError as error:
        print(f"\n调用失败：{error}")
        return

    print_stream(response)


if __name__ == "__main__":
    main()
