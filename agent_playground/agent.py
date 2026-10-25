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
from agent_playground.llm import LLMError, chat_once
from agent_playground.logging_setup import get_logger
from agent_playground.summarizer import find_cut, merge_into_system, summarize
from agent_playground.tools import TOOLS, run_tool

logger = get_logger("agent")

# ★ 保险丝：最多转几圈。
#   没有这个数字，一旦某个工具总是报错、或者模型陷进死循环，
#   程序会一直请求下去，一直扣钱，直到余额见底。
#   所有实现循环的地方都必须有上限，这是上线前的必检项。
MAX_STEPS = 5


def run_agent(messages, config, on_tool=None, summary=""):
    """跑完一整轮 agent 流程，返回最终回答。

    参数：
        messages  对话历史（会被**原地修改**：工具调用和结果都追加进去，
                  超长时最早的一段会被压成摘要后删掉）
        config    配置对象
        on_tool   回调函数，签名 on_tool(工具名, 参数, 结果)。
                  传进来是为了让 cli 能把「正在调什么工具」显示给用户。
                  这一层不关心怎么显示 —— 依赖注入。
        summary   上一轮累积下来的摘要文本。第一次调用传空字符串。

    返回 dict：
        {"text": 最终回答, "usage": 累计用量, "steps": 转了几圈, "stop_reason": 结束原因,
         "summary": 压缩后的摘要, "summarized": 这一轮有没有发生压缩}

    ★ 为什么要多出一个 summary 参数，而不是把它塞进 messages：
        它如果写进 messages[0] 的 system，下一次合并就会在已有摘要上再叠一遍，
        system 越滚越长，而且不报错、只表现为「越聊越贵」。
        让它在 messages 之外单独流转，messages 里那条 system 永远是原始守则。
    """
    # 一次 agent 可能发好几次请求，用量要累加，否则界面上显示的花费是错的
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    summary = summary or ""
    summarized = False

    # ★ 摘要压缩放在循环开始之前，只在整轮对话的第一圈做。
    #   为什么不在每一圈做：一轮 agent 内部会发生「模型要工具 → 塞结果 → 再问」
    #   的往返，这时候 messages 正处在半截状态（assistant 已入档、tool 结果刚塞进去），
    #   在这中间切一刀很容易把配对切坏，而且压的是几秒钟前才产生的内容，纯浪费。
    messages, summary, summarized = _compress(messages, config, summary)

    for step in range(1, MAX_STEPS + 1):
        # ★ 每一轮请求前都裁剪一次，这是最后一道保险。
        #   正常情况下 _compress 已经把历史压到阈值以下了，走到这里不会触发；
        #   但如果摘要被关掉了、或者压缩失败，就靠它兜底。
        window = trim(messages, config.max_context_messages)
        if len(window) < len(messages):
            logger.info("上下文已满：历史 %d 条，本次只发最近 %d 条",
                        len(messages), len(window))

        # ★ 摘要合并成 system 的一部分，而不是新插一条 system。
        #   有些兼容接口只认「开头一条 system」，插第二条会直接 400。
        window = merge_into_system(window, summary)

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
                "summary": summary,
                "summarized": summarized,
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
    return {
        "text": "",
        "usage": usage_total,
        "steps": MAX_STEPS,
        "stop_reason": "max_steps",
        "summary": summary,
        "summarized": summarized,
    }


def _compress(messages, config, summary):
    """历史超长时，把最早的一段压成摘要再删掉。返回 (messages, summary, 是否压缩了)。

    失败一律不抛异常 —— 宁可这一轮多发点 token，也不能因为摘要挂了就整轮不能用。
    压缩失败时什么都不删，退化成 context.trim() 的老行为，只是会丢旧内容。

    ★ 这里和「直接裁剪」的关键区别：裁剪是先把数据丢了再想补救，压缩是先
      把内容存下来再丢。顺序反了就没有补救的余地。
    """
    keep = config.summary_keep_recent
    # keep <= 0 表示用户主动关掉了摘要
    if keep <= 0 or len(messages) <= config.max_context_messages:
        return messages, summary, False

    cut = find_cut(messages, keep)
    if not cut:
        # 找不到合法切点（比如整段都是工具往返，一条 user 都没有）。
        # 不硬切，交给 trim 兜底。
        logger.debug("历史 %d 条但没有合法切点，跳过摘要", len(messages))
        return messages, summary, False

    try:
        new_summary = summarize(messages[:cut], summary, config)
    except LLMError as error:
        logger.warning("摘要压缩失败（%s），本轮保留完整历史", error)
        return messages, summary, False

    # ★ 只删「被压缩的那一段」，system 必须留住。
    #   cut 是相对整个列表的下标（system 占第 0 位），所以起点要跳过 system。
    #   写成 del messages[:cut] 会把守则一起删掉 —— 模型从此不再遵守工具使用规则，
    #   而且不会有任何报错。
    start = 1 if messages and messages[0].get("role") == "system" else 0
    del messages[start:cut]

    logger.info("已把最早 %d 条对话压成摘要（%d 字），当前历史 %d 条",
                cut - start, len(new_summary), len(messages))
    return messages, new_summary, True


def _add_usage(total, usage):
    """把本次用量加进累计值。内部辅助函数，外面不用管它。"""
    for key in ("prompt_tokens", "completion_tokens"):
        total[key] = total.get(key, 0) + (usage.get(key) or 0)
