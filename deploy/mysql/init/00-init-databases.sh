#!/bin/bash
# =============================================================
# MySQL 初始化入口 (DEP-04)
#
# 为什么需要这个脚本:
#   docker-entrypoint-initdb.d 会按**文件名字典序**执行目录下所有 .sql 文件。
#   而 docs/sql/ 下的文件名按字典序排是:
#       mock_schema.sql  →  seed.sql  →  schema.sql
#   seed 会先于 schema 执行, 必然失败(表还不存在)。
#   因此不直接挂载 docs/sql, 而由本脚本显式按依赖顺序执行。
#
# 执行顺序:
#   1. schema.sql       主业务库 (38 表)
#   2. mock_schema.sql  Mock 服务库 (7 表)
#   3. seed.sql         种子数据 (依赖前两者)
# =============================================================
set -euo pipefail

SQL_DIR=/docker-entrypoint-initdb.d/sql

echo "▶ [1/3] 初始化主业务库 aids_shop ..."
mysql --default-character-set=utf8mb4 -uroot -p"${MYSQL_ROOT_PASSWORD}" < "${SQL_DIR}/schema.sql"
echo "  ✅ aids_shop 完成"

echo "▶ [2/3] 初始化 Mock 服务库 aids_mock ..."
mysql --default-character-set=utf8mb4 -uroot -p"${MYSQL_ROOT_PASSWORD}" < "${SQL_DIR}/mock_schema.sql"
echo "  ✅ aids_mock 完成"

echo "▶ [3/3] 灌入种子数据 ..."
mysql --default-character-set=utf8mb4 -uroot -p"${MYSQL_ROOT_PASSWORD}" < "${SQL_DIR}/seed.sql"
echo "  ✅ 种子数据完成"

echo ""
echo "▶ 初始化结果自检:"
mysql --default-character-set=utf8mb4 -uroot -p"${MYSQL_ROOT_PASSWORD}" -t -e "
SELECT TABLE_SCHEMA AS 库, COUNT(*) AS 表数
  FROM information_schema.TABLES
 WHERE TABLE_SCHEMA IN ('aids_shop','aids_mock')
 GROUP BY TABLE_SCHEMA;
SELECT CONCAT(COUNT(*), ' 个 SKU') AS 种子商品 FROM aids_shop.biz_sku;
SELECT username AS 后台账号 FROM aids_shop.sys_user;
"

echo ""
echo "✅ 数据库初始化全部完成"
