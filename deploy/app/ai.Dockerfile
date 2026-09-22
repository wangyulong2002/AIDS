# =============================================================
# FastAPI AI 服务镜像 (DEP-02)
#
# 构建（在项目根目录）:
#   docker build -f deploy/app/ai.Dockerfile -t aids/ai:latest .
#
# 预期目录结构:
#   aids-ai/               FastAPI 工程
#     ├── requirements.txt
#     └── app/
#
# 另需仓库根的共享契约层（由下方 COPY 一并叠入镜像的 app 包）：
#   app/core/            配置 / 错误码 / 统一响应
#   app/domain/          状态枚举（SSOT）
# =============================================================

# ---------- 依赖阶段 ----------
FROM python:3.11-slim AS builder
WORKDIR /build

# 编译型依赖（pymilvus/numpy 等）需要构建工具，装完即弃
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY aids-ai/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---------- 运行阶段 ----------
FROM python:3.11-slim
WORKDIR /app

ENV TZ=UTC \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/home/aids/.local/bin:$PATH

# 非 root 运行
RUN groupadd -r aids && useradd -r -g aids -m aids \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 只复制已编译好的依赖，不带构建工具链
COPY --from=builder --chown=aids:aids /root/.local /home/aids/.local
# 先铺服务自己的代码（含 app/__init__.py 与服务入口）
COPY --chown=aids:aids aids-ai/app ./app
#
# 为什么需要单独 COPY 根 app/：
#   app/core（配置/错误码/统一响应）与 app/domain（状态枚举）是**跨服务共享的
#   契约层 SSOT**，位于仓库根 app/ 下。三个服务都写 `from app.core.config import ...`
#   （全仓统一），所以镜像里的 app 包必须同时含服务代码与共享层。
#   若不 COPY，容器起来就是 ModuleNotFoundError: No module named 'app.core'。
#   备选方案（在每个服务各存一份共享层副本）会让"改一处生效三处"靠人自觉，
#   而 DDL/枚举/错误码正是最容易漂移的地方——所以选共享包，不选复制。
#
# 合并方式与前提：
#   共享包是作为 `app` 包的**子目录**叠进去的（COPY app/core ./app/core），
#   不是替换整个 app 包，因此不会盖掉服务的 app/main.py。
#   前提：服务自己的 app/ 下不得再建同名 core/ 或 domain/ 目录——会互相覆盖。
# 再叠共享契约层，与服务的 app 包合一
COPY --chown=aids:aids app/core ./app/core
COPY --chown=aids:aids app/domain ./app/domain

USER aids
EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -sf http://localhost:8000/health || exit 1

# 生产用 gunicorn + uvicorn worker；SSE 需要较长的 keepalive
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "300", \
     "--access-logfile", "-"]
