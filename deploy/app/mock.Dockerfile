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
# 构建上下文（仓库根）中的相关目录：
#   app/              跨服务共享契约层（core / domain）
#   aids-mock/        本服务工程
#     ├── requirements.txt
#     └── aids_mock/    FastAPI 包（main / api / handlers）
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
# 两个顶层包，各司其职：
#   app/                 跨服务共享契约层（错误码 / 统一响应 / 状态枚举 / 配置）
#   aids_mock/         本服务的 FastAPI 工程
#
# 为什么服务包不叫 app：
#   共享层已占用 app 这个名字。两个同名包在**本地开发时无法共存**——
#   sys.path 上先命中的那个赢，另一个静默不可见（实测：普通包
#   app.__path__ 只含第一个目录，另一个直接 ModuleNotFoundError；只有
#   PEP 420 命名空间包会合并，但多服务同时在 path 上时 app.main 会静默
#   解析到错误的服务）。靠 Dockerfile 把两者 COPY 进同一目录只有镜像里成立，
#   本地没有这一步，服务级测试就跑不起来。故服务包独立命名。
COPY --chown=aids:aids app ./app
COPY --chown=aids:aids aids-mock/aids_mock ./aids_mock


USER aids
EXPOSE 8081

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -sf http://localhost:8081/health || exit 1

# 生产用 gunicorn + uvicorn worker
CMD ["gunicorn", "aids_mock.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:8081", \
     "--timeout", "120", \
     "--access-logfile", "-"]
