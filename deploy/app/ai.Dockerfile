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
COPY --chown=aids:aids aids-ai/app ./app

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
