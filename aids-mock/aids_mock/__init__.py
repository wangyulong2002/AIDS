"""AIDS Mock 渠道服务（FastAPI）。

职责（PRD §8）：模拟第三方支付 / 短信 / 物流渠道，供主业务联调与故障注入。
    独立服务、独立库（docs/sql/mock_schema.sql，7 表）、独立端口（8081）。
    真实渠道接入后，实现替换为同一 `PaymentChannel` 接口（Protocol/ABC）的
    真渠道实现，调用方代码不变——故 Mock 不是"玩具代码"，而是接口契约的
    第一个实现。

包名为什么是 `aids_mock` 而不是 `app`：
    共享契约层占用仓库根 `app/`（错误码 / 统一响应 / 配置 / 异常处理），
    两个同名包在本地开发时无法共存（详见 aids_ai/__init__.py 的说明）。

本服务当前状态（T1 · 骨架）：只有「能起来、能被探活、响应体与主业务一致」。
    业务与故障注入由 MOCK-01~MOCK-04 落地（见 docs/TASKS.md T1/T2）。

本地运行：
    # 首次：在仓库根安装共享层（提供 app.core / app.domain）
    pip install -e ".[dev]"

    # 起服务（端口 8081，与 deploy/app/mock.Dockerfile 的 EXPOSE 一致）
    cd aids-mock
    uvicorn aids_mock.main:app --reload --port 8081

    # 冒烟
    curl -s http://127.0.0.1:8081/health
"""
