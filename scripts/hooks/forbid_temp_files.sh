#!/usr/bin/env bash
# 禁止提交临时/测试残留文件
#
# 由 .pre-commit-config.yaml 调用，pass_filenames: true。
# 背景：bysj 时期仓库里留下了 _PRDfix.md / _wtest2 / *_backup 等测试残留，
#       这些文件本身无害，但会污染仓库并让"这个文件到底有没有用"变成考古题。
#
# ★ 白名单（必须显式登记，不接受通配）★
#   本 hook 用 `_*` 作为"下划线开头即残留"的粗判，但有两类下划线文件是合法的：
#     __init__.py                    —— 包标识，任何目录都必须入库
#     tests/contract/_doc_parser.py  —— 契约测试公共解析器（命名以 _ 开头是刻意的）
#   历史教训：没有白名单时，本 hook 会把上面两个文件判为"残留文件"，
#   于是**任何对它们的改动都无法提交**——护栏误伤了自己仓库里合法的东西，
#   这是比自己没生效更隐蔽的失效（开发者的"绕过"方式往往是 --no-verify，
#   而一旦养成这个习惯，整个门禁都废了）。
#   回归测试：tests/invariants/test_guard_selfcheck.py
set -euo pipefail

exit_code=0

_is_whitelisted() {
  local path="$1" base="$2"
  [ "$base" = "__init__.py" ] && return 0
  [ "$path" = "tests/contract/_doc_parser.py" ] && return 0
  return 1
}

for f in "$@"; do
  base="$(basename "$f")"

  case "$base" in
    _*|*_wtest*|*_probe*|*.bak|*.orig|*~|*.tmp|*.rej)
      if _is_whitelisted "$f" "$base"; then
        continue
      fi
      echo "禁止提交临时/残留文件: $f"
      echo "  提示：临时验证请用 /tmp，或加入 .gitignore"
      echo "       确属合法文件，请在本 hook 的白名单里显式登记并说明理由"
      exit_code=1
      ;;
  esac
done

exit "$exit_code"
