#!/usr/bin/env bash
# S7 结构性防御：禁止提交 .env 与密钥文件
#
# 由 .pre-commit-config.yaml 调用。
# 作为独立脚本而非内联 entry，是为了 YAML 可解析 + 便于本地单独测试。
set -euo pipefail

exit_code=0

for f in "$@"; do
  base="$(basename "$f")"

  # 模板文件是允许提交的
  case "$base" in
    *.example) continue ;;
  esac

  case "$base" in
    .env|*.env|*.pem|*.key|*.p12|*.keystore)
      echo "禁止提交敏感文件: $f"
      echo "  提示：本地配置请用 .env.example（模板）+ .env（本地，已 gitignore）"
      exit_code=1
      ;;
  esac
done

exit "$exit_code"
