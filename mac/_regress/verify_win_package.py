#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 交付包验收：静态检查 + 把包拷到临时目录真跑一遍环境流程。

跑法：.venv/bin/python -B _regress/verify_win_package.py
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent
WIN = Path("/Users/langzi/Desktop/PDF首页替换2.0_Windows版")
BODY = ("seq_core.py", "seq_app.py", "setup_env.py")
# 启动器的名字不写死：Mac 侧有什么 .command 就拉黑什么 —— 以后再改名不用回来改。
# LEGACY 是历史上用过的名字，改名后万一有残留也要报出来。
LEGACY_MAC_LAUNCHERS = ("run_macos.command",)
FORBIDDEN = ("_regress", "selftest.py", "__pycache__", ".venv", ".DS_Store") \
            + tuple(sorted({p.name for p in DEV.glob("*.command")} | set(LEGACY_MAC_LAUNCHERS)))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK ' if cond else 'NG '} {name}" + (f"  <{detail}>" if detail and not cond else ""))


def md5(p: Path) -> str:
    import hashlib

    return hashlib.md5(p.read_bytes()).hexdigest()


def third_party_imports(py: Path) -> set[str]:
    """程序实际 import 的**第三方**库（用来核对 setup_env 的依赖清单）。

    同目录下自己的模块（seq_core 之类）不算第三方 —— 漏掉这一步就会要求
    setup_env 去 pip install 一个根本不存在的包。
    """
    tree = ast.parse(py.read_text(encoding="utf-8"))
    std = set(sys.stdlib_module_names)
    local = {p.stem for p in DEV.glob("*.py")}
    out: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        for top in names:
            if top not in std and top not in local:
                out.add(top)
    return out


def main() -> int:
    print("【静态】交付包本身")
    check("交付包存在", WIN.is_dir(), str(WIN))

    print("\n  · 程序本体逐字节一致")
    for name in BODY:
        check(f"{name} 与开发目录一致",
              (WIN / name).is_file() and md5(WIN / name) == md5(DEV / name))

    print("\n  · .bat 的编码与结构")
    bats = sorted(WIN.glob("*.bat"))
    check("包里有 .bat 启动器", len(bats) >= 1, str([b.name for b in bats]))
    for b in bats:
        raw = b.read_bytes()
        check(f"{b.name} 是 CRLF 行尾",
              raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"))
        check(f"{b.name} 没有 BOM", not raw.startswith(b"\xef\xbb\xbf"))
        bad = [ch for ch in raw.decode("latin-1") if ord(ch) > 127]
        check(f"{b.name} 命令行纯 ASCII", not bad, str(sorted(set(bad))[:5]))
        lines = raw.decode("ascii").splitlines()
        labels = {m.group(1).lower() for ln in lines
                  if (m := re.match(r"^\s*:([A-Za-z0-9_]+)", ln))}
        gotos = {m.group(1).lower() for ln in lines
                 if (m := re.search(r"goto\s+(\w+)", ln, re.I))}
        check(f"{b.name} 的 goto 标签都存在", not (gotos - labels), str(sorted(gotos - labels)))
    check("没有把 Mac 的启动器混进来", not list(WIN.glob("*.command")))

    print("\n  · 说明文件")
    txt = WIN / "使用说明.txt"
    check("使用说明.txt 存在", txt.is_file())
    if txt.is_file():
        raw = txt.read_bytes()
        check("说明文件是 utf-8-sig（记事本不乱码）", raw.startswith(b"\xef\xbb\xbf"))
        body = raw.decode("utf-8-sig")
        check("开头写明本文件夹是 Windows 专用", "Windows 专用" in body)
        check("版本号是 2.0", "v2.0" in body)
        check("写清了「数量对不上就整批不执行」", "整批" in body and "差几份" in body)
        check("写了倒序开关", "倒序" in body)
        check("说了更新不用重装环境", "不用" in body and ".venv" in body)

    print("\n  · 不该出现的文件")
    for n in FORBIDDEN:
        check(f"没有混进 {n}", not (WIN / n).exists())

    print("\n  · setup_env.py 与程序的一致性")
    se = WIN / "setup_env.py"
    check("setup_env.py 在包里", se.is_file())
    if se.is_file():
        src = se.read_text(encoding="utf-8")
        used: set[str] = set()
        for name in ("seq_core.py", "seq_app.py"):
            used |= third_party_imports(DEV / name)
        need = src.lower()
        for lib in sorted(used):
            check(f"依赖清单含 {lib}", lib.lower() in need, f"程序实际用到 {sorted(used)}")
        m = re.search(r"NEED\s*=\s*\(([^)]*)\)", src)
        check("能读出依赖清单", bool(m))
        if m:
            check("pypdf 版本下限不低于 3.16（用到 Transformation 接口）",
                  "3.16" in m.group(1), m.group(1))
        check("入口文件名指向包内真实文件", "seq_app.py" in src and (WIN / "seq_app.py").is_file())

    print("\n【功能】把包拷到临时目录真跑一遍")
    tmp = Path(tempfile.gettempdir()) / "pdfseq_win_check"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    work = tmp / "包"
    shutil.copytree(WIN, work, ignore=shutil.ignore_patterns(".venv", ".DS_Store"))

    py = sys.executable
    r = subprocess.run([py, "-B", "setup_env.py", "--check"], cwd=work,
                       capture_output=True, text=True)
    check("全新目录自检：退出码 1", r.returncode == 1, str(r.returncode))
    check("并说明是「没装过」而不是含糊报错",
          "不存在" in (r.stdout or "") and "不可用" in (r.stdout or ""), (r.stdout or "")[-120:])

    r = subprocess.run([py, "-B", "setup_env.py", "--setup"], cwd=work,
                       capture_output=True, text=True)
    check("--setup 首次安装成功", r.returncode == 0, (r.stdout or "")[-300:])
    check("确实建了 .venv", (work / ".venv").is_dir())
    vpy = work / ".venv" / "bin" / "python"
    check("环境里的解释器存在", vpy.exists())

    r = subprocess.run([py, "-B", "setup_env.py", "--check"], cwd=work,
                       capture_output=True, text=True)
    check("装完再自检：可用", r.returncode == 0 and "可用" in (r.stdout or ""),
          (r.stdout or "")[-200:])
    check("自检报出依赖版本", "pypdf" in (r.stdout or ""), (r.stdout or "")[-120:])

    r = subprocess.run([py, "-B", "setup_env.py", "--check"], cwd=work,
                       capture_output=True, text=True)
    check("第二次仍是「可用」快路径", r.returncode == 0 and "可用" in (r.stdout or ""))

    # 解释器断链（换机 / 原 Python 卸载）能被识别
    if vpy.exists():
        bak = vpy.with_suffix(".bak")
        shutil.copy2(vpy, bak)
        vpy.unlink()
        r = subprocess.run([py, "-B", "setup_env.py", "--check"], cwd=work,
                           capture_output=True, text=True)
        check("解释器断链时自检能识别出不可用", r.returncode == 1, (r.stdout or "")[-200:])
        shutil.copy2(bak, vpy)

    # 用户自己放了个普通 .venv 文件夹 —— 绝不能误删
    tmp2 = tmp / "包2"
    shutil.copytree(WIN, tmp2, ignore=shutil.ignore_patterns(".venv", ".DS_Store"))
    (tmp2 / ".venv").mkdir()
    (tmp2 / ".venv" / "这是我的东西.txt").write_text("别删", encoding="utf-8")
    r = subprocess.run([py, "-B", "setup_env.py", "--setup"], cwd=tmp2,
                       capture_output=True, text=True)
    check("拒绝删除不像 venv 的 .venv 目录", "不像虚拟环境" in (r.stdout or ""),
          (r.stdout or "")[-200:])
    check("用户文件原封不动", (tmp2 / ".venv" / "这是我的东西.txt").is_file())

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for n in FAIL:
        print("  失败：" + n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
