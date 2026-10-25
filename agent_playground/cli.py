"""命令行入口：一个能连续对话、并且会自己调用工具的客户端。

跑法：
    cd E:/workshuju/2026-10-23-17-52-58/agent-playground
    python run.py                普通模式
    python run.py --verbose      连调试日志一起看

这一层只做两件事：把配置和数据接起来、把结果显示出来。
网络在 llm.py，循环在 agent.py，工具在 tools.py，配置在 config.py。
"""

import sys

from agent_playground.agent import run_agent
from agent_playground.config import Config
from agent_playground.llm import LLMError
from agent_playground.logging_setup import get_logger, setup
from agent_playground.tools import TOOLS

logger = get_logger("cli")

# 这句话会跟着每一次请求发出去，是模型的「工作守则」。
# 「优先调工具查证」这句很关键：不加的话，模型会凭记忆编天气数据。
SYSTEM_PROMPT = (
    "你是一个严谨的技术助手。回答简洁，不确定就明说。"
    "涉及天气、距离、时间这类事实，必须调用工具查证，不要凭记忆编造。"
)


def show_tool(name, args, result):
    """工具调用回调：让用户看见「它正在做什么」，而不是黑屏干等。"""
    print(f"   [工具] {name}({args})")
    print(f"          → {result}")


def show_tools():
    """打印可用工具清单。数据直接来自 TOOLS，所以永远和真实情况一致。"""
    print("可用工具（带 * 的是必填参数）：")
    for item in TOOLS:
        spec = item["function"]
        params = spec["parameters"]["properties"]
        required = spec["parameters"]["required"]
        parts = []
        for name in params:
            parts.append(name + ("*" if name in required else ""))
        arg_text = ", ".join(parts) or "无参数"
        print(f"  {spec['name']}({arg_text})")
        print(f"      {spec['description']}")
    print()


def format_usage(usage, config):
    """把 token 用量和估算花费拼成一行。"""
    prompt_tokens = usage.get("prompt_tokens")
    if not prompt_tokens:
        return ""
    completion = usage.get("completion_tokens") or 0
    cost = (prompt_tokens * config.price_in + completion * config.price_out) / 1_000_000
    if cost:
        return f"   [输入 {prompt_tokens} / 输出 {completion} / 约 ¥{cost:.4f}]"
    return f"   [输入 {prompt_tokens} / 输出 {completion}]"


def main():
    # --verbose 是启动开关。不用 argparse，因为现在只有一个参数，不值得引入新概念。
    verbose = "--verbose" in sys.argv
    setup(level="DEBUG" if verbose else "INFO")
    logger.debug("命令行参数：%s", sys.argv)

    config = Config()
    try:
        config.check()
    except RuntimeError as error:
        print(f"\n[配置错误] {error}\n")
        return 2

    print(f"模型 {config.model}   输入 /exit 退出，/clear 清空上下文，/tools 看工具，/summary 看摘要\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # ★ 摘要单独存，不放进 messages。
    #   放进去的话下次合并会在旧摘要上再叠一遍，越滚越长且不报错。
    #   它和 messages 一起构成了「这段对话的全部记忆」：
    #       messages = 最近的原文（有界）
    #       summary  = 更早内容的浓缩（也有界）
    summary = ""

    while True:
        try:
            user_text = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not user_text:
            continue
        if user_text in ("/exit", "/quit"):
            print("再见。")
            break
        if user_text == "/clear":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            summary = ""
            print("上下文已清空\n")
            continue
        if user_text == "/tools":
            show_tools()
            continue
        if user_text == "/summary":
            if summary:
                print(f"\n  当前摘要（{len(summary)} 字）：")
                print("  " + summary.replace("\n", "\n  "))
                print(f"  原文 {len(messages)} 条\n")
            else:
                print(f"  还没压缩过。当前原文 {len(messages)} 条，"
                      f"超过 {config.max_context_messages} 条才会触发压缩。\n")
            continue

        messages.append({"role": "user", "content": user_text})

        try:
            # agent 会自己决定要不要调工具、调几次。cli 只负责等结果。
            # summary 要传进去（上一轮的摘要），出来的新摘要要接住。
            result = run_agent(messages, config, on_tool=show_tool, summary=summary)
        except LLMError as error:
            # 失败了要把刚加进去的那句问题撤掉，
            # 否则下一轮模型会看到一段「有问无答」的残缺对话。
            messages.pop()
            print(f"[调用失败] {error}\n")
            continue

        summary = result.get("summary", "")

        # 压缩发生在请求之前，但结果要等请求回来才知道。
        # 所以这行显示在回答前面，而不是压缩真正发生的位置。
        if result.get("summarized"):
            print(f"   [摘要] 早期对话已压缩成 {len(summary)} 字，原文 {len(messages)} 条")

        if result["stop_reason"] == "max_steps":
            print(f"[已达到 {result['steps']} 轮上限，强制停止]")
            print("   可能是工具一直报错，也可能是模型陷进了循环。加 --verbose 能看到每轮细节。")
        else:
            print(f"AI > {result['text']}")

        print(format_usage(result["usage"], config))
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
