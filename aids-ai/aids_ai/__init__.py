"""AIDS AI 智能客服服务（FastAPI）。

包名为什么是 `aids_ai` 而不是 `app`：
    跨服务共享的契约层（错误码 / 统一响应 / 状态枚举 / 配置 / 异常处理）位于
    仓库根 `app/`，三个服务都写 `from app.core.xxx import ...`。
    如果本服务也叫 `app`，本地开发时两个 `app` 包**无法共存**——
    `sys.path` 上先命中的那个会赢，另一个静默不可见（镜像里靠 Dockerfile
    COPY 到同一目录可以规避，本地没有这一步）。故服务包独立命名。

本服务当前状态（T1 · AI-01 骨架）：
    只有「能起来、能被探活、响应体与主业务一致」这一层，业务接口由 T2/T5 落地：
        AI-02 Ark 客户端 / AI-03 Milvus / AI-04 文档解析 / AI-05 向量入库
        AI-06~AI-18 会话、RAG、转人工、工作台（见 docs/TASKS.md）

本地运行：
    # 首次：在仓库根安装共享层（提供 app.core / app.domain）
    pip install -e ".[dev]"

    # 起服务（端口 8000，与 deploy/app/ai.Dockerfile 的 EXPOSE 一致）
    cd aids-ai
    uvicorn aids_ai.main:app --reload --port 8000

    # 冒烟
    curl -s http://127.0.0.1:8000/health
"""
