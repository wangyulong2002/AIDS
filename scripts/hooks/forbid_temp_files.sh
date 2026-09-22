#!/usr/bin/env bash
# 禁止提交临时/测试残留文件
#
# 由 .pre-commit-config.yaml 调用，pass_filenames: true。
# 背景：bysj 时期仓库里留下了 _PRDfix.md / _wtest2 / *_backup 等测试残留，
#       这些文件本身无害，但会污染仓库并让"这个文件到底有没有用"变成考古题。
set -euo pipefail

exit_code=0

for f in "$@"; do
  base="$(basename "$f")"

  case "$base" in
    _*|*_wtest*|*_probe*|*.bak|*.orig|*~|*.tmp|*.rej)
      echo "禁止提交临时/残留文件: $f"
      echo "  提示：临时验证请用 /tmp，或加入 .gitignore"
      exit_code=1
      ;;
  esac
done

exit "$exit_code"
