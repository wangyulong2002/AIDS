# =============================================================
# Mock 渠道服务镜像 (DEP-02)
#
# 模拟第三方渠道（支付/短信/物流），与主业务**独立部署、独立数据库**。
# 物理隔离的意义：真实场景下渠道故障不应影响商户系统，
# 隔离部署才能如实模拟这一点（PRD §8.1）。
#
# 构建（在项目根目录）:
#   docker build -f deploy/app/mock.Dockerfile -t aids/mock:latest .
#
# 预期目录结构:
#   aids-mock/             Mock 服务工程（FastAPI 独立服务，独立库独立端口）
#     ├── requirements.txt
#     └── app/
# =============================================================

# ---------- 依赖阶段 ----------
FROM python:3.11-slim AS builder
WORKDIR /build

# 编译型依赖（cryptography 等，RSA2 签名验签用）需要构建工具，装完即弃
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY aids-mock/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---------- 运行阶段 ----------
FROM python:3.11-slim
WORKDIR /app

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
COPY --chown=aids:aids aids-mock/app ./app

USER aids
EXPOSE 8081

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -sf http://localhost:8081/health || exit 1

# 生产用 gunicorn + uvicorn worker
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:8081", \
     "--timeout", "120", \
     "--access-logfile", "-"]
