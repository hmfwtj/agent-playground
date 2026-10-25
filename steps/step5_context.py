"""step5：上下文太长怎么办 —— 只把最近几轮发给模型。

跑法：
    python steps/step5_context.py

新概念只有一个：滑动窗口（sliding window）。
历史仍然全部留在内存里，但每次真正发给模型的，只有最近 MAX_ROUNDS 轮。

建议按顺序做这个实验，一句都别少：
    你 > 我叫小明
    你 > 今天天气不错
    你 > 我们聊点别的
    你 > 窗口          ← 打印此刻真正发出去的那几条消息
    你 > 我叫什么？

第 4 句是重点：你会**确定地**看到「我叫小明」已经从窗口里消失了。
这是这一步唯一需要你确认的事实，100% 可以复现。

至于第 5 句它到底答不答得出来，不一定。它很可能照样答对，
因为 AI 自己的回答里也提过「小明」——信息从别的缝里漏进来了。
所以记住这条结论：**判断上下文有没有生效，看请求体，不要看回答。**
"""

import os

# ★ 先读 .env，而且必须写在 import step4_memory 之前。
#   因为那个模块在被 import 的那一瞬间就会执行 「API_KEY = os.environ.get(...)」，
#   如果那时 .env 还没读，拿到的是空字符串，后面怎么改都没用。
from _load_env import ENV_FILE, load_env

load_env()

# ★ 直接复用 step4 的请求函数，一个字符都没改。
#   跑 `python steps/step5_context.py` 时，Python 会自动把脚本所在的
#   steps/ 目录放进模块搜索路径，所以能直接 import 同目录下的 step4_memory。
from step4_memory import stream_reply

MAX_ROUNDS = 2     # 只把最近 2 轮发给模型（1 轮 = 你一句 + AI 一句）


def trim(history, max_rounds=MAX_ROUNDS):
    """从完整历史里切出最近几轮。永远返回一个新列表，绝不返回原对象。"""
    max_messages = max_rounds * 2

    # 这一行在两种情况都对：
    #   history 少于 6 条时，history[-6:] 就是整段，而且是它的**副本**
    #   history 超过 6 条时，只取最后 6 条
    #
    # ★ 千万别为了「省事」写成：
    #       if len(history) <= max_messages: return history
    #   那样返回的是同一个列表对象（叫「别名」）。调用方之后往 history 里 append，
    #   你手里的 window 会跟着一起变长 —— 这类 bug 不报错，只是数字悄悄不对，
    #   是新手最难查的一类。
    return history[-max_messages:]


def main():
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("没找到 key。请打开这个文件：")
        print(f"    {ENV_FILE}")
        print("找到 DEEPSEEK_API_KEY= 开头的那一行，把等号后面换成你的 key。")
        return

    # 完整历史，一条都不删。删了就再也拿不回来了，后面想做摘要都没素材。
    history = []

    print(f"开始聊天。最多只发最近 {MAX_ROUNDS} 轮给模型。输入 退出 结束。")
    print("输入「窗口」看当前真正发出去的那几条消息；输入「请求」开关请求体打印。")
    print("试试：我叫小明 → 聊两句别的 → 窗口 → 我叫什么？\n")

    turn = 0
    show_body = False   # 输入「请求」可以打开/关闭请求体打印
    while True:
        try:
            user_text = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not user_text:
            continue
        if user_text.lower() in ("退出", "exit", "quit"):
            break

        # ★ 开关：输入「请求」切换「要不要把请求体打出来」。
        #   注意 body 不在这个文件里，它在 step4_memory.py 的 stream_reply 里，
        #   这里只是把 debug=True 传下去，让它替我们打。
        if user_text in ("请求", "body"):
            show_body = not show_body
            state = "开启" if show_body else "关闭"
            print(f"  请求体打印已{state}，下一条消息生效。\n")
            continue

        # ★ 命令：把「此刻真正会发出去的内容」列成清单打出来。
        #   这是排查上下文类问题最有效的手段——别猜，直接看里面有什么。
        if user_text in ("窗口", "看看"):
            window = trim(history)
            dropped = len(history) - len(window)
            head = f"  历史共 {len(history)} 条，真正发出去的只有 {len(window)} 条"
            if dropped:
                head += f"（最早 {dropped} 条已被挤掉）"
            print(head + "：")
            for i, message in enumerate(window, 1):
                text = message["content"].replace("\n", " ")[:24]
                print(f"    {i}. {message['role']:<9} {text}")
            print()
            continue

        # ① 你说的话先记进完整历史
        history.append({"role": "user", "content": user_text})

        # ② ★ 这一步的分水岭：发出去的不是 history，而是裁过的 window
        window = trim(history)
        dropped = len(history) - len(window)

        turn += 1
        print("AI > ", end="", flush=True)
        try:
            reply, usage = stream_reply(window, debug=show_body)
        except RuntimeError as error:
            print(f"\n出错了：{error}")
            history.pop()      # 撤回这条没被回答的问题
            turn -= 1
            continue

        # ③ AI 的回答也记进完整历史（不是 window）
        history.append({"role": "assistant", "content": reply})

        # ④ 把两个数字并排打出来：历史一直在涨，发送数封顶
        #    「本次发送」比「历史」少，是因为发的时候 AI 的回答还没生成。
        stat = f"  [第 {turn} 轮 · 本次发送 {len(window)} 条 · 历史累计 {len(history)} 条"
        if dropped:
            stat += f" · 挤掉最早 {dropped} 条"
        if usage:
            stat += f" · 输入 {usage.get('prompt_tokens')} tokens"
        stat += "]"
        print(stat + "\n")


if __name__ == "__main__":
    main()
