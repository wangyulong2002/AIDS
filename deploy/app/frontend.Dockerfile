# =============================================================
# 前端镜像模板 (DEP-02) —— 商城 C 端 / 管理后台 B 端共用
#
# 两个前端工程结构相同，只是构建产物与 Nginx 配置不同。
# 通过构建参数区分，避免维护两份几乎相同的 Dockerfile。
#
# 构建:
#   docker build -f deploy/app/frontend.Dockerfile \
#     --build-arg APP_DIR=aids-mall --build-arg APP_NAME=商城 \
#     -t aids/mall:latest .
#
# 预期目录结构:
#   aids-mall/             商城前端
#   aids-admin/            管理后台
#     ├── package.json
#     ├── vite.config.ts
#     └── src/
# =============================================================

# ---------- 构建阶段 ----------
FROM node:20-alpine AS builder
WORKDIR /build

ARG APP_DIR
ARG VITE_API_BASE_URL=/api

# 先复制依赖清单，利用层缓存
COPY ${APP_DIR}/package.json ${APP_DIR}/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY ${APP_DIR}/ ./
# 构建期注入 API 地址（Vite 变量必须在构建时确定）
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# ---------- 运行阶段 ----------
FROM nginx:1.27-alpine

ARG APP_NAME=frontend

# SPA 路由回退 + 静态资源缓存
RUN printf 'server {\n\
  listen 80;\n\
  server_name _;\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
  charset utf-8;\n\
  gzip on;\n\
  gzip_types text/css application/javascript application/json image/svg+xml;\n\
  gzip_min_length 1024;\n\
  location / {\n\
    try_files $uri $uri/ /index.html;\n\
  }\n\
  location ~* \\.(js|css|png|jpg|jpeg|gif|svg|woff2?|ttf)$ {\n\
    expires 30d;\n\
    add_header Cache-Control "public, immutable";\n\
    access_log off;\n\
  }\n\
  # index.html 不缓存，保证发版后能立即拿到新资源清单\n\
  location = /index.html {\n\
    add_header Cache-Control "no-cache, no-store, must-revalidate";\n\
  }\n\
  location = /healthz {\n\
    access_log off;\n\
    return 200 "ok\\n";\n\
  }\n\
  server_tokens off;\n\
  add_header X-Content-Type-Options nosniff;\n\
}\n' > /etc/nginx/conf.d/default.conf

COPY --from=builder /build/dist /usr/share/nginx/html

LABEL org.opencontainers.image.title="AIDS ${APP_NAME}"

EXPOSE 80

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
