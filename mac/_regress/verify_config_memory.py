#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「记住上次选择」回归 —— 真建界面对象，读回填了什么、落盘了什么。

⚠️ CONFIG_PATH 一律重定向到 /tmp：v1 差点用测试路径覆盖掉用户真实记录。
跑法：.venv/bin/python -B _regress/verify_config_memory.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import seq_app as A  # noqa: E402
import seq_core as C  # noqa: E402

REAL_CONFIG_PATH = A.CONFIG_PATH       # 重定向之前先留一份，用来验名字
TMP = Path(tempfile.gettempdir()) / "pdfseq_cfg"
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)
A.CONFIG_PATH = TMP / "config.json"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK ' if cond else 'NG '} {name}" + (f"  <{detail}>" if detail and not cond else ""))


def new_app(root: tk.Tk) -> A.App:
    app = A.App(root)
    root.update()
    return app


def main() -> int:
    print("[1] 存与取")
    root = tk.Tk()
    root.withdraw()
    app = new_app(root)
    src = TMP / "原件"
    src.mkdir()
    (TMP / "输出").mkdir()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from selftest import build_pdf  # noqa: E402
    build_pdf(TMP / "扫描件.pdf", [(595, 842, "SCAN-1")])
    app.vars["src"].set(str(src))
    app.vars["scan"].set(str(TMP / "扫描件.pdf"))
    app.vars["out"].set(str(TMP / "输出"))
    app.vars["reverse"].set(True)
    app.vars["scale"].set(False)
    app.vars["recursive"].set(True)
    app.vars["suffix"].set("_已签字首页")
    root.update()
    app._save_config()
    check("记录文件写出来了", A.CONFIG_PATH.is_file())
    data = json.loads(A.CONFIG_PATH.read_text(encoding="utf-8"))
    check("三个路径都在", data.get("src") == str(src)
          and data.get("scan", "").endswith("扫描件.pdf")
          and data.get("out", "").endswith("输出"), str(data))
    check("选项也存了", data.get("reverse") is True and data.get("scale") is False
          and data.get("recursive") is True and data.get("suffix") == "_已签字首页", str(data))
    check("记了配置版本号", data.get("_version") == A.CONFIG_VERSION, str(data.get("_version")))
    check("记录了窗口大小", isinstance(data.get("window"), list) and len(data["window"]) == 2,
          str(data.get("window")))
    app.on_close()
    try:
        root.destroy()
    except tk.TclError:
        pass

    print("\n[2] 下次打开自动填回")
    root2 = tk.Tk()
    root2.withdraw()
    app2 = new_app(root2)
    check("原件文件夹填回来了", app2.vars["src"].get() == str(src))
    check("扫描件填回来了", app2.vars["scan"].get().endswith("扫描件.pdf"))
    check("输出文件夹填回来了", app2.vars["out"].get().endswith("输出"))
    check("倒序勾回来了", app2.vars["reverse"].get() is True)
    check("缩放关的状态也回来了", app2.vars["scale"].get() is False)
    check("后缀回来了", app2.vars["suffix"].get() == "_已签字首页")
    check("状态栏说了恢复了什么", "已自动填好" in app2.status.cget("text"),
          app2.status.cget("text"))

    print("\n[3] 记住了但文件夹已经不在")
    (TMP / "原件").rmdir()
    A.CONFIG_PATH.write_text(json.dumps(
        {"src": str(TMP / "原件"), "scan": str(TMP / "扫描件.pdf"), "out": ""},
        ensure_ascii=False), encoding="utf-8")
    app3 = new_app(root2)
    check("照填不清空，但状态栏点名提示",
          "已经不存在" in app3.status.cget("text") and app3.vars["src"].get() == str(TMP / "原件"),
          app3.status.cget("text"))

    print("\n[4] 记录文件坏了 / 不是 dict")
    A.CONFIG_PATH.write_text("{ 这不是 json", encoding="utf-8")
    app4 = new_app(root2)
    check("读不出来只提示，不崩", "读不出来" in app4.status.cget("text")
          and app4.vars["src"].get() == "", app4.status.cget("text"))
    A.CONFIG_PATH.write_text("[1,2,3]", encoding="utf-8")
    app5 = new_app(root2)
    check("不是 dict 也照样能开", app5.vars["src"].get() == "")

    print("\n[5] 不认识的键 / 类型对不上")
    A.CONFIG_PATH.write_text(json.dumps(
        {"src": str(TMP), "reverse": "不是布尔", "未来才会有的键": 123},
        ensure_ascii=False), encoding="utf-8")
    app6 = new_app(root2)
    check("不认识的键忽略，版本升级不炸", app6.vars["src"].get() == str(TMP))
    check("类型对不上的那项跳过", app6.vars["reverse"].get() is False)

    print("\n[6] 原子写失败 → 退一步直接写，且不留 .tmp")
    real_replace = os.replace
    calls = {"n": 0}

    def boom(a, b):
        calls["n"] += 1
        raise OSError("模拟安全软件拦住重命名")

    os.replace = boom
    try:
        app6.vars["src"].set(str(TMP / "回退"))
        root2.update()
        app6._save_config()
    finally:
        os.replace = real_replace
    check("退路把配置存下来了",
          A.CONFIG_PATH.is_file() and "回退" in A.CONFIG_PATH.read_text(encoding="utf-8"),
          A.CONFIG_PATH.read_text(encoding="utf-8") if A.CONFIG_PATH.is_file() else "没写")
    check("没有留下 .tmp 垃圾",
          not list(TMP.glob("config.json.tmp")), str([p.name for p in TMP.glob("*")]))

    print("\n[7] 记忆照旧，但不再有「忘记」按钮（UI 精简后）")
    check("已删掉「忘记记住的文件夹」入口", not hasattr(A.App, "on_forget"))
    check("路径照常被记住（删按钮不影响持久化）",
          bool(app6.vars["src"].get())
          and "回退" in A.CONFIG_PATH.read_text(encoding="utf-8"),
          str({k: app6.vars[k].get() for k in A.CONFIG_KEEP_KEYS}))
    check("记录文件仍在", A.CONFIG_PATH.exists())

    print("\n[8] 和 v1 各记各的")
    check("v2 的记录文件名和 v1 不同（两边各记各的）",
          REAL_CONFIG_PATH.name == ".pdf_batch_cover_swap.json"
          and "firstpage" not in REAL_CONFIG_PATH.name, REAL_CONFIG_PATH.name)

    print("\n[9] 窗口大小只恢复合理范围")
    check("太小的尺寸不恢复（免得开出一个看不见的窗）",
          A.fit_geometry([10, 10], 1920, 1080) is None, str(A.fit_geometry([10, 10], 1920, 1080)))
    check("比屏幕还大也不恢复", A.fit_geometry([5000, 3000], 1920, 1080) is None)
    check("正常尺寸照常恢复", A.fit_geometry([1100, 700], 1920, 1080) == "1100x700")
    check("不是两个数就不认", A.fit_geometry([1100], 1920, 1080) is None
          and A.fit_geometry("1100x700", 1920, 1080) is None)
    check("类型对不上不认", A.fit_geometry([1100, "abc"], 1920, 1080) is None)

    print("\n[10] 初始窗口：够大且不超出屏幕")
    check("大屏上用默认尺寸",
          A.default_geometry(1920, 1080) == f"{A.GEOM_DEFAULT[0]}x{A.GEOM_DEFAULT[1]}",
          A.default_geometry(1920, 1080))
    check("矮屏笔记本上要收一收高度（别被任务栏切掉）",
          A.default_geometry(1366, 768) == "1060x700", A.default_geometry(1366, 768))
    for sw, sh in ((1920, 1080), (1440, 900), (1366, 768), (1280, 800)):
        w, h = (int(x) for x in A.default_geometry(sw, sh).split("x"))
        check(f"屏幕 {sw}×{sh} 上开出的窗口不超出屏幕", w <= sw and h <= sh,
              f"{w}x{h}")

    for r in (root2,):
        try:
            r.destroy()
        except tk.TclError:
            pass
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for n in FAIL:
        print("  失败：" + n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
