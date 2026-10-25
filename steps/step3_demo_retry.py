"""step3 的配套观察工具：本地伪造一个「前两次失败、第三次成功」的服务器。

跑法：
    python steps/step3_demo_retry.py

全程不联网，不花一分钱。如果出问题，这个脚本会自己告诉你卡在哪一步。
"""

import http.server
import importlib.util
import os
import threading
import traceback
from pathlib import Path

# ★ 让发往本机的请求绕过系统代理。
#   很多机器（公司网络、开着的翻墙工具、本项目的运行环境）都设了 HTTP_PROXY，
#   但 no_proxy 是空的。urllib 会把 127.0.0.1 的请求也丢给代理，
#   结果本地服务器一次都收不到 —— 表现就是「被请求 0 次」。
#   这两行必须在发出任何请求之前执行。
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

FAKE_SSE = [
    'data: {"choices":[{"delta":{"content":"重试"}}]}\n\n',
    'data: {"choices":[{"delta":{"content":"成功了"}}]}\n\n',
    "data: [DONE]\n\n",
]

hits = {"count": 0}


class FakeServer(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        hits["count"] += 1
        order = hits["count"]
        self.rfile.read(int(self.headers.get("Content-Length", 0)))

        if order <= 2:
            body = b'{"error":"server is busy"}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in FAKE_SSE:
            self.wfile.write(event.encode("utf-8"))
            self.wfile.flush()

    def log_message(self, *args):
        pass


def main() -> None:
    # ① 起本地假服务器
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{port}/chat/completions"
    print(f"① 假服务器已启动：{target}")

    # ② 加载 step3 的代码（用相对路径，不管从哪个目录运行都能找到）
    step3_path = Path(__file__).with_name("step3_retry.py")
    print(f"② 加载待测代码：{step3_path}")
    if not step3_path.exists():
        print("   找不到 step3_retry.py，请确认两个文件在同一个目录里")
        server.shutdown()
        return
    spec = importlib.util.spec_from_file_location("step3", step3_path)
    step3 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(step3)

    # ③ 发起调用
    print(f"③ 开始调用，最多重试 {step3.MAX_RETRIES} 次……\n")
    error_text = None
    try:
        response = step3.send_with_retry(
            {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
            url=target,
            api_key="sk-fake",
        )
        step3.print_stream(response)
    except RuntimeError as error:
        error_text = str(error)
        print(f"调用失败：{error}")
    except Exception:
        error_text = "未预期的异常"
        print("出现了意料之外的错误，完整信息如下：")
        traceback.print_exc()

    server.shutdown()

    # ④ 自检
    print(f"\n④ 服务器一共被请求了 {hits['count']} 次（预期 3 次）")

    if hits["count"] == 0:
        print("\n请求一次都没到达本地服务器。最常见的原因是代理拦截：")
        print("  · 检查环境变量 HTTP_PROXY 是否有值、no_proxy 是否包含 127.0.0.1")
        print("  · 上面报错里的「发往」地址，确认是不是你以为的那个地址")
        print("  · 也可能是代理/防火墙直接拒绝了本机连接")
    elif hits["count"] == 1:
        print("  只请求了 1 次，说明第一次失败后没有再试。两种可能：")
        print("    · MAX_RETRIES 被改成了 0")
        print("    · 503 不在 RETRYABLE_CODES 里，被判成「不可重试」，一次就放弃")
    elif hits["count"] == 3 and not error_text:
        print("  完全符合预期：2 次失败 + 1 次成功")


if __name__ == "__main__":
    main()
