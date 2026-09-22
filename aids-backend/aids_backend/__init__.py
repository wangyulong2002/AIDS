"""AIDS 主业务服务（FastAPI）。

包名为什么是 `aids_backend` 而不是 `app`：
    跨服务共享的契约层（错误码 / 统一响应 / 状态枚举 / 配置）位于仓库根
    `app/`，三个服务都写 `from app.core.errors import ...`。
    如果本服务也叫 `app`，本地开发时两个 `app` 包**无法共存**——
    `sys.path` 上先命中的那个会赢，另一个静默不可见
    （实测：普通包 `app.__path__` 只含第一个目录，`app.main` 直接
    ModuleNotFoundError；只有 PEP 420 命名空间包才会合并，但那样
    多个服务同时在 path 上时 `app.main` 会静默解析到**错误的服务**）。
    镜像里靠 Dockerfile 把两者 COPY 到同一目录可以规避，本地没有这一步。
    故服务包独立命名，共享包与服务包各自清晰、零歧义。

本地运行：
    # 首次：在仓库根安装共享层（提供 app.core / app.domain）
    pip install -e ".[dev]"

    # 起服务
    cd aids-backend
    uvicorn aids_backend.main:app --reload --port 8080

    # 冒烟
    curl -s http://127.0.0.1:8080/health
"""
