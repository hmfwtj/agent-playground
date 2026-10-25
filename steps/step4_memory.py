"""
step4：让程序记住上一句话（多轮对话）

用法：
    cd E:/workshuju/2026-10-23-17-52-58/agent-playground
    python steps/step4_memory.py

进来之后随便聊。想验证它真的记住了，按顺序输入这两句：
    你 > 我叫小明，记住我的名字
    你 > 我叫什么？

输入 退出 结束。按 Ctrl+C 也行。

这一份只改一件事：把 messages 从「一条」变成「一个越变越长的列表」。
"""

import json
import os
import urllib.error
import urllib.request

# 读项目根目录的 .env，把 key 变成环境变量。
from _load_env import ENV_FILE, load_env

load_env()

# ── 配置区 ────────────────────────────────────────────────
API_KEY = os.environ.get("DEEPSEEK_API_KEY") or ""
URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
TIMEOUT = 60


# ── 核心函数：发一整段对话，边收边打印，返回完整回答 ──────────
# 参数多了 url 和 api_key 两个「可选参数」，默认用上面的配置。
# 这样是为了让测试能把它指向本地假服务器（这个写法叫依赖注入）。
# debug 也是可选参数：谁想要看请求体，谁就传 debug=True，默认不打。
# quiet=True 表示「别往屏幕上打回答」，只把结果返回给我。
#   摘要那种内部调用就要用 quiet，否则摘要过程会混进聊天记录里。
def stream_reply(messages, url=URL, api_key=API_KEY, debug=False, quiet=False):
    body = {
        "model": MODEL,
        # ★ 这一行和 step1 完全一样，但传进去的东西不一样了。
        #   step1 传的是只有一条消息的列表，这里传的是一整段历史。
        "messages": messages,
        "stream": True,
        # 让服务器在最后一个片段里带上 token 用量统计。
        # 不加这一句，流式模式下拿不到 usage。
        "stream_options": {"include_usage": True},
    }

    payload = json.dumps(body).encode("utf-8")

    # ★ 把「真正发出去的那串东西」原样打出来。
    #   排查上下文/记忆类问题，靠的就是它，不是靠看模型的回答。
    #   打印版用了 ensure_ascii=False，所以中文是中文；
    #   实际发出去的 payload 里中文被转成了 \u 编码，两者内容等价。
    if debug:
        print("\n┌─ 实际发出去的请求体（回答在这段之后）──────────")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        print(f"└─ messages 共 {len(messages)} 条 · {len(payload)} 字节 ─────")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        response = urllib.request.urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")[:200]
        raise RuntimeError(f"HTTP {error.code}：{detail}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"网络错误（{getattr(error, 'reason', error)}）") from error

    pieces = []   # 把收到的碎片攒起来，最后拼成完整回答
    usage = None  # token 用量，服务器给就存下来

    # with 的用处：不管中间出什么错，退出时都会把连接关掉。
    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line.startswith("data:"):
                continue
            data_text = line[len("data:"):].strip()
            if data_text == "[DONE]":
                break
            try:
                chunk = json.loads(data_text)
            except json.JSONDecodeError:
                continue
            # 有些帧只带 usage、没有 choices，先把它捞出来
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                piece = choice.get("delta", {}).get("content") or ""
                if piece:
                    if not quiet:
                        print(piece, end="", flush=True)
                    pieces.append(piece)

    if not quiet:
        print()  # 回答结束，换一行
    return "".join(pieces), usage


# ── 主循环：一个不断变长的对话列表 ──────────────────────────
def main():
    if not API_KEY:
        print("没找到 key。请打开这个文件：")
        print(f"    {ENV_FILE}")
        print("找到 DEEPSEEK_API_KEY= 开头的那一行，把等号后面换成你的 key。")
        return

    # ★★ 整份代码的重点就是这一行 ★★
    # 空列表 = 一张白纸。模型自己不记得任何东西，全靠你把这个列表喂给它。
    messages = []

    print("开始聊天。输入 退出 结束。")
    print("我叫小明，记住我的名字")

    turn = 0
    # while True 就是「一直循环，除非里面主动 break」。
    while True:
        try:
            user_text = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            # Ctrl+C 会抛 KeyboardInterrupt，按窗口关掉会抛 EOFError
            print("\n再见。")
            break

        if not user_text:
            # 直接回车，什么都不做，重新问一遍
            continue
        if user_text.lower() in ("退出", "exit", "quit"):
            break

        # ① 先把你说的话塞进历史
        messages.append({"role": "user", "content": user_text})

        # ② 把整段历史发出去
        turn += 1
        print("AI > ", end="", flush=True)
        try:
            reply, usage = stream_reply(messages)
        except RuntimeError as error:
            print(f"\n出错了：{error}")
            # ★ 失败了要把刚才那句撤掉。
            #   否则历史里会留一条「用户问了但从没被回答」的消息，
            #   下一次再发，模型看到的是一段缺了一环的对话。
            messages.pop()
            turn -= 1
            continue

        # ③ 把 AI 的回答也塞进历史
        messages.append({"role": "assistant", "content": reply})

        # ④ 打印现场情况，让你看见「上下文在变长」
        stat = f"  [第 {turn} 轮 · 上下文 {len(messages)} 条消息"
        if usage:
            stat += f" · 本次输入 {usage.get('prompt_tokens')} tokens"
            stat += f" · 输出 {usage.get('completion_tokens')} tokens"
        stat += "]"
        print(stat + "\n")


if __name__ == "__main__":
    main()
