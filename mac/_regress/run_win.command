#!/bin/bash
# 交付 Windows 包：同步程序本体 → 规范化 .bat/说明 → 静态+功能验收
cd "$(dirname "$0")/.." || exit 1
PY=./.venv/bin/python
fail=0

echo "════ 1/2 同步到 Windows 包（同步前会把旧版备份到 _regress/_win_prev/）"
$PY -B _regress/sync_win_package.py || fail=1

echo
echo "════ 2/2 Windows 包验收（静态 + 在临时目录真跑环境流程）"
$PY -B -u _regress/verify_win_package.py || fail=1

echo
[ $fail -eq 0 ] && echo "Windows 包就绪 ✓" || echo "有问题，别急着拷给别人 ✗"
echo "提醒：真正的「双击 .bat 能不能开」只能在 Windows 上点一次才算数。"
exit $fail
