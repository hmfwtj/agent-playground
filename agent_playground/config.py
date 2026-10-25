"""配置：所有可变的东西从这里读，代码里不写死。

两条原则：
    1. 密钥永远不写进 .py。写进 .env，而 .env 在 .gitignore 里。
    2. 默认值写在这里，.env 只覆盖你真正想改的那几项。
"""

import os
from pathlib import Path

# __file__ 是本文件的路径（.../agent-playground/agent_playground/config.py）
# .parent 拿到它所在的目录 agent_playground/，再 .parent 是项目根 agent-playground/
# 用绝对路径而不是 ".env"，是因为你的当前目录一变，相对路径就指错地方了。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(path=None):
    """把 .env 里的 KEY=VALUE 读进环境变量。

    已经存在的环境变量不覆盖——这样你临时 `set DEEPSEEK_API_KEY=xxx`
    就能压过文件里的值，方便测试。
    """
    path = Path(path) if path else ENV_FILE
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # 空行和注释跳过
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue  # 不是 KEY=VALUE 格式，忽略

        # 只按第一个 = 切。因为值里可能还有 = （比如 base64 的 key）
        key, value = line.split("=", 1)
        key = key.strip()
        # 去掉值两边可能存在的引号：是 "sk-xx" 和 sk-xx 都认
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def _read(key, default=""):
    """读一个环境变量，顺手去掉两边空白。"""
    return os.environ.get(key, default).strip()


class Config:
    """一次装配好全部配置，其他模块只管用，不再碰环境变量。"""

    def __init__(self):
        load_env_file()

        self.api_key = _read("DEEPSEEK_API_KEY")
        self.base_url = _read("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = _read("DEEPSEEK_MODEL", "deepseek-chat")
        self.temperature = float(_read("LLM_TEMPERATURE", "1.0"))
        self.timeout = float(_read("LLM_READ_TIMEOUT", "90"))
        self.max_retries = int(_read("LLM_MAX_RETRIES", "2"))
        # 每次最多发多少条消息给模型。超了就从最早的开始丢。
        # 想亲眼看见裁剪发生，在 .env 里把它改成 6 试一次。
        self.max_context_messages = int(_read("MAX_CONTEXT_MESSAGES", "20"))
        # 摘要压缩：历史超长时，把早期对话压成摘要，至少保留多少条原文。
        # 设成 0 就关掉摘要，退回「直接砍掉」的老行为。
        # 为什么要留这么多：一轮工具调用占 4 条消息，留 8 条大约等于最近 2 轮对话，
        # 够模型接上当前话题。设太小会压得很勤，每次都多花一次请求。
        self.summary_keep_recent = int(_read("SUMMARY_KEEP_RECENT", "8"))
        # 单价：元 / 百万 token。不填就是 0，只是不显示花费，不影响用。
        self.price_in = float(_read("PRICE_IN_PER_MTOK", "0"))
        self.price_out = float(_read("PRICE_OUT_PER_MTOK", "0"))

    @property
    def url(self):
        """完整接口地址。拼一次省得每个地方都写。"""
        return self.base_url + "/chat/completions"

    def check(self):
        """启动时检查必备项。缺 Key 立刻报一个看得懂的错，而不是等 401。"""
        if not self.api_key:
            raise RuntimeError(
                f"缺少 DEEPSEEK_API_KEY。请打开这个文件填上：\n    {ENV_FILE}"
            )
