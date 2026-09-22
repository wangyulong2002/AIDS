# =============================================================
# FastAPI 主业务服务镜像 (DEP-02)
#
# 构建（在项目根目录）:
#   docker build -f deploy/app/backend.Dockerfile -t aids/backend:latest .
#
# 预期目录结构:
#   aids-backend/          FastAPI 工程
#     ├── requirements.txt
#     └── app/
# =============================================================

# ---------- 依赖阶段 ----------
FROM python:3.11-slim AS builder
WORKDIR /build

# 编译型依赖（cryptography/bcrypt 等）需要构建工具，装完即弃
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY aids-backend/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---------- 运行阶段 ----------
FROM python:3.11-slim
WORKDIR /app

# 时区与编码：与 PRD §5.4「时间 UTC 存储」对齐
ENV TZ=UTC \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/home/aids/.local/bin:$PATH

# 非 root 运行（安全基线）
RUN groupadd -r aids && useradd -r -g aids -m aids \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 只复制已编译好的依赖，不带构建工具链
COPY --from=builder --chown=aids:aids /root/.local /home/aids/.local
COPY --chown=aids:aids aids-backend/app ./app

USER aids
EXPOSE 8080

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -sf http://localhost:8080/health || exit 1

# 生产用 gunicorn + uvicorn worker
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8080", \
     "--timeout", "120", \
     "--access-logfile", "-"]
