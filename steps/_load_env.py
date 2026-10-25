"""从项目根目录的 .env 文件里读配置，塞进环境变量。

为什么需要这个文件：
    Python 本身不会自动读 .env。你把 key 写进 .env，程序是看不见的。
    真实项目里用 python-dotenv 一行搞定，但我们在打基础，
    先手写一次，看清它到底做了什么——总共不到 20 行。

怎么用（每个 step 脚本顶部都有这两行）：
    from _load_env import load_env
    load_env()
"""

import os
from pathlib import Path

# __file__ 是这个文件自己的路径，比如 .../agent-playground/steps/_load_env.py
#   .parent        → .../agent-playground/steps
#   .parent.parent → .../agent-playground   （项目根目录）
# .resolve() 把相对路径变成绝对路径，防止因为当前目录不同而算错。
ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def load_env():
    """读 .env 并写入 os.environ。返回 True 表示找到了 .env 文件。"""
    if not ENV_FILE.exists():
        # 没有 .env 不算错误：也许你用的是 setx 设的系统环境变量
        return False

    text = ENV_FILE.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()

        # 跳过空行和注释行（以 # 开头的）
        if not line or line.startswith("#"):
            continue
        # 没有等号的行不是配置，跳过
        if "=" not in line:
            continue

        # 只按第一个 = 切开。因为值里面可能还有 = （比如某些 base64 的 key）
        key, value = line.split("=", 1)
        key = key.strip()
        # 去掉值两边可能带的引号，.env 里写 A="abc" 和 A=abc 都算
        value = value.strip().strip('"').strip("'")

        # ★ 环境变量优先：外面已经手动设过了，就不让 .env 覆盖它。
        #   这样临时想换个 key，直接在命令行 set 一下就行，不用改文件。
        if key and key not in os.environ:
            os.environ[key] = value

    return True
