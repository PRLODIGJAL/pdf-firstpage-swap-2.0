#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 首页替换工具 v2.0 —— 界面层（tkinter）

流程：选「原件文件夹」+「一次扫完的扫描件 PDF」→ 点「扫描并配对（预览）」→
      确认表格里的对应关系没错 → 点「开始替换」。

和 v1 的界面保持一致（同样的「文件夹 / 选项」两块、悬浮说明、右键菜单、记住上次选择），
只是把「名字匹配」换成「按顺序硬对应」，并多了「上移 / 下移」调序。

工具栏的 ①②③ 三个模块各占一行 —— 编号只留给这三个模块，上面两块不再编号，
免得界面上出现两个「①」。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).parent))
import seq_core as C  # noqa: E402

APP_TITLE = f"{C.TOOL_NAME}  v{C.TOOL_VERSION}"
# 和 v1 用不同的文件名，免得两个版本的记录互相踩
CONFIG_PATH = Path.home() / ".pdf_batch_cover_swap.json"
CONFIG_VERSION = 1
CONFIG_KEEP_KEYS = ("src", "scan", "out")
AUTO_OUT_NAME = C.AUTO_OUT_NAME
ROW_PAD = {"padx": 4, "pady": 3}
# 窗口太小就不恢复上次的大小（会把状态栏挤出去）；下面 default_geometry 也拿它兜底
GEOM_MIN = (980, 700)
# 初始窗口大小：要装得下「操作行 + ①②③ 三行工具栏 + 14 行表格」，不靠最大化
GEOM_DEFAULT = (1060, 740)
SAVE_DEBOUNCE_MS = 900

COLUMNS: tuple[tuple[str, str, int, str], ...] = (
    ("no", "#", 36, "center"),
    ("src", "原件", 280, "w"),
    ("pages", "页数", 48, "center"),
    ("size", "首页尺寸", 88, "center"),
    ("scan", "扫描页", 62, "center"),
    ("scansize", "扫描页尺寸", 90, "center"),
    ("rot", "方向", 74, "center"),
    ("status", "状态", 78, "center"),
    ("note", "说明", 200, "w"),
)

NEED_COLOR = {"替换失败": "#b00020", "替换成功": "#1a7f37", "已跳过": "#777777"}

# 工具栏上三个模块的按钮清单见下面的 MODULE_ROWS（那里是唯一真相源）


class FolderRow(NamedTuple):
    """文件夹区每一行的定义 —— 改文案只动这张表。

    hint：右边那句灰字（要短，一眼扫完）
    detail：鼠标停在灰字上才弹出来的长说明（要全）
    """

    key: str
    label: str
    hint: str
    detail: str = ""
    kind: str = "dir"          # dir = 选文件夹；file = 选文件


class RowWidgets(NamedTuple):
    frame: ttk.Frame
    label: ttk.Label
    entry: ttk.Entry
    button: ttk.Button
    hint: ttk.Label


FOLDER_SPECS: tuple[FolderRow, ...] = (
    FolderRow(
        "src", "原件文件夹", "顺序 = 文件夹里按「名称」看到的顺序",
        "没签字的原件放这里，排队顺序就是文件夹里按「名称」看到的那个顺序：\n\n"
        "· 不用改名成 001、002 —— 原名 WI-1032、WI-2001 直接排。\n"
        "· 字母不分大小写，数字按数值排（WI-9 在 WI-10 前面）。\n"
        "· 纸质文件就按这个顺序摊开去扫，扫完第 1 页对第 1 行。\n"
        "· 排错了不用改名，在表格里对调页码就行（见工具栏「页码对应」）。",
    ),
    FolderRow("scan", "扫描件 PDF", "一次扫完的那份多页 PDF",
              "一口气扫出来的那一份 PDF，第 N 页给表格第 N 行。\n\n"
              "份数必须和原件数量一致，不一致会整批不执行（顺序配对错一个后面全错）。",
              "file"),
    FolderRow("out", "输出文件夹", f"留空自动用「{AUTO_OUT_NAME}」",
              f"留空则自动填成「原件文件夹/{AUTO_OUT_NAME}」。\n\n"
              "会自动创建；原件文件夹任何时候都只读，不会被改动。"),
)

# 选项表：顺序即界面顺序（「页序倒序」已搬到工具栏做成按钮，不在这里）
CHECK_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "scale", "扫描页缩放到原件首页尺寸",
        "默认开。扫描页比原件大还是小都无所谓，会等比缩放并居中。\n\n"
        "例外：两边的“版面比例”差太多时（例如原件封面是横向 A4、\n"
        "你扫上来的是纵向 A4），不会硬塞进去——会原样保留扫描页，\n"
        "并在表格“说明”列写明原因，免得两旁留一大片白边。\n\n"
        "关掉则一律原样保留扫描页尺寸。",
    ),
    (
        "overwrite", "覆盖输出文件夹里的同名文件",
        "管的是“输出目录里已经有同名 PDF 怎么办”。默认关。\n\n"
        "· 不勾（推荐）：绝不覆盖任何已有文件，遇到重名自动加编号，\n"
        "  输出成 xxx_2.pdf、xxx_3.pdf。\n"
        "· 勾上：直接覆盖同名文件，旧内容被替换掉，不可恢复。\n\n"
        "注意：这条只管输出目录，原件文件夹任何时候都只读、不会被改。",
    ),
    (
        "recursive", "连同子文件夹一起处理",
        "默认关，只处理你选的那一层文件夹。\n\n"
        "勾上 → 子文件夹里的原件也一起排队（按「文件夹名/文件名」的名称顺序排），\n"
        "输出的 PDF 会复刻相同的子目录结构，避免不同子目录的同名文件互相打架。",
    ),
)

SUFFIX_TIP = (
    "给输出文件的名字加一个字尾，方便和原始文件区分开。\n\n"
    "例：原件叫 WI-1032.pdf\n"
    "    填 _已签字首页 → WI-1032_已签字首页.pdf\n"
    "    留空          → WI-1032.pdf（与原件同名）\n\n"
    "· 只改文件名，不影响内容；填什么就是什么，界面会把最终名字实时显示出来。\n"
    "· 路径分隔符 / \\ 等非法字符会被自动去掉。\n"
    "· 建议以 _ 或 - 开头，例如 _已替换、-signed。\n"
    "· 最长 40 个字符。"
)
SUFFIX_SAMPLE_FALLBACK = "WI-1032"

# ── 三个功能模块的说明文案（都挂在按钮上，鼠标停一下就出来）──────────────
ORDER_TIP = (
    "扫描件是不是「倒着扫的」—— 有些机器最后放上去的那张在最前面。\n\n"
    "点一下就在 正序 ⇄ 倒序 之间来回切，表格「扫描页」那一列会立刻反过来，\n"
    "对着看一眼就知道该用哪个。\n\n"
    "⚠️ 这是**整批重算**：切一次会把手动调过的页码清回默认（不是在当前结果上再反一次）。\n"
    "　所以正确顺序是：先把页序定下来 → 再去调个别行用第几页。"
)
PAGE_TIP_UP = "把这一行用的扫描页，和**上一行**对调 —— 行的顺序不动，只换页码。"
PAGE_TIP_DOWN = "把这一行用的扫描页，和**下一行**对调 —— 行的顺序不动，只换页码。"
PAGE_TIP_DBL = "双击某一行，可以直接指定它用扫描件第几页。"

ROT_TIP_AUTO = (
    "【默认规则】（自动生效，不用你点）\n"
    "扫描件一律是**竖着扫**的，所以：原件首页是**横向**的 → 它那一页默认**左转 90°**，\n"
    "把躺着的内容扳正。表格「方向」列会直接显示出来。\n\n"
    "【机器判不了的部分，交给你】\n"
    "纸放反了、整批都躺倒了，程序看来就是一堆墨迹，自动判必然有误判 ——\n"
    "所以工具栏这四个按钮都是**整批**转的：\n"
    "　· 全部左转 / 全部右转 —— 所有页一起转（整批都颠倒时用）\n"
    "　· 横向左转 / 横向右转 —— 只转「配给横向原件」的那些页（竖版没扫错，\n"
    "　　只有横版那几张方向不对时用）\n"
    "　· 方向复位 —— 回到上面的默认规则，清掉所有手动调整\n\n"
    "转完表格「方向」列立刻显示（正向 / 左转 90° / 右转 90° / 倒转 180°），\n"
    "「扫描页尺寸」也会跟着宽高互换 —— 对着看一眼再跑。\n\n"
    "⚠️ 手动定的方向记在**扫描页**上（那张纸确实是横的）；\n"
    "　换一份扫描件会自动清空，同一份重新预览则保留。"
)


# ── 工具栏「①②③」三个模块的表 ────────────────────────────────────────
# 一行 = 一个模块，界面上的顺序就是这张表的顺序。
# 建按钮、挂悬浮提示、控「什么时候能点」全都从这张表派生 ——
# 以后加/减按钮只改这里，不会漏掉某一处（这是 v1 踩过的坑）。
class BtnSpec(NamedTuple):
    """一个按钮的定义。method + args 由 App._mod_command 拼成回调。"""

    attr: str                                  # 挂到 App 上的属性名（回归脚本按它取按钮）
    text: str                                  # 按钮文字；留空 = 建完由代码动态填
    width: int
    method: str                                # App 上的方法名
    args: tuple = ()
    tip: str = ""


class ModuleRow(NamedTuple):
    tag: str                                   # 行首编号（① / ② / ③），也是这一行的名字
    buttons: tuple[BtnSpec, ...]
    hint: str = ""                             # 行尾灰字（要短，长说明收进按钮的悬浮提示）


MODULE_ROWS: tuple[ModuleRow, ...] = (
    ModuleRow("① 扫描件页序", (
        BtnSpec("btn_order", "", 12, "on_flip_order", tip=ORDER_TIP),
    ), "扫描仪倒着出纸时点一下"),
    ModuleRow("② 页码对应", (
        BtnSpec("btn_up", "↑ 与上行对调", 12, "_swap_page", (-1,), PAGE_TIP_UP),
        BtnSpec("btn_down", "↓ 与下行对调", 12, "_swap_page", (1,), PAGE_TIP_DOWN),
    ), "双击某一行也能直接指定页号"),
    ModuleRow("③ 方向（整批转）", (
        BtnSpec("btn_rot_all_l", "全部左转 90°", 13, "on_turn_all", (C.ROT_LEFT,), ROT_TIP_AUTO),
        BtnSpec("btn_rot_all_r", "全部右转 90°", 13, "on_turn_all", (C.ROT_RIGHT,), ROT_TIP_AUTO),
        BtnSpec("btn_rot_land_l", "横向左转 90°", 13, "on_turn_land", (C.ROT_LEFT,), ROT_TIP_AUTO),
        BtnSpec("btn_rot_land_r", "横向右转 90°", 13, "on_turn_land", (C.ROT_RIGHT,), ROT_TIP_AUTO),
        BtnSpec("btn_rot_reset", "方向复位", 10, "on_reset_rot", tip=ROT_TIP_AUTO),
    )),
)

# 三个模块的全部按钮（由上面的表派生）—— _set_busy / _sync_adjust_buttons 只看这一份
ADJUST_BUTTONS = tuple(b.attr for row in MODULE_ROWS for b in row.buttons)


def default_geometry(screen_w: int, screen_h: int) -> str:
    """初始窗口大小 —— 要放得下四条工具栏 + 表格，但绝不能超出屏幕。

    交付到 Windows 笔记本上时屏幕可能只有 1366×768，写死一个高尺寸会被任务栏切掉，
    所以这里按屏幕实际大小收一收。
    """
    w = min(GEOM_DEFAULT[0], max(screen_w - 40, GEOM_MIN[0]))
    h = min(GEOM_DEFAULT[1], max(screen_h - 90, GEOM_MIN[1]))
    return f"{w}x{h}"


def fit_geometry(geo: object, screen_w: int, screen_h: int) -> str | None:
    """窗口大小只在合理范围内才恢复，返回 None 表示「这次不恢复」。

    抽成独立函数是因为它不好从界面上测（withdraw 状态下 winfo_width() 恒为 1）。
    恢复出一个看不见的窗口比不恢复更糟，所以宁可保守。
    """
    if not isinstance(geo, (list, tuple)) or len(geo) != 2:
        return None
    try:
        w, h = int(geo[0]), int(geo[1])
    except Exception:  # noqa: BLE001
        return None
    if GEOM_MIN[0] <= w <= screen_w and GEOM_MIN[1] <= h <= screen_h:
        return f"{w}x{h}"
    return None


class Tip:
    """极简悬浮提示：鼠标停留片刻弹出，移开或点击即消失。零第三方依赖。"""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 350, width: int = 520) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self.width = width
        self._after: str | None = None
        self._win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e: tk.Event | None = None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:  # noqa: BLE001
                pass
            self._after = None

    def _show(self) -> None:
        if self._win or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._win = tk.Toplevel(self.widget)
        self._win.wm_overrideredirect(True)
        self._win.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._win, text=self.text, justify="left", anchor="w",
            background="#ffffe1", foreground="#222", relief="solid", borderwidth=1,
            wraplength=self.width, padx=10, pady=8,
        ).pack()

    def _hide(self, _e: tk.Event | None = None) -> None:
        self._cancel()
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._win = None


def _fmt_size(sz: tuple[float, float]) -> str:
    if not sz or min(sz) <= 0:
        return "—"
    return f"{sz[0]:.0f}×{sz[1]:.0f}"


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.pack(fill="both", expand=True)
        self.plan: C.Plan | None = None
        self.busy = False
        self.rows: dict[str, RowWidgets] = {}
        self._save_after: str | None = None
        self._jobs: list[tuple[str, object]] = []   # 后台线程 → 主线程的交接处
        self._config_broken = False
        self._restored: list[str] = []
        self._missing_dirs: list[str] = []
        # ⑥ 人工判的方向：{扫描页序号: 角度}（只记手动，自动规则由 core 按原件横竖算）
        self.rotations: dict[int, int] = {}
        self._rot_scan = ""
        self.vars = {
            "src": tk.StringVar(),
            "scan": tk.StringVar(),
            "out": tk.StringVar(),
            "reverse": tk.BooleanVar(value=False),
            "scale": tk.BooleanVar(value=True),
            "overwrite": tk.BooleanVar(value=False),
            "recursive": tk.BooleanVar(value=False),
            "suffix": tk.StringVar(value=""),
        }
        self._build()
        self._load_config()
        self.vars["suffix"].trace_add("write", lambda *_a: self._refresh_suffix_hint())
        self._refresh_suffix_hint()
        self.vars["src"].trace_add("write", lambda *_a: self._autofill_out())
        # ⚠️ reverse 不挂自动重排 —— 它是按钮显式控制的（on_flip_order），
        #    挂 trace 会在读配置/复位时偷偷重排一次，用户没点却变了
        for var in self.vars.values():
            var.trace_add("write", lambda *_a: self._schedule_save())
        self.winfo_toplevel().protocol("WM_DELETE_WINDOW", self.on_close)
        self._announce_restore()

    # ── 界面搭建 ─────────────────────────────────────────────────────────
    def _build(self) -> None:
        box = ttk.LabelFrame(self, text="文件夹（选好会自动记住）", padding=6)
        box.pack(fill="x")
        for spec in FOLDER_SPECS:
            row = ttk.Frame(box)
            lbl = ttk.Label(row, text=spec.label, width=12, anchor="w")
            lbl.pack(side="left")
            ent = ttk.Entry(row, textvariable=self.vars[spec.key])
            ent.pack(side="left", fill="x", expand=True, padx=(0, 4))
            btn = ttk.Button(row, text="浏览…", width=8,
                             command=lambda k=spec.key: self._pick(k))
            btn.pack(side="left")
            hint = ttk.Label(row, text=spec.hint, foreground="#777")
            hint.pack(side="left", padx=(8, 0))
            if spec.detail:
                Tip(hint, spec.detail)      # 长说明收进悬浮，别堆在界面上
            self.rows[spec.key] = RowWidgets(row, lbl, ent, btn, hint)
            row.pack(fill="x", **ROW_PAD)

        opt = ttk.LabelFrame(self, text="选项（鼠标停在文字上会弹出说明）", padding=6)
        opt.pack(fill="x", pady=(6, 0))
        l1 = ttk.Frame(opt)
        l1.pack(fill="x")
        for key, label, tip in CHECK_SPECS:
            cb = ttk.Checkbutton(l1, text=label, variable=self.vars[key])
            cb.pack(side="left", padx=(0, 14))
            Tip(cb, tip)
        l2 = ttk.Frame(opt)
        l2.pack(fill="x", pady=(4, 0))
        ttk.Label(l2, text="输出文件名后缀：").pack(side="left")
        ent_suffix = ttk.Entry(l2, textvariable=self.vars["suffix"], width=16)
        ent_suffix.pack(side="left", padx=(0, 10))
        Tip(ent_suffix, SUFFIX_TIP)
        self.lbl_suffix = ttk.Label(l2, text="", foreground="#1a5fb4")
        self.lbl_suffix.pack(side="left", padx=(0, 18))
        ttk.Label(l2, text="（顺序配对，不需要相似度门槛）",
                  foreground="#777").pack(side="left")

        # 操作行：真正要动手的两步
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(8, 4))
        self.btn_scan = ttk.Button(bar, text="扫描并配对（预览）", command=self.on_preview)
        self.btn_scan.pack(side="left")
        self.btn_run = ttk.Button(bar, text="开始替换", command=self.on_run, state="disabled")
        self.btn_run.pack(side="left", padx=6)

        # ①②③ 三个模块**各占一行** —— 全挤在一行会逼着用户把窗口拉到最大才看得全。
        # 用 grid 让第 0 列自动按最宽的标签定宽，三行按钮自然左对齐（不写死像素）。
        mods = ttk.Frame(self)
        mods.pack(fill="x", pady=(0, 4))
        for r, mod in enumerate(MODULE_ROWS):
            ttk.Label(mods, text=mod.tag, foreground="#555").grid(
                row=r, column=0, sticky="w", padx=(0, 8), pady=2)
            cell = ttk.Frame(mods)
            cell.grid(row=r, column=1, sticky="w")
            for spec in mod.buttons:
                b = ttk.Button(cell, text=spec.text, width=spec.width, state="disabled",
                               command=self._mod_command(spec))
                b.pack(side="left", padx=(0, 6))
                setattr(self, spec.attr, b)
                if spec.tip:
                    Tip(b, spec.tip)
            if mod.hint:
                ttk.Label(cell, text=mod.hint, foreground="#888").pack(side="left", padx=(8, 0))
        self.btn_order.config(text=self._order_text())   # 文字随正序/倒序变，建完再填
        self.mod_hint_row = mods                          # 回归脚本用它量这一块占多宽

        # 底部状态条**先 pack**（且 side="bottom"），再 pack 表格：
        # 窗口被压矮时牺牲的是表格行数，而不是把「就绪 / 进度」挤出可视区。
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", pady=(6, 0))
        self.pb = ttk.Progressbar(bottom, mode="determinate", length=260)
        self.pb.pack(side="left")
        self.status = ttk.Label(bottom, text="就绪：选好原件文件夹和扫描件 PDF，点「扫描并配对」",
                                foreground="#333")
        self.status.pack(side="left", padx=10)

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=[c[0] for c in COLUMNS], show="headings", height=14)
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, stretch=(key in ("src", "note")))
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(mid, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        for st, color in NEED_COLOR.items():
            self.tree.tag_configure(st, foreground=color)
        self.tree.tag_configure("odd", background="#f5f6f8")
        self.tree.bind("<Double-1>", self.on_row_double)
        self.tree.bind("<<TreeviewSelect>>", lambda *_a: self._sync_adjust_buttons())
        self.tree.bind("<Button-2>", self._popup)
        self.tree.bind("<Button-3>", self._popup)

        # 方向是**整批**操作（工具栏③），右键里不放单页旋转 —— 免得两套入口打架
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="改用扫描件第…页", command=self.on_assign_page)
        self.menu.add_command(label="忽略此行（不处理）", command=self.on_ignore)
        self.menu.add_separator()
        self.menu.add_command(label="在访达/资源管理器中显示原件", command=self.on_reveal)

    # ── 选择路径 ─────────────────────────────────────────────────────────
    def _spec(self, key: str) -> FolderRow:
        for s in FOLDER_SPECS:
            if s.key == key:
                return s
        raise KeyError(key)

    def _pick(self, key: str) -> None:
        spec = self._spec(key)
        cur = self.vars[key].get().strip()
        start = cur if cur else str(Path.home())
        if spec.kind == "file":
            got = filedialog.askopenfilename(
                title=f"选择{spec.label}",
                initialdir=str(Path(start).parent if Path(start).is_file() else start),
                filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
        else:
            got = filedialog.askdirectory(title=f"选择{spec.label}",
                                          initialdir=start if Path(start).is_dir() else str(Path.home()))
        if got:
            self.vars[key].set(got)

    def _autofill_out(self) -> None:
        if self.vars["out"].get().strip():
            return
        src = self.vars["src"].get().strip()
        if src and Path(src).is_dir():
            self.vars["out"].set(str(Path(src) / AUTO_OUT_NAME))

    # ── 模块①：扫描件页序（正序 ⇄ 倒序，整批）─────────────────────────────
    def _order_text(self) -> str:
        return "页序：倒序" if self.vars["reverse"].get() else "页序：正序"

    def on_flip_order(self) -> None:
        """一键把整批的页序反过来（扫描仪倒着出纸时用）。

        ⚠️ 是「按模式重算」不是「把当前结果再反一次」—— 来回点是幂等的，
           用户不用记自己按过几次；代价是手动调过的页码会被清回默认，
           所以顺序必须是：先定页序 → 再调个别页码。
        """
        if self.plan is None or self.plan.blocked:
            return
        rev = not self.vars["reverse"].get()
        self.vars["reverse"].set(rev)
        C.reindex(self.plan, rev)
        self._apply_rotations()
        self._fill_table()
        self.btn_order.config(text=self._order_text())
        self.status.config(
            text=f"页序已切成{'倒序' if rev else '正序'}（表格「扫描页」列已重算，"
                 f"手动调过的页码也一并清回默认）。",
            foreground="#333")

    # ── 模块②：页码对应（一页只许一个人用）───────────────────────────────
    def _swap_page(self, delta: int) -> None:
        """与相邻行对调扫描页 —— 行不动，只换「这一行用哪一页」。"""
        sel = self.tree.selection()
        if not sel or self.plan is None or self.plan.blocked:
            return
        idx = self.tree.index(sel[0]) + 1
        if not C.swap_pages(self.plan, idx, delta):
            return
        self._apply_rotations()
        pos = idx + delta - 1
        self._reselect_refresh(pos)

    def _mod_command(self, spec: BtnSpec):
        """把 BtnSpec 的「方法名 + 参数」拼成按钮回调。

        参数用默认值固化在闭包里 —— 直接在 for 循环里写 lambda 会踩晚绑定，
        五个按钮最后全指向最后一次迭代的值。
        """
        return lambda m=spec.method, a=spec.args: getattr(self, m)(*a)

    def _adjust_buttons(self) -> tuple:
        """①②③ 三个模块的全部按钮 —— 由 MODULE_ROWS 派生，一起控，不会漏掉某一个。"""
        return tuple(getattr(self, n) for n in ADJUST_BUTTONS)

    def _sync_adjust_buttons(self) -> None:
        """只在「有可用的配对结果」时才让①②③的按钮可点。"""
        on = (self.plan is not None and not self.plan.blocked
              and bool(self.tree.get_children()))
        state = "normal" if on else "disabled"
        for b in self._adjust_buttons():
            b.config(state=state)

    # ── 模块③：方向（整批转，没有单页入口）────────────────────────────────
    def on_turn_all(self, delta: int) -> None:
        """整份扫描件**所有页**统一左转 / 右转 90°（整批都躺倒了时用）。"""
        self._turn_pages(None, delta, "全部")

    def on_turn_land(self, delta: int) -> None:
        """只转「配给横向原件」的那几页（竖版没扫错、只有横版方向不对时用）。"""
        if self.plan is None or self.plan.blocked:
            return
        pages = {it.scan_index for it in self.plan.items if C.is_landscape(it.src_size)}
        if not pages:
            self.status.config(text="这批原件里没有横向的（首页都是竖向），横向按钮用不上。",
                               foreground="#333")
            return
        self._turn_pages(pages, delta, "横向")

    def _turn_pages(self, pages: set[int] | None, delta: int, scope: str) -> None:
        """在当前方向的基础上再转 —— 自动值就此固化成人工值，之后不再跟着原件横竖变。"""
        if self.plan is None or self.plan.blocked:
            return
        hit = 0
        for it in self.plan.items:
            if pages is not None and it.scan_index not in pages:
                continue
            self.rotations[it.scan_index] = C.turn_deg(it.rotation, delta)
            hit += 1
        if not hit:
            return
        self._apply_rotations()
        self._reselect_refresh()
        self.status.config(
            text=f"已把{scope} {hit} 页统一{'左' if delta < 0 else '右'}转 90°"
                 f"（看表格「方向」列；转错了点「方向复位」）。",
            foreground="#1a5c2a")

    def on_reset_rot(self) -> None:
        """清掉全部手动调整，回到默认规则（横向原件那一页左转 90°）。"""
        if not self.rotations:
            return
        self.rotations = {}
        self._apply_rotations()
        self._reselect_refresh()
        n = sum(1 for it in (self.plan.items if self.plan else [])
                if it.rotation)
        self.status.config(
            text="方向已复位到默认规则" + (f"（{n} 页按默认左转 90°）" if n else "（全部正向）"),
            foreground="#333")

    def _reselect_refresh(self, pos: int | None = None) -> None:
        """刷表并保住选中行（pos 给定时改选到那一行）。

        ⚠️ 只能按**位置**恢复，不能按行 id —— _fill_table 是整表重建，
           填完旧 id 就已经失效了（selection_set 会抛 "Item Ixxx not found"）。
        """
        if pos is None:
            sel = self.tree.selection()
            pos = self.tree.index(sel[0]) if sel else -1
        self._fill_table()
        kids = self.tree.get_children()
        if 0 <= pos < len(kids):
            self.tree.selection_set(kids[pos])
            self.tree.see(kids[pos])

    def _apply_rotations(self) -> None:
        """把「人工 > 自动 > 正向」写回 plan。

        口径统一走 core 的 apply_rotations，界面自己不算 —— 两边各算一套迟早漂移。
        """
        if self.plan is None:
            return
        C.apply_rotations(self.plan, self.rotations)

    # ── 输出文件名后缀的实时预览 ─────────────────────────────────────────
    def _sample_stem(self) -> str:
        if self.plan and self.plan.items:
            return self.plan.items[0].src.stem
        return SUFFIX_SAMPLE_FALLBACK

    def _refresh_suffix_hint(self) -> None:
        suffix = C.clean_output_suffix(self.vars["suffix"].get())
        self.lbl_suffix.config(text=f"{self._sample_stem()}{suffix}.pdf")

    # ── 配置持久化 ───────────────────────────────────────────────────────
    def _load_config(self) -> None:
        if not CONFIG_PATH.is_file():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self._config_broken = True
            return
        if not isinstance(data, dict):
            self._config_broken = True
            return
        for k, v in data.items():
            if k in self.vars:
                try:
                    self.vars[k].set(v)
                except Exception:  # noqa: BLE001
                    pass
        for key in CONFIG_KEEP_KEYS:
            val = self.vars[key].get().strip()
            if not val:
                continue
            label = self._spec(key).label
            self._restored.append(label)
            if not Path(val).exists():
                self._missing_dirs.append(label)
        geo = fit_geometry(data.get("window"),
                           self.winfo_toplevel().winfo_screenwidth(),
                           self.winfo_toplevel().winfo_screenheight())
        if geo:
            self.winfo_toplevel().geometry(geo)

    def _save_config(self) -> None:
        """先写临时文件再原子替换；替换被拦就退一步直接写，并收掉 .tmp。"""
        self._cancel_save()
        top = self.winfo_toplevel()
        data: dict[str, object] = {k: v.get() for k, v in self.vars.items()}
        data["_version"] = CONFIG_VERSION
        try:
            data["window"] = [top.winfo_width(), top.winfo_height()]
        except Exception:  # noqa: BLE001
            pass
        text = json.dumps(data, ensure_ascii=False, indent=1)
        tmp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, CONFIG_PATH)
            return
        except Exception:  # noqa: BLE001
            pass
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            CONFIG_PATH.write_text(text, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _schedule_save(self) -> None:
        self._cancel_save()
        self._save_after = self.after(SAVE_DEBOUNCE_MS, self._save_config)

    def _cancel_save(self) -> None:
        if self._save_after:
            try:
                self.after_cancel(self._save_after)
            except Exception:  # noqa: BLE001
                pass
            self._save_after = None

    def _announce_restore(self) -> None:
        if self._config_broken:
            self.status.config(text="上次的记录文件读不出来（已忽略，不影响使用）",
                               foreground="#b00020")
        elif self._missing_dirs:
            self.status.config(
                text="已填好上次的路径，但「" + "、".join(self._missing_dirs)
                     + "」已经不存在了，请重新选一下", foreground="#b00020")
        elif self._restored:
            self.status.config(
                text="已自动填好上次用的「" + "、".join(self._restored)
                     + "」，直接点『扫描并配对』即可", foreground="#1a5c2a")

    # ── 预览 / 执行 ──────────────────────────────────────────────────────
    def _collect_options(self) -> C.Options:
        """⚠️ 别叫 `_options` —— tkinter.Widget 内部有同名方法，会被覆盖掉。"""
        return C.Options(
            src_dir=self.vars["src"].get().strip(),
            scan_pdf=self.vars["scan"].get().strip(),
            out_dir=self.vars["out"].get().strip(),
            reverse_order=bool(self.vars["reverse"].get()),
            scale_to_original=bool(self.vars["scale"].get()),
            overwrite=bool(self.vars["overwrite"].get()),
            recursive=bool(self.vars["recursive"].get()),
            suffix=self.vars["suffix"].get(),
            rotations=dict(self.rotations),
        )

    def _clear_table(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)

    def _fill_table(self) -> None:
        self._clear_table()
        if self.plan is None:
            return
        for k, it in enumerate(self.plan.items):
            tags = [it.status] if it.status in NEED_COLOR else []
            if k % 2:
                tags.append("odd")
            self.tree.insert(
                "", "end",
                values=(it.index, it.src.name, it.src_pages or "—",
                        _fmt_size(it.src_size), it.scan_index,
                        _fmt_size(it.scan_size),
                        C.ROT_LABELS.get(it.rotation % 360, "正向"),
                        it.status, it.note),
                tags=tuple(tags))
        self._sync_adjust_buttons()

    def on_preview(self) -> None:
        if self.busy:
            return
        # 换了一份扫描件 → 上次人工判的方向作废（那批纸已经不在了）
        scan_now = self.vars["scan"].get().strip()
        if scan_now != self._rot_scan:
            self.rotations = {}
            self._rot_scan = scan_now
        opts = self._collect_options()
        if not opts.src_dir or not opts.scan_pdf:
            messagebox.showwarning(APP_TITLE, "先把「原件文件夹」和「扫描件 PDF」选好。")
            return
        if not opts.out_dir:
            self._autofill_out()
            opts.out_dir = self.vars["out"].get().strip()
        self._set_busy(True, "正在读取……")
        self.status.config(text="正在读取原件和扫描件……", foreground="#333")

        def work() -> None:
            try:
                plan = C.plan_batch(opts)
            except Exception as exc:  # noqa: BLE001
                plan = C.Plan(blocked=True, block_reason=f"读取时出错：{exc}")
            self._jobs.append(("preview", plan))

        threading.Thread(target=work, daemon=True).start()
        self._poll()

    def _after_preview(self, plan: C.Plan) -> None:
        self._set_busy(False)
        self.plan = plan
        self._fill_table()
        if plan.blocked:
            self.btn_run.config(state="disabled")
            self.status.config(text="数量/文件有问题，整批不执行（看弹出提示）",
                               foreground="#b00020")
            messagebox.showwarning(APP_TITLE, plan.block_reason)
            return
        self.btn_run.config(state="normal")
        self.status.config(
            text=f"原件 {len(plan.items)} 份、扫描件 {plan.scan_pages} 页，"
                 f"一一对应上了。确认表格没错再点「开始替换」。",
            foreground="#1a5c2a")

    def on_run(self) -> None:
        if self.busy or self.plan is None or self.plan.blocked:
            return
        opts = self._collect_options()
        if not opts.out_dir:
            self._autofill_out()
            opts.out_dir = self.vars["out"].get().strip()
        out_dir = Path(opts.out_dir).expanduser()
        n = len([it for it in self.plan.items if it.status != C.Status.SKIPPED.value])
        if not n:
            messagebox.showwarning(APP_TITLE, "没有要处理的文件。")
            return
        if not messagebox.askokcancel(
                APP_TITLE,
                f"将输出 {n} 份 PDF 到：\n{out_dir}\n\n"
                "原件文件夹不会被改动。开始吗？"):
            return
        self._set_busy(True, "替换中……")
        self.pb["maximum"] = max(n, 1)
        self.pb["value"] = 0

        def work() -> None:
            results, _notes = C.run_batch(self.plan, opts, out_dir)
            self._jobs.append(("run", (results, out_dir)))

        threading.Thread(target=work, daemon=True).start()
        self._poll()

    def _poll(self) -> None:
        """主线程定时取后台结果 —— 后台线程绝不直接碰 Tk（macOS 上会炸
        “main thread is not in main loop”）。"""
        if not self._jobs:
            self.after(50, self._poll)
            return
        kind, payload = self._jobs.pop(0)
        if kind == "preview":
            self._after_preview(payload)
        else:
            self._after_run(payload[0], payload[1])

    def _after_run(self, results: list[C.SwapResult], out_dir: Path) -> None:
        self._set_busy(False)
        self.pb["value"] = self.pb["maximum"]
        ok = sum(1 for r in results if r.status == C.Status.OK.value)
        bad = [r for r in results if r.status == C.Status.FAILED.value]
        skip = sum(1 for r in results if r.status == C.Status.SKIPPED.value)
        # 把状态写回表格，用户能直接看到哪一份失败
        if self.plan is not None:
            by_name = {r.src.name: r for r in results}
            for it in self.plan.items:
                r = by_name.get(it.src.name)
                if r is not None:
                    it.status = r.status
                    it.note = r.note
            self._fill_table()
        msg = f"完成 {ok} 份"
        if skip:
            msg += f"，跳过 {skip} 份"
        if bad:
            msg += f"，失败 {len(bad)} 份（表格「说明」列有原因）"
        self.status.config(text=msg + f"　→　{out_dir}",
                           foreground="#b00020" if bad else "#1a5c2a")
        messagebox.showinfo(APP_TITLE, msg + f"\n\n输出目录：{out_dir}")

    def _set_busy(self, busy: bool, title: str = "") -> None:
        self.busy = busy
        self.btn_scan.config(state="disabled" if busy else "normal")
        self.btn_run.config(state=("disabled" if busy or self.plan is None
                                   or self.plan.blocked else "normal"))
        if busy:
            for b in self._adjust_buttons():
                b.config(state="disabled")
        else:
            self._sync_adjust_buttons()
        if busy:
            self.winfo_toplevel().title(f"{APP_TITLE}　——　{title}")

    # ── 右键菜单 ─────────────────────────────────────────────────────────
    def _current_item(self) -> C.Item | None:
        sel = self.tree.selection()
        if not sel or self.plan is None or self.plan.blocked:
            return None
        i = self.tree.index(sel[0])
        return self.plan.items[i] if 0 <= i < len(self.plan.items) else None

    def _popup(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid or self.plan is None or self.plan.blocked:
            return
        self.tree.selection_set(iid)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def on_row_double(self, _e: tk.Event | None = None) -> None:
        self.on_assign_page()

    def on_assign_page(self) -> None:
        """双击行 / 右键：直接指定这一行用扫描件第几页。

        ⭐ 一页只给一份原件：填的页已被别的行占用时自动**对调**，
           不会让两行抢同一页（那样必定有一份原件拿不到封面）。
        """
        it = self._current_item()
        if it is None or self.plan is None:
            return
        idx = self.tree.index(self.tree.selection()[0]) + 1
        total = self.plan.scan_pages
        win = tk.Toplevel(self)
        win.title("改用扫描件第几页")
        win.transient(self.winfo_toplevel())
        ttk.Label(win, text=f"《{it.src.name}》现在用的是扫描件第 {it.scan_index} 页。",
                  padding=10).pack()
        var = tk.IntVar(value=it.scan_index)
        ttk.Spinbox(win, from_=1, to=max(total, 1), textvariable=var, width=8).pack(pady=4)
        ttk.Label(win, text=f"扫描件共 {total} 页。一页只给一份原件 ——\n"
                           "填的页码若已被别的行占用，会自动和它对调。",
                  foreground="#777").pack()

        def ok() -> None:
            changed, note = C.assign_page(self.plan, idx, int(var.get()))
            win.destroy()
            if not changed:
                return
            self._apply_rotations()
            self._reselect_refresh()
            self.status.config(text=f"《{it.src.name}》{note}。看一眼表格再跑。",
                               foreground="#333")

        ttk.Button(win, text="确定", command=ok).pack(pady=8)

    def on_ignore(self) -> None:
        it = self._current_item()
        if it is None:
            return
        it.status = C.Status.SKIPPED.value if it.status != C.Status.SKIPPED.value \
            else C.Status.PENDING.value
        it.note = "你手动忽略了这一行" if it.status == C.Status.SKIPPED.value else ""
        self._fill_table()

    def on_reveal(self) -> None:
        it = self._current_item()
        if it is None:
            return
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", str(it.src)], check=False)
            elif os.name == "nt":
                subprocess.run(["explorer", "/select,", str(it.src)], check=False)
            else:
                subprocess.run(["xdg-open", str(it.src.parent)], check=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning(APP_TITLE, f"打不开：{exc}")

    # ── 其它 ─────────────────────────────────────────────────────────────
    def on_close(self) -> None:
        self._cancel_save()
        self._save_config()
        self.winfo_toplevel().destroy()


def main() -> int:
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry(default_geometry(root.winfo_screenwidth(), root.winfo_screenheight()))
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
