"""和大模型通信：发请求、解析流、失败重试。

这一层只做一件事——把 messages 变成回答。它不知道什么是 agent，也不打印任何东西。
（打印是 cli 的事。分层的好处：以后换成网页界面，这一层一个字都不用改。）

为什么有两个函数：
    stream_chat  流式，边想边说。适合纯聊天，用户不用干等。
    chat_once    一次性返回。工具调用必须用它，原因见下面那个注释。
"""

import json
import logging
import time
import urllib.error
import urllib.request

from agent_playground.logging_setup import get_logger

logger = get_logger("llm")

# 这些状态码代表「服务端暂时有问题」，值得重试。
# 400/401/402 这类是请求本身错了，重试一万次结果一样，只会白烧配额。
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """调用大模型失败，且已经没法靠重试救回来。"""


def _request(body, config):
    """发一次请求，必要时重试。成功时返回一个还没关闭的响应对象。

    返回「没关闭的响应」而不是「读完的结果」，是因为流式要边读边处理。
    调用方用 with 语句接住它，读完自动关闭。
    """
    payload = json.dumps(body).encode("utf-8")
    attempts = config.max_retries + 1
    last_reason = "未知错误"

    for attempt in range(attempts):
        # 每次重试都要重新构造 request：请求体是一次性的，发过就消耗掉了
        request = urllib.request.Request(
            config.url,
            data=payload,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        logger.debug("第 %d/%d 次请求 %s", attempt + 1, attempts, config.url)

        try:
            return urllib.request.urlopen(request, timeout=config.timeout)

        # ★ 这两个 except 的顺序不能反。HTTPError 是 URLError 的子类，
        #   Python 从上往下匹配，URLError 写前面就永远轮不到 HTTPError，状态码就丢了。
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="ignore")[:200]
            if error.code not in RETRYABLE_STATUS:
                # 不可重试：直接放弃，别浪费时间
                raise LLMError(f"HTTP {error.code}：{detail}") from error
            last_reason = f"HTTP {error.code}"
            logger.warning("第 %d 次失败（%s），可重试", attempt + 1, last_reason)

        except (urllib.error.URLError, TimeoutError) as error:
            last_reason = f"网络错误（{getattr(error, 'reason', error)}）"
            logger.warning("第 %d 次失败（%s），可重试", attempt + 1, last_reason)

        # 指数退避：0.5 → 1.0 → 2.0 秒。翻倍是为了把重试摊开，别一起打过去。
        if attempt < attempts - 1:
            wait = 0.5 * (2 ** attempt)
            logger.debug("%.1f 秒后重试", wait)
            time.sleep(wait)

    raise LLMError(f"重试 {config.max_retries} 次仍然失败：{last_reason}")


def stream_chat(messages, config, on_text=None):
    """流式对话。每收到一小片文字，就交给 on_text 处理。

    参数 on_text 是一个函数。cli 传进来的函数负责把它打到屏幕上。
    这一层不关心你怎么显示——这就是「依赖注入」在实际代码里的样子。

    返回 dict：{"text": 完整回答, "usage": token 用量}
    """
    body = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "stream": True,
        # 不加这一句，流式模式下拿不到 token 用量
        "stream_options": {"include_usage": True},
    }

    pieces = []
    usage = {}

    with _request(body, config) as response:
        # response 可以按行迭代，一行一条 SSE 数据
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or not line.startswith("data:"):
                continue

            payload = line[5:].strip()  # 去掉开头的 "data:"
            if payload == "[DONE]":
                break

            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue  # 偶尔会有心跳之类的非 JSON 行，跳过

            if chunk.get("usage"):
                usage = chunk["usage"]

            for choice in chunk.get("choices") or []:
                # message 换成 delta，就是「整段」变成「一小片」
                piece = (choice.get("delta") or {}).get("content") or ""
                if piece:
                    pieces.append(piece)
                    if on_text:
                        on_text(piece)

    text = "".join(pieces)
    # 这条用 debug 不用 info：正常流程的结果由 cli 那行「输入/输出」负责显示。
    # 日志的默认职责是记录异常和过程，不是把正常流程再报一遍。
    logger.debug("回答完成：%d 字，输入 %s / 输出 %s tokens",
                 len(text), usage.get("prompt_tokens", "?"), usage.get("completion_tokens", "?"))
    return {"text": text, "usage": usage}


def chat_once(messages, config, tools=None):
    """一次性拿完整回答（非流式）。工具调用必须走这一条。

    为什么工具调用不能用流式：流式返回的 tool_calls 是分片的，
    函数名和参数会被切成好几段分批发过来，要自己拼。拼错了就是诡异的 bug。
    非流式一次给你完整的 tool_calls，是本阶段最稳的做法。
    等你要做「工具调用也实时显示」的产品时，再去处理分片。

    返回 dict：{"message": 模型的消息, "finish_reason": 结束原因, "usage": 用量}
    """
    body = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
    }
    if tools:
        body["tools"] = tools

    with _request(body, config) as response:
        data = json.loads(response.read().decode("utf-8"))

    choice = data["choices"][0]
    message = choice["message"]
    finish = choice.get("finish_reason")

    called = [c["function"]["name"] for c in (message.get("tool_calls") or [])]
    logger.debug("结束原因 %s%s", finish, f"，要求调用 {called}" if called else "")

    return {
        "message": message,
        "finish_reason": finish,
        "usage": data.get("usage") or {},
    }
