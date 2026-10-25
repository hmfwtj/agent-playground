"""agent-playground：一个不依赖第三方库的最小 agent 工程骨架。

分层：
    config          读配置（.env）
    logging_setup   日志配置
    llm             和模型通信（请求、流式、重试）
    tools           工具说明书 + 真实函数
    context         上下文裁剪（按条数）
    summarizer      上下文压缩（旧的对话压成摘要）
    agent           agent 主循环
    cli             命令行入口

启动：
    python -m agent_playground
"""

__version__ = "0.2.0"
