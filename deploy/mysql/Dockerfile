# =============================================================
# MySQL 镜像（内置初始化脚本）
#
# 为什么打进镜像而不是 bind mount:
#   Docker Desktop for WSL2 存在 bind mount 故障（挂载目录在容器内为空），
#   而 build context 的 COPY 正常工作。因此把 SQL 与初始化脚本构建期打入镜像，
#   运行时零挂载，同时避免对宿主文件系统的实时依赖。
#
# 【构建上下文必须是项目根目录】因为 SQL 文件在 docs/sql/，不在 deploy/ 下。
# 构建命令（在项目根目录执行）:
#   docker compose -f deploy/docker-compose.yml build mysql
#   （compose 中已配置 context: .. 与 dockerfile: deploy/mysql/Dockerfile）
# =============================================================
FROM mysql:8.4

# 初始化脚本：显式按 schema → mock_schema → seed 顺序执行
# （entrypoint 按文件名字典序执行会得到错误顺序，详见 00-init-databases.sh）
COPY deploy/mysql/init/00-init-databases.sh /docker-entrypoint-initdb.d/00-init-databases.sh
COPY docs/sql/ /docker-entrypoint-initdb.d/sql/
