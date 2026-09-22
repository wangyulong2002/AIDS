# =============================================================
# Nginx 镜像（内置反代配置与静态页）
#
# 同 MySQL：构建期 COPY 替代 bind mount，规避 WSL2 挂载故障。
#
# 构建（在项目根目录）:
#   docker compose -f deploy/docker-compose.yml build nginx
#
# 前端工程就绪后，把 dist 产物 COPY 进来即可（见文末注释）。
# =============================================================
FROM nginx:1.27-alpine

# 反代规则：/api/ai/** → FastAPI AI 服务（SSE 关闭 buffering），/api/** → FastAPI 主业务
COPY deploy/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf

# 占位页（前端工程启动后由 Vite dev server 或构建产物接管）
COPY deploy/nginx/html/ /usr/share/nginx/html/

# 证书目录占位（开发环境走 HTTP，生产挂载真实证书）
RUN mkdir -p /etc/nginx/certs

# 配置语法自检：构建期失败好过运行期崩溃
RUN nginx -t

EXPOSE 80 443

# ---------------------------------------------------------------------
# 前端就绪后（T2 之后），追加以下行把构建产物打进镜像：
#
#   COPY aids-mall/dist/ /usr/share/nginx/html/
#
# 并在 docker-compose.yml 中为该 stage 增加多阶段构建。
# ---------------------------------------------------------------------
