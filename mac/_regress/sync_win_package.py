#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Mac 版（开发目录）的程序本体同步到 Windows 交付文件夹，并做规范化。

和第一版同一个套路：
  · Windows 包里的 seq_core.py / seq_app.py / setup_env.py 必须与开发目录
    **逐字节一致**，否则「我这边改了、你那边没改」这种漂移根本看不出来。
  · 规范化也在这里做：.bat 要 CRLF + 纯 ASCII + 无 BOM；使用说明要 utf-8-sig。
  · **幂等**：内容一致就一个字节都不写（省无谓写入，也避开环境对覆盖写的拦截）。

⚠️ `使用说明.txt` 只存在于交付包，开发目录没有源文件 —— 它的内容要手改，
   改了界面文案务必同步改这一份。

用法：
    .venv/bin/python -B _regress/sync_win_package.py [Windows包路径]
"""
import hashlib
import re
import shutil
import sys
import time
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent
WIN = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/Users/langzi/Desktop/PDF首页替换2.0_Windows版")
BACKUP_ROOT = DEV / "_regress" / "_win_prev"

BODY = ("seq_core.py", "seq_app.py", "setup_env.py")
SIG_TXT = ("使用说明.txt",)
# 启动器的名字**不写死**：Mac 侧有什么 .command 就拉黑什么，交付包里有什么 .bat
# 就规范化什么。以后启动器再改名（1.0 / 2.0 / 3.0 …），这两个脚本不用回来改。
# LEGACY 是历史上用过的名字 —— 改名后万一有残留，仍然要报出来。
LEGACY_MAC_LAUNCHERS = ("run_macos.command",)
MAC_LAUNCHERS = tuple(sorted({p.name for p in DEV.glob("*.command")} | set(LEGACY_MAC_LAUNCHERS)))
FORBIDDEN = ("_regress", "selftest.py", "__pycache__", ".venv", ".DS_Store") + MAC_LAUNCHERS


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def to_crlf_ascii(p: Path) -> None:
    """.bat 规范化为 CRLF + 纯 ASCII + 无 BOM（只修，不改内容）。幂等。"""
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
        print(f"  去掉 BOM：{p.name}")
    bad = sorted({ch for ch in raw.decode("latin-1") if ord(ch) > 127})
    if bad:
        raise SystemExit(f"[失败] {p.name} 里有非 ASCII 字符：{bad}；"
                         "中文提示应该写进 Python，不要写进 .bat")
    text = raw.decode("ascii").replace("\r\n", "\n").replace("\r", "\n")
    want = text.replace("\n", "\r\n").encode("ascii")
    if want == p.read_bytes():
        print(f"  已是 CRLF + 纯 ASCII，跳过：{p.name}")
        return
    p.write_bytes(want)


def to_utf8_sig(p: Path) -> None:
    """说明文件用 utf-8-sig（带 BOM），Windows 记事本才认。幂等。"""
    want = p.read_text(encoding="utf-8-sig").encode("utf-8-sig")
    if want == p.read_bytes():
        print(f"  已是 utf-8-sig，跳过：{p.name}")
        return
    p.write_bytes(want)


def check_bat_labels(p: Path) -> None:
    """静态检查 goto 目标是否都存在 —— .bat 里最容易犯的错。"""
    lines = p.read_text(encoding="ascii").splitlines()
    labels = {m.group(1).lower() for ln in lines
              if (m := re.match(r"^\s*:([A-Za-z0-9_]+)", ln))}
    gotos = {m.group(1).lower() for ln in lines
             if (m := re.search(r"goto\s+(\w+)", ln, re.I))}
    missing = gotos - labels
    if missing:
        raise SystemExit(f"[失败] {p.name} 里有 goto 找不到的标签：{sorted(missing)}")


def main() -> int:
    if not WIN.is_dir():
        raise SystemExit(f"[失败] Windows 包目录不存在：{WIN}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    for name in BODY:
        tgt = WIN / name
        if tgt.is_file() and md5(tgt) != md5(DEV / name):
            bk = BACKUP_ROOT / stamp
            bk.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tgt, bk / name)
            print(f"  旧版已备份：_regress/_win_prev/{stamp}/{name}")

    for name in BODY:
        src = DEV / name
        if not src.is_file():
            raise SystemExit(f"[失败] 开发目录里找不到 {name}")
        if (WIN / name).is_file() and md5(WIN / name) == md5(src):
            print(f"  已一致，跳过：{name}")
            continue
        shutil.copy2(src, WIN / name)
        print(f"  已更新：{name}")

    # ── 2) 规范化文本 ──────────────────────────────────────────────────────
    # .bat 的名字不写死：交付包里有什么就规范化什么（「重装运行环境.bat」也在内）。
    for p in sorted(WIN.glob("*.bat")):
        to_crlf_ascii(p)
        check_bat_labels(p)
    for name in SIG_TXT:
        p = WIN / name
        if p.is_file():
            to_utf8_sig(p)

    print("\n逐字节复核（开发目录 vs Windows 包）：")
    bad = []
    for name in BODY:
        a, b = md5(DEV / name), md5(WIN / name)
        print(f"  {'SAME' if a == b else 'DIFF'}  {name}  {b[:12]}")
        if a != b:
            bad.append(name)

    leaked = [n for n in FORBIDDEN if (WIN / n).exists()]
    if leaked:
        print(f"\n  [!] 交付包里混进了开发用文件：{leaked}")
        bad += leaked

    print("\n包里现有文件：", "、".join(sorted(p.name for p in WIN.iterdir())))
    if bad:
        print("\n[失败] 有需要处理的问题：" + "、".join(bad))
        return 1
    print("\n同步完成 ✓（.bat 已 CRLF + 纯 ASCII，说明文件已 utf-8-sig）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
