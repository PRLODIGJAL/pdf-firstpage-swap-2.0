#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面驱动端到端 —— 真建界面对象、真点按钮，验证 v2 的顺序配对流程。

⚠️ 必须先把 CONFIG_PATH 指到 /tmp 再建界面，否则会把用户真实的记录覆盖掉。
跑法：.venv/bin/python -B _regress/verify_gui_e2e.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.gettempdir()) / "pdfseq_gui_e2e"
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)

import seq_app as A  # noqa: E402
import seq_core as C  # noqa: E402

A.CONFIG_PATH = TMP / "config.json"          # 别碰用户真实记录
sys.path.insert(0, str(ROOT / "_regress"))
from selftest import build_pdf, page_texts, page_sizes  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []
ASK_YES = True
WARNED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK ' if cond else 'NG '} {name}" + (f"  <{detail}>" if detail and not cond else ""))


def pump(root: tk.Tk, done, timeout: float = 8.0) -> bool:
    """把 Tk 事件循环泵起来，等后台线程回调完成。"""
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if done():
            return True
        time.sleep(0.02)
    return False


# ── 假的消息框：不阻塞、并记录被调用的内容 ──────────────────────────────
def fake_askokcancel(title, msg, **kw):
    return ASK_YES


def fake_showinfo(title, msg, **kw):
    return None


def fake_showwarning(title, msg, **kw):
    WARNED.append(str(msg))
    return None


A.messagebox.askokcancel = fake_askokcancel
A.messagebox.showinfo = fake_showinfo
A.messagebox.showwarning = fake_showwarning


def make_corpus(n_ori: int, n_scan: int, size=(595.0, 842.0)) -> tuple[Path, Path, Path]:
    d = TMP / f"corpus_{n_ori}_{n_scan}"
    if d.exists():
        shutil.rmtree(d)
    src = d / "原件"
    src.mkdir(parents=True)
    for i in range(1, n_ori + 1):
        build_pdf(src / f"{i:03d}.pdf",
                  [(size[0], size[1], f"ORI{i}-P1"), (size[0], size[1], f"ORI{i}-P2")])
    scan = d / "扫描件_签字版.pdf"
    build_pdf(scan, [(size[0], size[1], f"SCAN-{k}") for k in range(1, n_scan + 1)])
    out = d / "输出"
    return src, scan, out


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    app = A.App(root)

    print("[1] 默认布局")
    labels = [w.label.cget("text") for w in app.rows.values()]
    check("文件夹区三行：原件文件夹 / 扫描件 PDF / 输出文件夹",
          labels == ["原件文件夹", "扫描件 PDF", "输出文件夹"], str(labels))
    check("默认不倒序", app.vars["reverse"].get() is False)
    check("默认开缩放", app.vars["scale"].get() is True)
    check("默认不覆盖", app.vars["overwrite"].get() is False)
    check("默认不递归", app.vars["recursive"].get() is False)
    check("开始时「开始替换」是禁用的", str(app.btn_run["state"]) == "disabled")
    check("版本号是 2.0", C.TOOL_VERSION == "2.0", C.TOOL_VERSION)

    print("\n[1b] 默认窗口放得下 —— 不用最大化也看得全")
    root.update_idletasks()
    need_w, need_h = app.winfo_reqwidth(), app.winfo_reqheight()
    check(f"界面只要 {need_w}px 宽 ≤ 默认窗宽 {A.GEOM_DEFAULT[0]}",
          need_w <= A.GEOM_DEFAULT[0], f"要 {need_w}px")
    check(f"界面只要 {need_h}px 高 ≤ 默认窗高 {A.GEOM_DEFAULT[1]}",
          need_h <= A.GEOM_DEFAULT[1], f"要 {need_h}px")
    check("①②③ 三个模块各占一行（2 列 × 3 行，不是挤在一行）",
          app.mod_hint_row.grid_size() == (2, 3), str(app.mod_hint_row.grid_size()))
    tags = [m.tag for m in A.MODULE_ROWS]
    check("三个模块的编号不重复（①②③ 各一个）",
          len(tags) == 3 and len({t[0] for t in tags}) == 3, str(tags))
    check("界面上不再出现两个「①」（上面两块不编号）",
          not any("①" in w.cget("text") for w in app.winfo_children()
                  if w.winfo_class() == "TLabelframe"))

    print("\n[2] 正常配对（3 份原件 / 3 页扫描件）")
    src, scan, out = make_corpus(3, 3)
    app.vars["src"].set(str(src))
    app.vars["scan"].set(str(scan))
    root.update()
    check("输出文件夹自动补默认值", app.vars["out"].get().endswith(C.AUTO_OUT_NAME),
          app.vars["out"].get())
    app.on_preview()
    check("预览完成", pump(root, lambda: not app.busy))
    check("没被阻断", app.plan is not None and not app.plan.blocked,
          app.plan.block_reason if app.plan else "")
    rows = app.tree.get_children()
    check("表格 3 行", len(rows) == 3, str(len(rows)))
    vals = [app.tree.item(r, "values") for r in rows]
    # Treeview 取回来的值一律是字符串
    check("扫描页列 = 1、2、3",
          [v[4] for v in vals] == ["1", "2", "3"], str([v[4] for v in vals]))
    check("原件列 = 001、002、003",
          [v[1] for v in vals] == ["001.pdf", "002.pdf", "003.pdf"], str([v[1] for v in vals]))
    check("「开始替换」已启用", str(app.btn_run["state"]) == "normal")

    print("\n[3] 模块①：页序按钮（正序 ⇄ 倒序，一键来回切）")
    check("② 选项里已经没有「倒序」勾选框了",
          not any(k == "reverse" for k, _l, _t in A.CHECK_SPECS))
    check("按钮初始显示「页序：正序」", app.btn_order.cget("text") == "页序：正序",
          app.btn_order.cget("text"))
    app.on_flip_order()
    root.update()
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("点一下 → 扫描页列 = 3、2、1",
          [v[4] for v in vals] == ["3", "2", "1"], str([v[4] for v in vals]))
    check("按钮文字跟着变成「页序：倒序」", app.btn_order.cget("text") == "页序：倒序",
          app.btn_order.cget("text"))
    app.on_flip_order()
    root.update()
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("再点一下回到正序（来回点是幂等的）",
          [v[4] for v in vals] == ["1", "2", "3"], str([v[4] for v in vals]))
    check("按钮文字回到「页序：正序」", app.btn_order.cget("text") == "页序：正序")

    print("\n[4] 模块②：页码对应（行不动，只换用哪一页）")
    app.tree.selection_set(app.tree.get_children()[0])
    app._sync_adjust_buttons()
    check("选中行后页码按钮可用", str(app.btn_up["state"]) == "normal")
    app._swap_page(1)
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("对调后原件顺序一动不动（名称序是唯一的排队依据）",
          [v[1] for v in vals] == ["001.pdf", "002.pdf", "003.pdf"], str([v[1] for v in vals]))
    check("001 用第 2 页、002 用第 1 页",
          [v[4] for v in vals] == ["2", "1", "3"], str([v[4] for v in vals]))
    check("一页不会被两行同时用", len({v[4] for v in vals}) == 3, str([v[4] for v in vals]))
    app._swap_page(-1)          # 选中行已跟着走到第 2 行，往上换回去
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("再换回来", [v[4] for v in vals] == ["1", "2", "3"], str([v[4] for v in vals]))

    # 直接指定页码：占用别人的页 → 自动对调
    app.tree.selection_set(app.tree.get_children()[3 - 1])
    changed, note = C.assign_page(app.plan, 3, 1)
    app._apply_rotations()
    app._reselect_refresh()
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("把第 3 行改成用第 1 页 → 第 1 行自动换成第 3 页",
          [v[4] for v in vals] == ["3", "2", "1"], str([v[4] for v in vals]))
    check("对调说明里点名了被换的那份原件", "001.pdf" in note, note)
    C.assign_page(app.plan, 3, 3)
    app._apply_rotations()
    app._reselect_refresh()

    print("\n[5] 执行替换")
    # 输出目录是自动填的（工作文件夹/输出_已替换），以界面里实际的值为准
    real_out = Path(app.vars["out"].get())
    app.on_run()
    check("替换完成", pump(root, lambda: not app.busy))
    outs = sorted(p.name for p in real_out.glob("*.pdf")) if real_out.is_dir() else []
    check("输出 3 份", len(outs) == 3, str(outs))
    texts = page_texts(real_out / "002.pdf")
    check("002 的第 1 页是扫描件第 2 页", texts and "SCAN-2" in texts[0], str(texts[:1]))
    check("002 的第 2 页还是原件内容", texts[1:2] == ["ORI2-P2"], str(texts))
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("表格状态列刷新成「替换成功」",
          all(v[7] == "替换成功" for v in vals), str([v[7] for v in vals]))

    print("\n[6] 数量对不上 → 整批拒绝")
    src2, scan2, out2 = make_corpus(5, 3)
    app.vars["src"].set(str(src2))
    app.vars["scan"].set(str(scan2))
    app.vars["out"].set(str(out2))
    WARNED.clear()
    app.on_preview()
    check("预览完成", pump(root, lambda: not app.busy))
    check("被阻断", app.plan is not None and app.plan.blocked)
    check("「开始替换」仍是禁用的", str(app.btn_run["state"]) == "disabled")
    check("弹了警告说清楚差几份",
          any("差 2" in w for w in WARNED), str(WARNED))
    app.on_run()
    check("阻断时点「开始替换」不会产出任何文件",
          not out2.exists() or not list(out2.glob("*.pdf")))

    print("\n[7] 右键：忽略一行")
    src3, scan3, out3 = make_corpus(3, 3)
    app.vars["src"].set(str(src3))
    app.vars["scan"].set(str(scan3))
    app.vars["out"].set(str(out3))
    app.on_preview()
    check("预览完成", pump(root, lambda: not app.busy))
    app.tree.selection_set(app.tree.get_children()[1])
    app.on_ignore()
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("第 2 行变成「已跳过」", vals[1][7] == "已跳过", str(vals[1]))
    app.on_run()
    check("替换完成", pump(root, lambda: not app.busy))
    outs3 = sorted(p.name for p in out3.glob("*.pdf"))
    check("被忽略的那份没有输出", outs3 == ["001.pdf", "003.pdf"], str(outs3))

    print("\n[8] 后缀实时预览")
    src4, scan4, _o = make_corpus(2, 2)
    app.vars["src"].set(str(src4))
    app.vars["scan"].set(str(scan4))
    app.on_preview()
    check("预览完成", pump(root, lambda: not app.busy))
    app.vars["suffix"].set("_已签字首页")
    root.update()
    check("后缀预览显示真实文件名", "001_已签字首页.pdf" in app.lbl_suffix.cget("text"),
          app.lbl_suffix.cget("text"))
    app.vars["suffix"].set("_a/b")
    root.update()
    check("非法字符被去掉", "001_ab.pdf" in app.lbl_suffix.cget("text"),
          app.lbl_suffix.cget("text"))

    print("\n[9] 不改名：按 WI-xxxx 原名排队（2026-10-08 用户拍板）")
    app.vars["suffix"].set("")
    d9 = TMP / "corpus_named"
    src9 = d9 / "原件"
    src9.mkdir(parents=True)
    for nm in ("WI-3852", "WI-1032", "WI-2001"):      # 故意乱序建
        build_pdf(src9 / f"{nm}.pdf",
                  [(595, 842, f"{nm}-P1"), (595, 842, f"{nm}-P2")])
    scan9 = d9 / "扫描件_签字版.pdf"
    build_pdf(scan9, [(595, 842, f"SCAN-{k}") for k in range(1, 4)])
    out9 = d9 / "输出"
    app.vars["src"].set(str(src9))
    app.vars["scan"].set(str(scan9))
    app.vars["out"].set(str(out9))
    app.on_preview()
    check("预览完成", pump(root, lambda: not app.busy))
    check("没被阻断（3 份对 3 页）", app.plan is not None and not app.plan.blocked)
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    got9 = [v[1] for v in vals]
    check("表格顺序 = 文件夹里按名称看到的顺序（1032 → 2001 → 3852）",
          got9 == ["WI-1032.pdf", "WI-2001.pdf", "WI-3852.pdf"], str(got9))
    check("扫描页仍是 1、2、3",
          [v[4] for v in vals] == ["1", "2", "3"], str([v[4] for v in vals]))
    app.on_run()
    check("替换完成", pump(root, lambda: not app.busy))
    t9 = page_texts(out9 / "WI-2001.pdf")
    check("WI-2001 拿到扫描件第 2 页", bool(t9) and "SCAN-2" in t9[0], str(t9[:1]))
    check("WI-2001 第 2 页还是原件内容", t9[1:2] == ["WI-2001-P2"], str(t9))

    print("\n[10] 文案与按钮口径（UI 精简后）")
    check("原件文件夹那行的提示够短（≤ 20 字，不再堆一整段）",
          len(A.FOLDER_SPECS[0].hint) <= 20, A.FOLDER_SPECS[0].hint)
    check("短提示里不再提 001/002", "001" not in A.FOLDER_SPECS[0].hint,
          A.FOLDER_SPECS[0].hint)
    check("长说明收进了悬浮提示（detail）", "不用改名" in A.FOLDER_SPECS[0].detail)
    check("后缀示例换成真实 WI 名", "WI-1032" in A.SUFFIX_SAMPLE_FALLBACK,
          A.SUFFIX_SAMPLE_FALLBACK)
    for gone, why in (("on_open_out", "打开输出文件夹"), ("on_forget", "忘记记住的文件夹"),
                      ("on_help", "使用说明按钮")):
        check(f"删掉了「{why}」", not hasattr(A.App, gone))
    check("不再留 HELP_TEXT 死常量", not hasattr(A, "HELP_TEXT"))
    check("①②③ 的按钮都建好了（ADJUST_BUTTONS 全在）",
          all(hasattr(app, n) for n in A.ADJUST_BUTTONS), str(A.ADJUST_BUTTONS))
    check("方向按钮都是「整批」口径（有全部 / 横向两组）",
          "全部左转" in app.btn_rot_all_l.cget("text")
          and "横向右转" in app.btn_rot_land_r.cget("text"))

    print("\n[11] 模块③：方向（默认规则 + 整批旋转）")
    d11 = TMP / "corpus_rot"
    src11 = d11 / "原件"
    src11.mkdir(parents=True)
    # 第 2 份原件是横向的（842×595），其余竖向；扫描件一律竖扫（595×842）
    for i, sz in ((1, (595, 842)), (2, (842, 595)), (3, (595, 842))):
        build_pdf(src11 / f"{i:03d}.pdf",
                  [(sz[0], sz[1], f"ORI{i}-P1"), (sz[0], sz[1], f"ORI{i}-P2")])
    scan11 = d11 / "扫描件.pdf"
    build_pdf(scan11, [(595, 842, f"SCAN-{k}") for k in range(1, 4)])
    out11 = d11 / "输出"
    app.vars["src"].set(str(src11))
    app.vars["scan"].set(str(scan11))
    app.vars["out"].set(str(out11))
    app.on_preview()
    check("预览完成", pump(root, lambda: not app.busy))
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("默认规则：横向原件那一页自动「左转 90°」，其余正向",
          [v[6] for v in vals] == ["正向", "左转 90°", "正向"], str([v[6] for v in vals]))
    check("那一页的扫描页尺寸已经宽高互换",
          [v[5] for v in vals] == ["595×842", "842×595", "595×842"],
          str([v[5] for v in vals]))
    check("预览后方向按钮可用", str(app.btn_rot_all_r["state"]) == "normal")

    # 整批右转：所有页一起转（横向那页 270→0，其余 0→90）
    app.on_turn_all(C.ROT_RIGHT)
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("「全部右转」所有页都动了",
          [v[6] for v in vals] == ["右转 90°", "正向", "右转 90°"], str([v[6] for v in vals]))
    app.on_turn_all(C.ROT_LEFT)
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("「全部左转」转回默认的样子",
          [v[6] for v in vals] == ["正向", "左转 90°", "正向"], str([v[6] for v in vals]))

    # 横向专用：只动「配给横向原件」的那几页
    app.on_turn_land(C.ROT_LEFT)
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("「横向左转」只影响横向原件那一行",
          [v[6] for v in vals] == ["正向", "倒转 180°", "正向"], str([v[6] for v in vals]))
    check("状态栏说明了转了几页", "横向 1 页" in app.status.cget("text"),
          app.status.cget("text"))
    app.on_reset_rot()
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("「方向复位」回到默认规则（不是全部清零）",
          [v[6] for v in vals] == ["正向", "左转 90°", "正向"], str([v[6] for v in vals]))

    # 页码一动，「这一页是谁的封面」变了 → 默认的左转跟着横向原件走
    app.tree.selection_set(app.tree.get_children()[1])      # 第 2 行 = 横向原件 002
    app._swap_page(-1)                                      # 与第 1 行对调页码
    vals = [app.tree.item(r, "values") for r in app.tree.get_children()]
    check("对调后原件顺序没变（行不动）",
          [v[1] for v in vals] == ["001.pdf", "002.pdf", "003.pdf"], str([v[1] for v in vals]))
    check("对调后页码换成了 2、1、3",
          [v[4] for v in vals] == ["2", "1", "3"], str([v[4] for v in vals]))
    check("横向原件那一行仍是左转（跟着原件走，不跟着页码走）",
          [v[6] for v in vals] == ["正向", "左转 90°", "正向"], str([v[6] for v in vals]))
    check("于是「要转的那页」换成了第 1 页",
          app.plan.items[1].scan_index == 1 and app.plan.items[1].rotation == 270,
          f"{app.plan.items[1].scan_index}/{app.plan.items[1].rotation}")

    app.on_run()
    check("替换完成", pump(root, lambda: not app.busy))
    check("横向原件那份：输出首页真的横过来了 842×595",
          page_sizes(out11 / "002.pdf")[0] == (842.0, 595.0),
          str(page_sizes(out11 / "002.pdf")))
    check("竖向原件那份：输出首页仍是 595×842（没被连坐）",
          page_sizes(out11 / "001.pdf")[0] == (595.0, 842.0),
          str(page_sizes(out11 / "001.pdf")))

    app.on_close()          # 关窗这条路也要走一遍（它会保存配置）
    try:                    # on_close 已经把窗口销毁了，再 destroy 会报错
        root.destroy()
    except tk.TclError:
        pass
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for n in FAIL:
        print("  失败：" + n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
