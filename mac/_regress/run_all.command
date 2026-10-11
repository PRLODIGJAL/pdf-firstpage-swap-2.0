#!/bin/bash
# 改完代码后跑：核心自测 → 界面端到端 → 记住上次选择 → 真实语料
cd "$(dirname "$0")/.." || exit 1
PY=./.venv/bin/python
fail=0

echo "════ 1/4 核心自测（临时目录造语料）"
$PY -B _regress/selftest.py || fail=1

echo
echo "════ 2/4 界面驱动端到端（真点按钮）"
$PY -B _regress/verify_gui_e2e.py || fail=1

echo
echo "════ 3/4 记住上次选择"
$PY -B _regress/verify_config_memory.py || fail=1

echo
echo "════ 4/4 真实语料（桌面上的 WI 原件，只读）"
$PY -B _regress/verify_real_corpus.py || fail=1

echo
[ $fail -eq 0 ] && echo "全部通过 ✓" || echo "有失败项，别急着交付 ✗"
exit $fail
