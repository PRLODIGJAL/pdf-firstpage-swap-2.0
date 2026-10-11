#!/bin/bash
# macOS 双击启动（首次会自动准备环境；逻辑与 Windows 侧共用 setup_env.py）
#
# 这里只干一件事：找到本机 python3，交给 setup_env.py。
# 所有中文提示、环境判断、按需安装都在那个 .py 里 —— 两边口径一致，
# 修一次两边都好。
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo "没找到 python3。请先安装 Python 3.9 或更高版本："
  echo "  https://www.python.org/downloads/macos/"
  echo
  read -r -p "按回车键关闭…"
  exit 1
fi

"$PY" -B setup_env.py "$@"
rc=$?
if [ $rc -ne 0 ]; then
  echo
  read -r -p "出错了（退出码 $rc）。按回车键关闭…"
fi
exit $rc
