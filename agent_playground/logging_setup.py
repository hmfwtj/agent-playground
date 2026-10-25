"""日志：整个项目只在这里配置一次。

为什么不用 print：
    1. print 关不掉。上线后想关掉调试输出，只能回来一处处删代码。
    2. print 没有时间、没有来源。出问题时你不知道是哪一行、什么时候发生的。
    3. print 分不了级别。想只看错误的时候，它和普通信息混在一起。

用法（三步，全项目一致）：
    setup()                          # 在 main() 里调一次，就这一次
    logger = get_logger("llm")       # 其他模块各拿一个，别用 print
    logger.info("发请求")             # 平时输出
    logger.debug("请求体 %s", body)   # 只有 --verbose 时才出现
"""

import logging
import sys
from pathlib import Path

# 格式串里 %(xxx)s 的每一项，由 logging 在输出时自动填。
#   asctime   时间           levelname  级别
#   name      谁打的日志      message    你写的内容
FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-16s  %(message)s"
DATE_FORMAT = "%H:%M:%S"

# 模块级变量，记录「配过没有」。放在函数外面，函数里要改它必须先写 global。
_configured = False


def setup(level="INFO", log_file=None):
    """配置项目自己的 logger。重复调用只生效一次，否则日志会打印两遍。"""
    global _configured
    if _configured:
        return

    # 只动 "agent" 这个分支，不改全局 root。
    # 好处：第三方库（有一天你会用到）的日志格式不会被我们搅乱。
    root = logging.getLogger("agent")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # handler 决定「往哪输出」。一个 logger 可以挂多个 handler，同时往多处写。
    #
    # ★ 为什么是 sys.stderr 而不是 sys.stdout：
    #   stdout 是「程序的结果」，stderr 是「程序的诊断信息」，业界默认这么分。
    #   混在一起会出现这种难看的东西：流式回答还没换行，日志就贴上去了。
    #   分开之后，`python -m agent_playground > answer.txt` 只收回答，日志还在屏幕上。
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
    root.addHandler(console)

    if log_file:
        # log_file 是可选参数，不传就不写文件。写法同 python -m xxx --log-file run.log
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)  # 目录不存在就先建出来
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
        root.addHandler(file_handler)

    _configured = True


def get_logger(name):
    """拿一个带项目前缀的 logger。name 会出现在日志的第 3 列。"""
    return logging.getLogger("agent." + name)
