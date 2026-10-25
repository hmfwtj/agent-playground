"""摘要压缩：历史变长时，把最早的部分浓缩成几句话，而不是直接扔掉。

它解决的问题
------------
context.trim() 的做法是「超了就砍掉最早的」。砍掉的对话就真的没了 ——
用户说了三遍自己的名字，第 21 轮之后模型依然不认识他。

这个模块换个做法：砍之前，先让模型把那段旧对话读一遍、写成摘要，之后每次
请求都带着这份摘要。**摘要只有几十到两百字，比原文省一个数量级。**

为什么分成「纯函数」和「网络调用」两部分
----------------------------------------
这个文件里有五类函数，边界很清楚：

    _one_line / render      纯数据处理，不碰网络，绝不失败
    find_cut                纯计算，决定「切在第几条」，绝不失败
    merge_into_system       纯数据处理，不碰网络，绝不失败
    summarize               唯一会发请求的，会超时、会花钱、会失败

好处是测试的时候：前四个不用联网、不花钱、毫秒级跑完。只有 summarize 需要
假服务器。而且一旦出错，能立刻分清是「切错了」还是「网络挂了」。

和 step6 教学版的区别（三处，都是结构差异）
------------------------------------------
1. render 认得 tool_calls 和 tool 两种消息。step6 只认 user/assistant，
   会把工具结果当成「助手说的话」，还会输出一堆「助手：None」。
2. find_cut 保证切点落在 user 消息上。step6 的 del history[:cut] 在纯聊天
   里没事，但这里切在 assistant(tool_calls) 和 tool 之间会留下孤儿 tool
   消息，服务端直接 400。
3. 摘要合并进已有的 system 消息，不新插一条。全程只有一条 system，
   换任何 OpenAI 兼容的接口都不会被拒。
"""

import logging

from agent_playground.llm import LLMError, chat_once
from agent_playground.logging_setup import get_logger

logger = get_logger("summarizer")

# 压缩后至少保留多少条原文。太小则模型接不上当前话题，太大则很快又要压。
DEFAULT_KEEP_RECENT = 8

# 喂给摘要模型的文本上限（字符数）。超了从最早的开始丢 ——
# 压缩的目的是省 token，如果待压缩内容本身就超长，那这次压缩反而更贵。
MAX_RENDER_CHARS = 6000

# 摘要内容的上限。写进 prompt 里约束模型，省得它写一篇小作文。
SUMMARY_HINT = "不超过 200 字"

# 摘要被合并进 system 时用的引导语。告诉模型「这段不是新指令，是背景」。
SUMMARY_HEADER = "以下是本次对话更早部分的摘要，回答时请参考（不要复述它）："

SUMMARIZE_PROMPT = (
    "你在压缩一段对话的上下文，目的是让模型在有限的窗口里保留尽可能多的关键信息。\n"
    "把下面内容合并成%s的中文摘要。\n"
    "只保留事实：人名、偏好、数字、承诺、已得出的结论、正在做的事、工具查到并已确认的数据。\n"
    "丢掉：寒暄、客套、重复表达、工具调用的过程描述。\n"
    "不要写「用户说……」「助手回答……」这种转述腔，直接给结论。\n"
    "如果给了【已有摘要】，要把它和新内容合并成一份，不要简单拼接。\n\n"
)


def _one_line(text, limit=200):
    """把一段可能很长的文本压成一行，超长的截断。

    为什么要截断：一个工具结果可能是几百字的 JSON，三四条就把待压缩的
    额度吃光了。摘要只需要知道「查到了什么」，不需要逐字复核。
    """
    if not text:
        return ""
    flat = " ".join(str(text).split())  # 换行和连续空格全部压成单个空格
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def render(messages):
    """把消息列表转成一段纯文本，喂给摘要模型。

    这里必须认得四种角色。遗漏任何一种都会让摘要内容悄悄失真 ——
    不会报错，只是摘要变傻，而这种错最难发现。
    """
    lines = []
    for message in messages:
        role = message.get("role")

        if role == "user":
            lines.append("用户：" + _one_line(message.get("content")))

        elif role == "tool":
            # 工具结果：告诉摘要模型「这是查到的数据」，别当成助手说的话。
            lines.append("工具结果：" + _one_line(message.get("content")))

        elif role == "assistant":
            # assistant 的 content 可能是 None（它只要求调工具、没说话）。
            # 所以先判空再决定要不要写这一行，否则会输出「助手：」空行。
            text = _one_line(message.get("content"))
            if text:
                lines.append("助手：" + text)
            for call in (message.get("tool_calls") or []):
                function = call.get("function") or {}
                # 参数是 JSON 字符串，原样带上 —— 「调了哪个工具、传了什么」
                # 本身就是事实，比如「查的是杭州不是北京」。
                lines.append(
                    "助手调用工具：" + str(function.get("name")) + "(" + str(function.get("arguments")) + ")"
                )

        # system 不参与渲染：它是守则，不是对话内容，压进摘要毫无意义。

    return "\n".join(lines)


def find_cut(messages, keep_recent=DEFAULT_KEEP_RECENT):
    """算出应该切在第几条：messages[:cut] 压成摘要，messages[cut:] 原样保留。

    返回下标；不需要压缩时返回 None。

    三条硬规则，任何一条不满足就宁可不压缩：
        1. 切点必须是 user 消息 —— 保留段才能以 user 开头，结构合法
        2. 保留段不少于 keep_recent 条 —— 否则压完立刻就又超了，白花钱
        3. 切点不能是第 0 条 —— 那等于什么都没压
    """
    # system 不参与压缩，把它从计算范围内排除。
    start = 1 if messages and messages[0].get("role") == "system" else 0
    body = len(messages) - start

    # 不够长，不需要压缩（也包括 <= keep_recent 的情况）
    if body <= keep_recent:
        return None

    # 从「最靠后但仍能保留够 keep_recent 条」的位置往前找 user。
    # 往前找而不是往后找，是为了尽量多压一点 —— 每压一次都要花一次请求，
    # 压得太保守会导致每轮都触发一次压缩。
    limit = len(messages) - keep_recent
    for cut in range(limit, start, -1):
        if messages[cut].get("role") == "user":
            return cut

    # 一条 user 都没找到（比如整段都是工具往返）。这种情况不压缩，
    # 交给 context.trim() 兜底 —— 那个模块专门处理「结构不完美」的情况。
    return None


def merge_into_system(messages, summary):
    """把摘要并进 system 消息，返回新列表。不改动传进来的 messages。

    **注意：摘要放在 system 里，不新插一条 system。**
    有些 OpenAI 兼容接口只接受「开头一条 system」，插第二条会 400。
    合并进去则到处都能跑，代价只是 system 变长了几百字。
    """
    if not summary:
        return [dict(m) for m in messages]

    # 浅拷贝每条消息：调用方手里还留着完整历史，我们只读不改。
    # 不拷贝的话，下面改 content 会连带改掉原始历史里的 system 消息 ——
    # 下次再合并就会叠加两次摘要，越滚越长且不报错。
    result = [dict(m) for m in messages]

    block = SUMMARY_HEADER + "\n" + summary

    if result and result[0].get("role") == "system":
        head = dict(result[0])
        existing = (head.get("content") or "").rstrip()
        head["content"] = (existing + "\n\n" + block) if existing else block
        result[0] = head
        return result

    # 历史里没有 system（极少见，比如调用方直接传了 user 列表）：补一条。
    return [{"role": "system", "content": block}] + result


def summarize(messages, previous_summary, config):
    """调模型把 messages 压成摘要文本。会发网络请求，也可能失败。

    失败时抛 LLMError，由调用方决定怎么办 —— **本函数绝不吞异常**。
    因为「摘要失败」和「摘要成功但内容为空」都必须让上层知道，
    上层才知道这一轮到底该不该删原文。
    """
    if not messages:
        return previous_summary or ""

    parts = []
    if previous_summary:
        parts.append("【已有摘要】\n" + previous_summary)

    body = render(messages)
    if len(body) > MAX_RENDER_CHARS:
        # 从最早的开始丢，保留最近的 —— 近期内容跟当前话题更相关。
        body = "（更早的内容已省略）\n" + body[-MAX_RENDER_CHARS:]
        logger.debug("待压缩文本过长，已截断到 %d 字", MAX_RENDER_CHARS)
    parts.append("【需要合并的新对话】\n" + body)

    prompt = (SUMMARIZE_PROMPT % SUMMARY_HINT) + "\n\n".join(parts)

    logger.debug("开始压缩 %d 条消息（%d 字）", len(messages), len(body))

    # 不带 tools：这是纯文本任务，让模型有机会调工具只会节外生枝。
    result = chat_once([{"role": "user", "content": prompt}], config)
    text = (result["message"].get("content") or "").strip()

    # 空摘要必须当失败处理。如果返回空串还继续，上层会以为压缩成功、
    # 把原文删掉，结果信息真的丢了 —— 而且日志里一切正常。
    if not text:
        raise LLMError("摘要模型返回了空内容")

    logger.debug("压缩完成：%d 字 → %d 字", len(body), len(text))
    return text
