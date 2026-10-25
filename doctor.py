"""诊断：程序跑不起来或聊不了的时候，先跑这个。

跑法（必须在 agent-playground 目录下）：
    cd E:/workshuju/2026-10-23-17-52-58/agent-playground
    python doctor.py

它会依次检查五件事，最后直接告诉你卡在哪一环。
不打印完整 key，只显示前 7 位和总长度，可以放心截图。
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 本文件所在目录就是项目根
PROJECT_ROOT = Path(__file__).resolve().parent


def line(title):
    print()
    print("─" * 56)
    print(title)
    print("─" * 56)


def check_1_location():
    line("① 运行位置")
    cwd = Path.cwd()
    print(f"   当前目录：{cwd}")
    print(f"   项目目录：{PROJECT_ROOT}")

    if cwd.resolve() != PROJECT_ROOT.resolve():
        print("   ✗ 当前目录不是项目目录 —— 这就是聊不了的原因")
        print("     解决：先 cd 到项目目录，再运行")
        print(f"     cd \"{PROJECT_ROOT}\"")
        return False

    print("   ✓ 位置正确")
    return True


def check_2_import():
    line("② 能否导入自己的包")
    try:
        import agent_playground
        from agent_playground.llm import stream_chat  # noqa: F401
        print(f"   ✓ 导入成功，版本 {agent_playground.__version__}")
        return True
    except Exception as error:
        print(f"   ✗ 导入失败：{type(error).__name__}: {error}")
        print("     如果错误里提到某个第三方库，说明有文件还在 import 它")
        return False


def check_3_key():
    line("③ 读取密钥")
    from agent_playground.config import ENV_FILE, Config

    print(f"   .env 路径：{ENV_FILE}")
    if not ENV_FILE.exists():
        print("   ✗ .env 不存在")
        print("     解决：复制模板再填 —— cp .env.example .env")
        return None

    print("   ✓ .env 存在")

    config = Config()
    key = config.api_key
    if not key:
        print("   ✗ 读不到 DEEPSEEK_API_KEY")
        print("     检查 .env 里有没有以 DEEPSEEK_API_KEY= 开头的一行")
        return None

    # 只显示开头，中间打码。绝不能把完整 key 打到屏幕上或截图里。
    masked = key[:7] + "*" * 8 + f"（共 {len(key)} 位）"
    print(f"   ✓ 读到了：{masked}")

    if len(key) < 20:
        print("   ⚠ 长度偏短，可能没粘全，或者还是占位符 sk-your-key-here")
    return config


def check_4_proxy():
    line("④ 代理设置")
    proxy_vars = {}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "no_proxy", "NO_PROXY"):
        value = os.environ.get(name)
        if value:
            proxy_vars[name] = value

    if not proxy_vars:
        print("   没有设置代理，直接连")

    for name, value in proxy_vars.items():
        print(f"   {name} = {value}")

    print()
    print("   提示：如果代理不通，请求会失败，而且报错看起来像「网络错误」。")
    print("   下面第 ⑤ 步会分别用「走代理」和「绕过代理」各试一次，看哪个通。")
    return proxy_vars


def _try(config, use_proxy):
    """发一个最小的请求。max_tokens=1 让它几乎不花钱。"""
    body = {
        "model": config.model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    request = urllib.request.Request(
        config.url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    if use_proxy:
        opener = urllib.request.urlopen
    else:
        # 显式传一个空的 ProxyHandler，等于「不走任何代理」
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    try:
        with opener(request, timeout=30) as response:
            response.read()
        return True, "成功"
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")[:120]
        return False, f"HTTP {error.code}：{detail}"
    except Exception as error:
        reason = getattr(error, "reason", error)
        return False, f"{type(error).__name__}：{reason}"


def check_5_network(config):
    line("⑤ 实际连通性（各发一次最小请求）")

    ok_proxy, msg_proxy = _try(config, use_proxy=True)
    print(f"   走系统代理：{'✓ 通' if ok_proxy else '✗ 不通'}   {msg_proxy}")

    ok_direct, msg_direct = _try(config, use_proxy=False)
    print(f"   绕过代理　：{'✓ 通' if ok_direct else '✗ 不通'}   {msg_direct}")

    print()
    if ok_proxy and not ok_direct:
        print("   结论：只能走代理。保持现状即可，代码不用改。")
    elif ok_direct and not ok_proxy:
        print("   结论：代理是坏的，直连没问题。")
        print("   解决：跑程序前先绕过代理 ——")
        print("       export no_proxy=\"*\"   （bash）")
        print("       set no_proxy=*         （cmd）")
    elif ok_proxy and ok_direct:
        print("   结论：两条路都通，网络没问题。聊不了的原因在别处（看上面几项）。")
    else:
        print("   结论：两条路都不通。检查这几种可能：")
        print("     · key 无效或余额不足（HTTP 401 / 402）")
        print("     · 公司或校园网拦了 api.deepseek.com")
        print("     · 模型名写错（HTTP 400 里会说明）")
        print("   把上面两行的报错原文发我。")

    return ok_proxy or ok_direct


def main():
    print()
    print("agent-playground 自检")
    print(f"Python {sys.version.split()[0]}")

    location_ok = check_1_location()
    if not location_ok:
        print()
        print("先解决位置问题，后面的检查才有意义。")
        return 1

    import_ok = check_2_import()
    if not import_ok:
        return 1

    config = check_3_key()
    if config is None:
        return 1

    check_4_proxy()
    network_ok = check_5_network(config)

    print()
    print("═" * 56)
    if network_ok:
        print("全部检查通过。如果还是聊不了，把屏幕上的报错原文发我。")
    else:
        print("网络这一环没通过，先按上面的建议处理。")
    print("═" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
