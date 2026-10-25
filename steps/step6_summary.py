"""step6：历史太长怎么办（下）—— 让模型自己把旧对话压成摘要。

跑法：
    python steps/step6_summary.py

新概念只有一个：摘要压缩（summary compression）。

step5 的做法是「直接扔掉最早的几条」，问题是扔了就真没了。
这一步换个做法：被扔之前，先让模型读一遍、浓缩成三句话存起来，
之后每次请求都把这句摘要放在最前面。

可以这样验证：
    你 > 我叫小明，在做 agent 开发的学习
    你 > 我每周能投入十二小时
    你 > 我在准备明年三月的暑期实习
    你 > 随便聊点别的
    你 > 摘要            ← 看它把前面压成了什么
    你 > 我叫什么？我每周能投入多少小时？

中途输入「窗口」可以看当前真正发出去的 messages。
"""

import os

from _load_env import ENV_FILE, load_env

load_env()

# 除了发请求的函数，还把 step4 里的地址和 key 也借过来用。
# 这样参数默认值就是「正式环境」，测试时再传假地址进去。
from step4_memory import API_KEY, URL, stream_reply

MAX_MESSAGES = 6     # 原文超过 6 条就触发压缩
KEEP_RECENT = 2      # 压缩后，最近 2 条原文原样保留（对话要接得上）


# ── 把消息列表转成好读的文本，喂给摘要模型 ──────────────────
def render(messages):
    lines = []
    for message in messages:
        who = "用户" if message["role"] == "user" else "助手"
        lines.append(f"{who}：{message['content']}")
    return "\n".join(lines)


# ── 压缩：旧摘要 + 即将被删的消息 → 一份新摘要 ────────────────
# url / api_key 又有默认值又是参数，理由和之前一样：
# 正式跑用默认值，测试时把假服务器地址传进来。
def make_summary(old_messages, old_summary="", url=URL, api_key=API_KEY):
    parts = []
    if old_summary:
        parts.append(f"【已有摘要】\n{old_summary}")
    parts.append(f"【需要合并的新对话】\n{render(old_messages)}")

    prompt = (
        "你在压缩一段对话的上下文。把下面的内容合并成不超过三句话的中文摘要。\n"
        "只保留事实：人名、偏好、数字、承诺、已得出的结论、正在做的事。\n"
        "不要寒暄，不要评论，不要写「用户说……」这种转述腔，直接给结论。\n\n"
        + "\n\n".join(parts)
    )

    # ★ quiet=True：别把摘要过程打到屏幕上，它只是内部的一次调用。
    text, _ = stream_reply(
        [{"role": "user", "content": prompt}],
        url=url,
        api_key=api_key,
        quiet=True,
    )
    return text.strip()


# ── 拼出真正要发出去的消息：摘要放在最前面 ────────────────────
def build_payload(summary, history):
    if not summary:
        return list(history)
    # system 角色是「给模型的背景设定」，不占对话轮次，但模型一定会读。
    head = {
        "role": "system",
        "content": "以下是本次对话更早部分的摘要，回答时请参考：\n" + summary,
    }
    return [head] + list(history)


def main():
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("没找到 key。请打开这个文件：")
        print(f"    {ENV_FILE}")
        print("找到 DEEPSEEK_API_KEY= 开头的那一行，把等号后面换成你的 key。")
        return

    history = []    # 还没被压缩的原文。它现在是有界的了
    summary = ""    # 已经被压缩掉的旧内容

    print(f"开始聊天。原文超过 {MAX_MESSAGES} 条时，最早的几条会被压成摘要。")
    print("输入「摘要」看当前的摘要；输入「窗口」看真正发出去的消息。\n")

    turn = 0
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

        if user_text in ("摘要", "summary"):
            if summary:
                print(f"\n  ┌─ 当前摘要 ─────────────────────")
                print("  " + summary.replace("\n", "\n  "))
                print(f"  └─ 共 {len(summary)} 字 ─────────────────\n")
            else:
                print("  还没压缩过，摘要为空。\n")
            continue

        if user_text in ("窗口", "看看"):
            payload = build_payload(summary, history)
            print(f"\n  历史原文 {len(history)} 条 + 摘要 {len(summary)} 字 → 实发 {len(payload)} 条：")
            for i, message in enumerate(payload, 1):
                text = message["content"].replace("\n", " ")[:30]
                print(f"    {i}. {message['role']:<9} {text}")
            print()
            continue

        # ① 先入档
        history.append({"role": "user", "content": user_text})

        # ② 原文超标了？先压缩，再发。
        if len(history) > MAX_MESSAGES:
            cut = len(history) - KEEP_RECENT
            old = history[:cut]
            print(f"  ⚙ 原文 {len(history)} 条超过上限，正在把最早 {cut} 条压成摘要……", flush=True)
            try:
                summary = make_summary(old, summary)
            except RuntimeError as error:
                # ★ 压缩失败就什么都别删。宁可这一轮多发点，也不能让信息凭空消失。
                print(f"  ⚙ 压缩失败（{error}），本轮按原样发送，历史保留。")
            else:
                del history[:cut]      # 删的是列表开头的 cut 条
                print(f"  ⚙ 压缩完成：摘要 {len(summary)} 字 + 原文 {len(history)} 条\n")

        # ③ 拼包发送
        payload = build_payload(summary, history)
        turn += 1
        print("AI > ", end="", flush=True)
        try:
            reply, usage = stream_reply(payload)
        except RuntimeError as error:
            print(f"\n出错了：{error}")
            history.pop()
            turn -= 1
            continue

        # ④ 回答回到原文里（它会被下一次压缩吸收掉）
        history.append({"role": "assistant", "content": reply})

        stat = f"  [第 {turn} 轮 · 原文 {len(history)} 条 · 摘要 {len(summary)} 字"
        if usage:
            stat += f" · 输入 {usage.get('prompt_tokens')} tokens"
        stat += "]"
        print(stat + "\n")


if __name__ == "__main__":
    main()
