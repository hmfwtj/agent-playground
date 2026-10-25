"""llm.py 的测试：这一层唯一碰网络的地方，也是最容易出意外的地方。

跑法（在 agent-playground 目录下）：
    python -m unittest discover -s tests -t . -v
    python tests/test_llm.py

前两个测试文件（tools / context）都是「给一个值，看返回值」，
不联网、不花钱。但这个文件测的是「网络出错时程序怎么办」——
你没法让真实的 DeepSeek 服务器给你返回 503，也不能让它超时。

所以这里自己做一个假服务器：
    在本机开一个端口，假装自己是 DeepSeek 的 /chat/completions 接口。
    你告诉它「第 1 次请求返回 503，第 2 次返回正常内容」，它就照做。
    程序以为自己在跟真服务器说话，其实对面是我们自己写的几行代码。

好处：
    1. 不花钱、不需要 API key、不依赖网络通不通。
    2. 想测哪种错误就造哪种，真服务器上这些错误一年也遇不到几次。

这批测试大约要跑 3 秒——不是卡了，是「重试前要等一会儿」是真的在等。
"""

import json
import os

# ★ 这两行必须在 import urllib 之前执行。
# 你这台机器设了 HTTP_PROXY=127.0.0.1:65431，而 no_proxy 是空的。
# 不排除的话，连本机 127.0.0.1 的请求也会被塞给代理，假服务器永远收不到东西
# （症状是 hits == 0，以及 WinError 10061）。
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import http.server          # noqa: E402  标准库自带的服务器，不用装东西
import sys                  # noqa: E402
import threading            # noqa: E402  服务器要跑在另一个线程里，否则会挡住测试
import unittest             # noqa: E402
from pathlib import Path    # noqa: E402

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_playground import llm  # noqa: E402


# ---------------------------------------------------------------------------
# 一、假的配置对象
# ---------------------------------------------------------------------------

class FakeConfig:
    """假的配置。llm.py 只用到下面这几个属性，所以给够就行。

    这里故意**不**继承 config.Config：
    继承会去读你的 .env、要你的真 key，测试就变得「必须配好环境才能跑」。
    只要「长得像」，就够用了——Python 不管你是不是同一个类，
    只问「你有没有这个属性」。这叫鸭子类型。
    """

    def __init__(self, port, max_retries=1, timeout=5.0):
        self.api_key = "test-key-123"
        self.base_url = f"http://127.0.0.1:{port}"
        self.model = "fake-model"
        self.temperature = 0.0
        self.timeout = timeout          # 秒。测超时时会调得很小
        self.max_retries = max_retries  # 重试几次（不含第一次）

    @property
    def url(self):
        return self.base_url + "/chat/completions"


# ---------------------------------------------------------------------------
# 二、假服务器
# ---------------------------------------------------------------------------

class FakeAPI:
    """本机上的假 DeepSeek。

    用法：
        with FakeAPI(script) as api:
            llm.stream_chat(messages, FakeConfig(api.port))
        print(api.hits)     # 被请求了几次

    script 是一个函数，签名 (api, handler, 第几次请求)。
    它在**服务器线程**里执行，职责就是往 handler 里写响应。
    """

    def __init__(self, script):
        self.script = script
        self.hits = 0          # 被请求的总次数，用来验证「到底重试了没有」
        self.bodies = []       # 每次收到的请求体，用来验证「发出去的东西对不对」
        self.paths = []
        self.headers = []
        api = self            # 起个别名，好在下面的类里用（下面那个类看不到 self）

        class Handler(http.server.BaseHTTPRequestHandler):
            # 默认是 HTTP/1.0，改成 1.1 更接近真实情况
            # （1.1 下没有 Content-Length 时，客户端会一直读到连接关闭为止，正好模拟流式）
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                api.hits += 1
                # 必须把请求体读掉。不读的话，客户端那边可能卡住等不到响应。
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                api.bodies.append(json.loads(raw.decode("utf-8")))
                api.paths.append(self.path)
                api.headers.append(dict(self.headers))

                try:
                    api.script(api, self, api.hits)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # 客户端已经不等了（超时测试里就是这样），写会失败。
                    # 这是预期内的，不用让异常冒出来污染测试输出。
                    pass

            def log_message(self, *args):
                # 关掉「每来一个请求就打一行」的日志，否则测试输出全是噪音
                pass

        # 端口写 0 = 让系统随便挑一个没人用的端口，避免和别的程序撞车
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        # daemon=True：主程序退出时这个线程自动结束，不会把测试进程吊死
        # poll_interval 默认 0.5 秒：close() 里的 shutdown() 要等它空转一圈才生效。
        # 每个测试都开关一次服务器，11 个测试就白等 5 秒多。改成 0.05 秒几乎没开销。
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    def close(self):
        self.httpd.shutdown()      # 停掉 serve_forever 的循环
        self.httpd.server_close()  # 释放端口

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False   # 返回 False 表示「异常不要被我吞掉，继续往外抛」


# ---------------------------------------------------------------------------
# 三、几个写响应的小工具
# ---------------------------------------------------------------------------

def send_json(handler, status, data):
    """回一个普通的 JSON 响应（非流式接口就用这个）。"""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))  # 有长度，客户端才知道读到哪算完
    handler.end_headers()
    handler.wfile.write(body)


def send_error_json(handler, status, message="模拟的错误"):
    """回一个错误状态码。真实接口出错时也是这个形状。"""
    send_json(handler, status, {"error": {"message": message}})


def open_sse(handler):
    """开始一个流式响应：先发响应头。

    注意这里**故意不写 Content-Length**。
    SSE 是「不知道总共多长」的响应，只能靠连接关闭来判断结束。
    """
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Connection", "close")
    handler.end_headers()


def write_sse(handler, text):
    """往流里写一片文字，格式照着真实的抄：data: {json}\\n\\n"""
    chunk = {"choices": [{"delta": {"content": text}}]}
    line = "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
    handler.wfile.write(line.encode("utf-8"))


def write_usage(handler, prompt_tokens=11, completion_tokens=3):
    """用量信息的那个分片：choices 是空的，只有 usage。"""
    chunk = {"choices": [], "usage": {"prompt_tokens": prompt_tokens,
                                      "completion_tokens": completion_tokens}}
    handler.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode("utf-8"))


def write_done(handler):
    """流正常结束的标志。"""
    handler.wfile.write(b"data: [DONE]\n\n")


# ---------------------------------------------------------------------------
# 四、流式：正常情况下能不能正确拼接
# ---------------------------------------------------------------------------

class TestStreamChat(unittest.TestCase):

    def test_正常的流式回答(self):
        """三片文字要拼成一整句，用量也要拿到。"""
        def script(api, handler, n):
            open_sse(handler)
            write_sse(handler, "北京")
            write_sse(handler, "今天")
            write_sse(handler, "多云")
            write_usage(handler, prompt_tokens=11, completion_tokens=3)
            write_done(handler)

        pieces = []   # on_text 每被调用一次，就往这里塞一片
        with FakeAPI(script) as api:
            result = llm.stream_chat(
                [{"role": "user", "content": "北京天气"}],
                FakeConfig(api.port),
                on_text=pieces.append,   # 直接把 list.append 当回调传进去
            )

        self.assertEqual(result["text"], "北京今天多云")
        self.assertEqual(result["usage"]["prompt_tokens"], 11)
        # on_text 收到的是「一片一片」，不是整句——这就是流式的意义
        self.assertEqual(pieces, ["北京", "今天", "多云"])

    def test_不认识的行要跳过(self):
        """SSE 里会混进心跳、注释、坏掉的 JSON，都不许让程序崩。

        真实接口偶尔会发 ": keep-alive" 这种注释行，
        网络抖动时也可能收到半截 JSON。这些都不能处理成文本。
        """
        def script(api, handler, n):
            open_sse(handler)
            handler.wfile.write(b": keep-alive\n\n")        # 注释行，不是我们的数据
            handler.wfile.write(b"data: {\"choices\": [\n\n")  # 坏掉的 JSON
            handler.wfile.write(b"\n")                      # 空行
            write_sse(handler, "好的")
            write_done(handler)

        with FakeAPI(script) as api:
            result = llm.stream_chat([{"role": "user", "content": "hi"}], FakeConfig(api.port))

        self.assertEqual(result["text"], "好的")

    def test_半截流不能丢掉已经收到的字(self):
        """★ 服务器发到一半就断了：已经说出口的字必须留着。

        这是流式最常见的真实故障：模型说到一半，连接被掐、
        网关超时、或者手机网络切了一下。
        这时候如果整个回答被丢掉、或者直接抛异常，
        用户看到的就是「明明屏幕上已经出来一半了，突然全没了」。

        llm.py 现在的写法（先 append 到 pieces，最后才 join）
        天然满足这一条：异常也好、断流也好，已经进来的都在 pieces 里。
        这个测试就是把它锁住，防止以后有人改成「先收集再一次性处理」。
        """
        def script(api, handler, n):
            open_sse(handler)
            write_sse(handler, "这个问题要分")
            write_sse(handler, "三步来看，")
            # 到此为止：不写 [DONE]，直接返回 → 连接关闭，客户端读到 EOF
            # 真实世界里这代表「回答被截断了」

        with FakeAPI(script) as api:
            result = llm.stream_chat([{"role": "user", "content": "怎么学"}], FakeConfig(api.port))

        self.assertEqual(result["text"], "这个问题要分三步来看，")
        self.assertEqual(result["usage"], {}, "没收到用量时给空字典，不要报错")

    def test_请求体里该有的东西(self):
        """发出去的请求必须带上这些字段，否则真实接口会拒绝。"""
        def script(api, handler, n):
            open_sse(handler)
            write_sse(handler, "ok")
            write_done(handler)

        with FakeAPI(script) as api:
            llm.stream_chat([{"role": "user", "content": "hi"}], FakeConfig(api.port))
            body = api.bodies[0]
            headers = api.headers[0]

        self.assertTrue(body["stream"], "流式请求必须 stream=True")
        self.assertEqual(body["model"], "fake-model")
        # 不加 include_usage，流式模式下永远拿不到用量
        self.assertTrue(body["stream_options"]["include_usage"])
        self.assertEqual(headers.get("Authorization"), "Bearer test-key-123")
        self.assertEqual(api.paths[0], "/chat/completions")


# ---------------------------------------------------------------------------
# 五、重试：这才是网络层真正的考验
# ---------------------------------------------------------------------------

class TestRetry(unittest.TestCase):
    """这一组每个测试都要真等一会儿——重试的退避是 time.sleep，不是假的。

    max_retries=1 意味着最多请求 2 次，中间等 0.5 秒。
    """

    def test_503_之后重试成功(self):
        """第一次 503（服务端临时故障），第二次正常 → 用户无感。"""
        def script(api, handler, n):
            if n == 1:
                send_error_json(handler, 503, "Service Unavailable")
                return
            open_sse(handler)
            write_sse(handler, "第二次成功了")
            write_done(handler)

        with FakeAPI(script) as api:
            result = llm.stream_chat([{"role": "user", "content": "hi"}],
                                     FakeConfig(api.port, max_retries=1))

        self.assertEqual(result["text"], "第二次成功了")
        self.assertEqual(api.hits, 2, "应该正好请求 2 次")

    def test_一直_503_就放弃并报错(self):
        """重试用完还是失败，要给一个看得懂的错，而不是死循环。"""
        def script(api, handler, n):
            send_error_json(handler, 503, "Service Unavailable")

        with FakeAPI(script) as api:
            with self.assertRaises(llm.LLMError) as ctx:
                llm.stream_chat([{"role": "user", "content": "hi"}],
                                FakeConfig(api.port, max_retries=2))

        # max_retries=2 → 第 1 次 + 重试 2 次 = 一共 3 次
        self.assertEqual(api.hits, 3, "重试次数不对")
        self.assertIn("503", str(ctx.exception))

    def test_401_一次都不重试(self):
        """★ 这条测试锁住一个踩过的坑。

        llm.py 里两个 except 的顺序不能反：
        HTTPError 是 URLError 的子类，把 URLError 写在前面的话，
        401 就会被当成「网络错误」重试 3 次 —— 白花钱，还查不出原因。

        所以这里断言 hits == 1：只要有人把顺序改反，这个测试立刻变红。
        """
        def script(api, handler, n):
            send_error_json(handler, 401, "Authentication Fails, Your api key is invalid")

        with FakeAPI(script) as api:
            with self.assertRaises(llm.LLMError) as ctx:
                llm.stream_chat([{"role": "user", "content": "hi"}],
                                FakeConfig(api.port, max_retries=2))

        self.assertEqual(api.hits, 1, "请求本身错了，重试一万次结果也一样")
        message = str(ctx.exception)
        self.assertIn("401", message)
        self.assertIn("api key", message, "服务端给的原始错误要能透出来，否则没法排查")

    def test_超时会重试(self):
        """服务器半天不回 → 客户端超时 → 重试。

        假服务器故意睡 0.6 秒，而客户端只等 0.2 秒。
        这里用 Event().wait() 而不是 time.sleep()，
        是因为它不受别处 monkeypatch 影响，等多久就是多久。
        """
        def script(api, handler, n):
            threading.Event().wait(0.6)   # 装死
            send_error_json(handler, 500, "太晚了")

        with FakeAPI(script) as api:
            with self.assertRaises(llm.LLMError) as ctx:
                llm.stream_chat([{"role": "user", "content": "hi"}],
                                FakeConfig(api.port, max_retries=1, timeout=0.2))

        self.assertEqual(api.hits, 2, "超时属于可重试，应该再来一次")
        self.assertIn("网络错误", str(ctx.exception))


# ---------------------------------------------------------------------------
# 六、非流式：工具调用走这一条
# ---------------------------------------------------------------------------

class TestChatOnce(unittest.TestCase):

    def test_解析工具调用(self):
        """模型要求调工具时，返回的结构必须能让 agent 循环直接用。"""
        tool_call = {
            "id": "call_abc",
            "type": "function",
            "function": {"name": "get_weather", "arguments": "{\"city\": \"北京\"}"},
        }
        body = {
            "choices": [{
                "message": {"role": "assistant", "content": None, "tool_calls": [tool_call]},
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 30, "completion_tokens": 12},
        }

        with FakeAPI(lambda api, handler, n: send_json(handler, 200, body)) as api:
            result = llm.chat_once([{"role": "user", "content": "北京天气"}],
                                   FakeConfig(api.port))

        self.assertEqual(result["finish_reason"], "tool_calls")
        self.assertEqual(result["message"]["tool_calls"][0]["function"]["name"], "get_weather")
        self.assertEqual(result["usage"]["prompt_tokens"], 30)

    def test_传了工具才带_tools(self):
        """没给工具就别往请求里塞 tools 字段，免得模型莫名其妙想调工具。"""
        body = {"choices": [{"message": {"role": "assistant", "content": "你好"},
                             "finish_reason": "stop"}]}
        declared = [{"type": "function",
                     "function": {"name": "get_weather", "description": "查天气",
                                  "parameters": {"type": "object", "properties": {}}}}]

        with FakeAPI(lambda api, handler, n: send_json(handler, 200, body)) as api:
            llm.chat_once([{"role": "user", "content": "hi"}], FakeConfig(api.port))
            without = api.bodies[0]

            llm.chat_once([{"role": "user", "content": "hi"}], FakeConfig(api.port),
                          tools=declared)
            with_tools = api.bodies[1]

        self.assertNotIn("tools", without)
        self.assertEqual(with_tools["tools"], declared)

    def test_工具调用不用流式(self):
        """chat_once 的请求里不能有 stream:True。

        流式返回的 tool_calls 是分片的，名字和参数会被切开发过来，
        自己拼很容易出错。所以工具调用这一路必须是普通请求。
        """
        body = {"choices": [{"message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}]}

        with FakeAPI(lambda api, handler, n: send_json(handler, 200, body)) as api:
            llm.chat_once([{"role": "user", "content": "hi"}], FakeConfig(api.port))
            body_sent = api.bodies[0]

        self.assertNotIn("stream", body_sent)


if __name__ == "__main__":
    unittest.main()
