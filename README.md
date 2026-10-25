# agent-playground

> 一个**只用 Python 标准库**实现的 DeepSeek 命令行 agent：模型自己决定调哪个工具、调几次，程序负责执行并把结果回传。  
> 用来练习超时重试、SSE 流式解析和上下文裁剪。

## 它能做什么

- **会调用工具**：模型自己决定调哪个、调几次（天气 / 距离 / 当前时间），程序负责执行并回传结果
- 多轮对话，上下文在进程内保留，`/clear` 清空，`/tools` 查看可用工具
- **上下文自动裁剪**：超过上限只发最近若干条，且保证 `system` 提示和最后一轮问题不被丢掉
- 超时与指数退避重试，只对暂时性错误（408 / 409 / 429 / 5xx）重试
- 每轮结束后显示 token 用量（单价填了则显示估算花费），多轮工具调用会累加
- 日志与回答分离：日志走 stderr，`--verbose` 才输出调试信息

> 启用工具后回答是**一次性输出**的，不再逐字流式显示。  
> 原因：工具调用必须拿到完整的 `tool_calls` 结构，流式下它是分片下发的，需要额外拼接。  
> `llm.py` 里的 `stream_chat()` 仍然保留，纯聊天的流式能力没有丢，只是当前入口走的是 agent 那条路。

## 快速开始

需要 Python 3.10+。**不需要安装任何第三方库。**

```bash
# 1. 配置密钥（把 .env.example 复制成 .env，填进你的 key）
cp .env.example .env

# 2. 跑起来（任选一种）
python run.py                        # 最省事：在任何目录都能跑
python -m agent_playground           # 正规跑法：必须先 cd 进 agent-playground/
```

看到 `你 >` 就说明成功了。输入问题回车，`/exit` 退出，`/clear` 清空上下文。

```bash
python run.py --verbose              # 打开调试日志
```

> **为什么有两个入口？**  
> `python -m agent_playground` 只认「当前目录」，必须先 cd 进项目根；  
> 一旦在 `agent_playground/` 里面执行，就会报 `No module named agent_playground`。  
> `run.py` 把项目根写死了，所以在哪个目录都能跑，编辑器里点运行也不出错。  
> 两者最终跑的是同一份代码（`agent_playground/cli.py`）。

## 配置

全部通过 `.env` 或环境变量，见 `.env.example`。密钥只从环境读，代码里不出现。

| 变量                     | 必填 | 默认值                        | 说明                   |
| ---------------------- | -- | -------------------------- | -------------------- |
| `DEEPSEEK_API_KEY`     | 是  | —                          | API 密钥               |
| `DEEPSEEK_BASE_URL`    | 否  | `https://api.deepseek.com` | 接口地址                 |
| `DEEPSEEK_MODEL`       | 否  | `deepseek-chat`            | 模型名，想换模型改这里          |
| `LLM_TEMPERATURE`      | 否  | `1.0`                      | 采样温度                 |
| `LLM_READ_TIMEOUT`     | 否  | `90`                       | 单次请求超时（秒）            |
| `LLM_MAX_RETRIES`      | 否  | `2`                        | 最大重试次数               |
| `MAX_CONTEXT_MESSAGES` | 否  | `20`                       | 每次最多发多少条消息，`0` 表示不裁剪 |
| `SUMMARY_KEEP_RECENT`  | 否  | `8`                        | 压缩后至少保留多少条原文，`0` 表示关闭摘要 |
| `PRICE_IN_PER_MTOK`    | 否  | `0`                        | 输入单价，元/百万 token      |
| `PRICE_OUT_PER_MTOK`   | 否  | `0`                        | 输出单价，元/百万 token      |

## 架构

```
cli.py  ──▶  agent.py  ──▶  llm.py  ──▶  api.deepseek.com
   │            │             │
   │            │             ├── _request()        组装请求体 + 超时 + 退避重试
   │            │             ├── stream_chat()     逐行解析 SSE，边收边交给 on_text（当前入口未用）
   │            │             └── chat_once()       非流式，agent 走这条
   │            │
   │            └── run_agent()   每轮：压缩 → 裁剪 → 合并摘要 → 问 → 调工具 → 塞结果 → 再问
   │                    │
   │                    ├──▶  tools.py       TOOLS 说明书 + 真实函数 + TOOL_FUNCTIONS 调度表
   │                    ├──▶  context.py     trim()：超长时只留最近若干条，保证结构合法
   │                    └──▶  summarizer.py  find_cut() 算切点 / merge_into_system() 并进 system
   │                                         summarize() 唯一会发请求的那个，失败不删原文
   │
   ├──▶  config.py          .env → Config 对象
   └──▶  logging_setup.py   setup() 配一次，get_logger() 到处拿
```

摘要状态不放在 `messages` 里：它作为 `run_agent(summary=...)` 的参数传入、从返回值取出，由 `cli` 保管。塞进 `messages[0]` 的话，下次合并会在旧摘要上再叠一遍，越滚越长且不报错。

一次带工具的请求，`messages` 会是这样长出来的：

```
1. system     你的工作守则
2. user       北京和上海哪个更热？相距多远？
3. assistant  （要求调用 get_weather / get_weather / get_distance）
4. tool       北京：多云 18℃
5. tool       上海：小雨 21℃
6. tool       1213 公里
7. assistant  上海更热，差 3℃，相距约 1213 公里     ← 第 2 轮请求产出
```

## 已知限制

1. **工具数据是假的。** 天气只有北京、上海、深圳 3 个城市，距离只有 3 对固定值，除 `get_current_time` 外都不实时，问到其它城市一律返回 error。
   这是**故意的**：接第三方 API 会引入密钥管理、限流、配额，会把注意力从 agent 循环本身引开。要接真接口，只需替换 `tools.py` 里那三个函数，`TOOLS` 说明书一个字不用改。

2. **裁剪按「消息条数」算，不按 token 算。** 默认上限 20 条，一次完整工具调用占 4 条（user → assistant 要工具 → tool 结果 → assistant 回答），所以大约 5 轮就开始触发压缩。一条 10 字的提问和一条 2000 字的回答，在裁剪眼里一样重。
   为什么不用 token：精确切分需要 tokenizer，那是第三方依赖，与「零依赖」直接冲突。
   被压掉的内容**不是直接丢弃**：先让模型浓缩成一段摘要（上限 200 字）再删原文，摘要每轮随 system 一起发出去。但摘要会丢细节 —— 数字、人名、已确认的查询结果通常保得住，语气、举例、具体措辞保不住。`SUMMARY_KEEP_RECENT` 控制压缩后保留多少条原文，设 0 退回「直接裁剪」。
   实测：同一段 6 轮对话，「我叫小明」那轮被压掉后，带摘要能答出名字，关掉摘要则回答「你没有告诉我你的名字」。

3. **摘要压缩依赖额外一次模型请求。** 每触发一次压缩就多一次调用，多花一份 token 和 1~3 秒延迟。
   失败时（网络错误、模型返回空）不会删除任何原文，这一轮退化成「直接裁剪」，只是会丢旧内容。

4. **没有持久化。** 对话状态只在内存里，进程一退出全部丢失，`/clear` 之后也找不回来（摘要也一起清空）。
   方向：写进 SQLite，启动时加载。

5. **单进程、同步、阻塞。** 一次只能处理一个请求，两个会话同时进来就会排队。
   方向：换成异步，或者每个会话起一个 worker。

## 开发

用标准库 `unittest`，**不用 pytest**（本项目坚持零依赖）。测试不联网、不花钱。

工具层、上下文层、摘要层是纯函数测试，瞬间跑完；网络层那组要真的等重试退避，整套约 3.6 秒。

```bash
python -m unittest discover -s tests -t . -v   # 全部（65 个）
python tests/test_tools.py                     # 只跑工具层
python tests/test_context.py                   # 只跑上下文裁剪
python tests/test_summarizer.py                # 只跑摘要压缩
python tests/test_agent.py                     # 只跑主循环
python tests/test_llm.py                       # 只跑网络层
```

| 组                                               | 防的是什么                                                 |
| ----------------------------------------------- | ----------------------------------------------------- |
| `TestDeclaration`                               | 说明书和代码「悄悄对不上」。含一条关键检查：`TOOLS` 里的参数名必须和函数签名逐字一致        |
| `TestRunTool`                                   | 各种畸形输入都不许抛异常。参数名写错、工具名不存在、参数不是 JSON、缺必填参数……           |
| `TestTrim`                                      | 裁剪后的结构必须合法：`system` 不能丢、不能以 `tool` 开头、工具结果必须留得住它的调用方  |
| `TestRender` / `TestFindCut` / `TestMergeIntoSystem` / `TestSummarize` | 摘要压缩的四个纯逻辑环节。重点：切点必须落在 `user` 消息上（否则留下孤儿 `tool`）、`system` 不能被压掉、摘要要并进原来那条 `system` 而不是新插一条、模型返回空摘要必须当失败（否则原文被删、信息真丢） |
| `TestCompression`                               | 摘要接进主循环之后：压缩失败一条消息都不删、`system` 必须还在、上一轮的摘要要传进这一轮 |
| `TestStreamChat` / `TestRetry` / `TestChatOnce` | 网络层。用本机假服务器模拟 503、401、超时、半截流，验证重试边界、以及「连接断了但已收到的字不能丢」 |

网络层测试靠一个本机的假服务器（`FakeAPI`）：它假装自己是 `api.deepseek.com`，你告诉它「第几次请求返回什么」，它照做。真服务器上一年遇不到几次的错误，只有这样才测得到。

## 下一步

- 加 `Dockerfile`，做到 `docker run` 一条命令启动
- 裁剪改成按 token 计数（需要自己实现一个粗略的 tokenizer，才能不引入第三方库）
- 对话历史持久化到 SQLite
- 把 `tools.py` 里的假数据换成真实接口

## 许可

[MIT](LICENSE) © 2026 受气包
