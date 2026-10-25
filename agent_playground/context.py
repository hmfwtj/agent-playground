"""上下文裁剪：对话变长时，只把最近的一部分发给模型。

为什么必须有它：
    模型没有记忆，每一轮都要把**整段历史**重新发过去。历史只涨不跌，
    所以迟早会撞上模型上限（DeepSeek 单次约 64K tokens），然后整轮请求直接报错。
    这不是「聊久了变慢」这种小毛病，是「到某一轮突然彻底不能用」。

为什么不能简单地「留最后 N 条」：
    一次工具调用会在历史里留下两条消息，它们是**成对的**：

        {"role": "assistant", "tool_calls": [...]}   ← 模型说「我要调工具」
        {"role": "tool", "tool_call_id": "call_1"}   ← 工具的结果

    如果切的时候正好把它们切开，只留下 tool 那条，服务端会直接拒绝：
        messages 里出现了一条没有前因的 tool 消息
    所以裁剪之后，第一条必须是 user 消息 —— 这样任何被保留下来的
    assistant(tool_calls) 都必然带着它后面的 tool 结果，结构始终完整。

这个模块只做「按条数砍」，不做摘要。旧内容是直接丢掉的，不可恢复。
"""

DEFAULT_MAX_MESSAGES = 20


def trim(messages, max_messages=DEFAULT_MAX_MESSAGES):
    """返回一个「可以安全发出去」的新列表，不改动传进来的 messages。

    副作用为零很重要：调用方手里还留着完整历史，之后想加摘要、想导出、
    想做「对话回放」都还有素材。**先删数据再想用它，是最常见的自找麻烦。**
    """
    # 用 list(...) 复制一份。返回同一个对象会造成「别名」问题：
    # 上层再往原列表 append，这里拿到的窗口会跟着变长，数字悄悄错掉，而且不报错。
    if len(messages) <= max_messages or max_messages <= 0:
        return list(messages)

    # system 是「工作守则」，任何情况下都不能丢。
    # 丢了它，模型就不再遵守你的角色设定和工具使用规则 —— 而且不会有任何报错。
    if messages and messages[0].get("role") == "system":
        system = [messages[0]]
        rest = messages[1:]
    else:
        system = []
        rest = messages

    # system 也占名额，剩下的余额才给对话。
    # 余额算出 0 或负数时（说明上限设得比 system 还小），强制给 1 条，
    # 否则会返回「只有 system」的列表 —— 那等于问了个不存在的问题。
    budget = max_messages - len(system)
    if budget < 1:
        budget = 1

    window = rest[-budget:]

    # 往右挪到最近的 user 消息，保证：
    #   1. 第一条不是孤零零的 tool 结果（结构非法，服务端会 400）
    #   2. 不会从一句 assistant 发言开始（模型容易接不上）
    while window and window[0].get("role") != "user":
        window = window[1:]

    # 边界：余额小到连最后一轮都装不下时，上面会把 window 削成空列表。
    # 那样发出去的就是「只有 system，没有 user」的请求，等于问了个不存在的问题。
    # 所以这里兜底：宁可多带一条，也不能发一份没有 user 消息的请求。
    if not window:
        for index in range(len(rest) - 1, -1, -1):
            if rest[index].get("role") == "user":
                window = rest[index:]
                break

    return system + window
