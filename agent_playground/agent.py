"""agent 循环：给它一段对话，它自己决定要调几次工具。

这是整个项目的心脏，只有四十行。LangGraph、OpenAI Agents SDK、AutoGen，
底下都是这同一个循环，区别只是它们在外面又包了状态管理、可视化、持久化。

流程：
    发请求（带上工具说明书）
        ├─ 模型说「我要调工具」 → 执行 → 把结果塞回对话 → 再来一轮
        └─ 模型说「我说完了」   → 这就是答案，结束

注意出口：**由模型决定，不由你决定**。
你唯一负责的是「它说还要调，你就继续帮它调」，直到它不再要工具。

这个文件不 print、不读键盘。它只负责循环，显示交给 cli.py。
"""

import json

from agent_playground.context import trim
from agent_playground.llm import chat_once
from agent_playground.logging_setup import get_logger
from agent_playground.tools import TOOLS, run_tool

logger = get_logger("agent")

# ★ 保险丝：最多转几圈。
#   没有这个数字，一旦某个工具总是报错、或者模型陷进死循环，
#   程序会一直请求下去，一直扣钱，直到余额见底。
#   所有实现循环的地方都必须有上限，这是上线前的必检项。
MAX_STEPS = 5


def run_agent(messages, config, on_tool=None):
    """跑完一整轮 agent 流程，返回最终回答。

    参数：
        messages  对话历史（会被**原地修改**：工具调用和结果都追加进去）
        config    配置对象
        on_tool   回调函数，签名 on_tool(工具名, 参数, 结果)。
                  传进来是为了让 cli 能把「正在调什么工具」显示给用户。
                  这一层不关心怎么显示 —— 依赖注入。

    返回 dict：
        {"text": 最终回答, "usage": 累计用量, "steps": 转了几圈, "stop_reason": 结束原因}
    """
    # 一次 agent 可能发好几次请求，用量要累加，否则界面上显示的花费是错的
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    for step in range(1, MAX_STEPS + 1):
        # ★ 每一轮请求前都裁剪一次。
        #   注意发出去的是 window（副本），往 messages（完整历史）里追加。
        #   完整历史一条都不删 —— 以后想加摘要，素材还在。
        window = trim(messages, config.max_context_messages)
        if len(window) < len(messages):
            logger.info("上下文已满：历史 %d 条，本次只发最近 %d 条",
                        len(messages), len(window))

        logger.debug("第 %d 轮请求（发送 %d 条消息）", step, len(window))

        # 用 chat_once（非流式），不用 stream_chat：工具调用必须拿到完整的
        # tool_calls 结构，流式下它是分片的，本阶段不碰这个复杂度。
        result = chat_once(window, config, tools=TOOLS)
        _add_usage(usage_total, result["usage"])

        message = result["message"]
        finish = result["finish_reason"]

        # ★ 这条 assistant 消息必须先入档，再执行工具。
        #   漏掉它，下一轮模型看到的是「用户问了一句，突然来一条工具结果」，
        #   它会问：我什么时候要求调过工具？也就是说，模型得看见自己的意图，
        #   才能把结果和意图对上号。
        messages.append(message)

        if finish != "tool_calls":
            # 唯一的正常出口：它不要工具了，说明它能回答了
            return {
                "text": message.get("content") or "",
                "usage": usage_total,
                "steps": step,
                "stop_reason": "done",
            }

        for call in message.get("tool_calls") or []:
            name, args, payload = run_tool(call)
            logger.debug("工具 %s(%s) → %s", name, args, payload)
            if on_tool:
                on_tool(name, args, payload)

            # role 必须是 "tool"，tool_call_id 必须和模型给的 id 一致。
            # 一次回答可能要调 3 个工具，服务器靠这个 id 分辨哪条结果属于哪次调用。
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                # content 必须是**字符串**。反方向的操作：字典 → 文本，用 dumps。
                "content": json.dumps(payload, ensure_ascii=False),
            })

    # 转满上限还没结束。不要假装成功，如实告诉上层。
    logger.warning("已经转了 %d 轮还没结束，强制停下", MAX_STEPS)
    return {"text": "", "usage": usage_total, "steps": MAX_STEPS, "stop_reason": "max_steps"}


def _add_usage(total, usage):
    """把本次用量加进累计值。内部辅助函数，外面不用管它。"""
    for key in ("prompt_tokens", "completion_tokens"):
        total[key] = total.get(key, 0) + (usage.get(key) or 0)
