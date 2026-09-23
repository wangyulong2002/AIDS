# =============================================================
# FastAPI 主业务服务镜像 (DEP-02)
#
# 构建（在项目根目录）:
#   docker build -f deploy/app/backend.Dockerfile -t aids/backend:latest .
#
# 构建上下文（仓库根）中的相关目录：
#   app/              跨服务共享契约层（core / domain）
#   aids-backend/     本服务工程
#     ├── requirements.txt
#     └── aids_backend/ FastAPI 包（main / api / handlers）
# =============================================================

# ---------- 依赖阶段 ----------
FROM python:3.11-slim AS builder
WORKDIR /build

# 编译型依赖（cryptography/bcrypt 等）需要构建工具，装完即弃
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# constraints.txt（仓库根）是已验证的版本快照；用 -c 只约束版本、不限定安装集合
COPY aids-backend/requirements.txt constraints.txt .
RUN pip install --no-cache-dir --user -r requirements.txt -c constraints.txt

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
# 两个顶层包，各司其职：
#   app/                 跨服务共享契约层（错误码 / 统一响应 / 状态枚举 / 配置）
#   aids_backend/         本服务的 FastAPI 工程
#
# 为什么服务包不叫 app：
#   共享层已占用 app 这个名字。两个同名包在**本地开发时无法共存**——
#   sys.path 上先命中的那个赢，另一个静默不可见（实测：普通包
#   app.__path__ 只含第一个目录，另一个直接 ModuleNotFoundError；只有
#   PEP 420 命名空间包会合并，但多服务同时在 path 上时 app.main 会静默
#   解析到错误的服务）。靠 Dockerfile 把两者 COPY 进同一目录只有镜像里成立，
#   本地没有这一步，服务级测试就跑不起来。故服务包独立命名。
COPY --chown=aids:aids app ./app
COPY --chown=aids:aids aids-backend/aids_backend ./aids_backend


USER aids
EXPOSE 8080

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -sf http://localhost:8080/health || exit 1

# 生产用 gunicorn + uvicorn worker
CMD ["gunicorn", "aids_backend.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8080", \
     "--timeout", "120", \
     "--access-logfile", "-"]
