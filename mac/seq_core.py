#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 首页替换工具 v2.0 —— 核心层（顺序配对，不做名字匹配）

和 v1（PDF 首页替换工具）的区别
------------------------------
v1：一份原件 ↔ 一份扫描件，靠文件名相似度匹配。
v2：现实里不可能一份一份扫 —— 把十几二十份签字首页**一口气扫成一份多页 PDF**，
    原件**按文件名排队**（就是文件夹里按「名称」看到的那个顺序，不用改名成 001/002），
    然后**按顺序硬对应**：
        表格第 1 行 ← 扫描件第 1 页
        表格第 2 行 ← 扫描件第 2 页
        ……
    不做任何名字匹配（也没有相似度门槛）。
    顺序 = 你扫的顺序 = 你把纸质文件摊开的顺序，所以界面里能 ↑↓ 微调、也能双重指定页号。

⚠️ 顺序配对是一条「脆弱链」：中间任何一份原件出问题（读不出、页数不齐），
   后面全部会错位。所以校验做成**阻断式** —— 有问题就整批不输出，
   而不是"跳过那一份继续跑"（跳过 = 从第 N 份起全部贴错封面）。

规则都收在下面的表里，加规则只改表：
    ① 排序规则表   ORDER_RULES   —— 原件怎么排队
    ② 阻断闸门表   GATE_RULES    —— 什么情况下整批拒绝执行
    ③ 贴合决策表   FIT_*         —— 扫描页缩放到原件首页 vs 原样保留
    ④ 后缀规范化   SUFFIX_*      —— 界面与命令行同一口径
    ⑤ 状态枚举     Status        —— 预览表格里能出现的全部状态
    ⑥ 方向表       ROT_*         —— 扫描页左转/右转（人工判，机器判不了）
    ⑦ 自动方向     AUTO_ROT_*    —— 原件首页是横向的 → 它那一页默认左转 90°
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, NamedTuple, Sequence

TOOL_NAME = "PDF 首页替换工具"
TOOL_VERSION = "2.0"

PDF_EXTS = frozenset({".pdf"})
# 输出文件夹留空时的默认子目录名
AUTO_OUT_NAME = "输出_已替换"


# ══════════════════════════════════════════════════════════════════════════
# ① 排序规则表
# ══════════════════════════════════════════════════════════════════════════

_NUM_RE = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple[tuple[int, int, str], ...]:
    """「文件名顺序」的排序键 —— 力求与文件夹里按「名称」看到的顺序一致。

    两条规则，缺一不可：
        ① 字母不区分大小写（Apple 与 apple 不会被大小写劈成两堆）
        ② 数字按数值大小排（WI-9 在 WI-10 前面，不会变成 WI-10 < WI-9 的字典序错误）

    每段统一成 (类别, 数值, 小写文本) 三元组，保证任意两个名字比较时
    类型永远一致 —— 否则 str 和 int 撞在一起会抛 TypeError。

    ⚠️ 已知的边界：中文名按 Unicode 码点排，可能与资源管理器/访达的拼音序不完全一致。
       真遇到就到表格里用 ↑↓ 手动调一次（手动调过的位置以表格为准）。
    """
    return tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p.lower())
        for p in _NUM_RE.split(name)
    )


def _key_by_name(p: Path, base: Path) -> tuple:
    """按「相对路径」排：有子文件夹时，先按目录排再按文件名排。"""
    try:
        rel = p.relative_to(base)
    except ValueError:
        rel = Path(p.name)
    return tuple(natural_key(part) for part in rel.parts)


class OrderRule(NamedTuple):
    """① 一条排序规则。加一种排法就往 ORDER_RULES 里加一行。"""

    key: str
    label: str
    desc: str
    sorter: Callable[[list[Path], Path], list[Path]]


ORDER_RULES: tuple[OrderRule, ...] = (
    OrderRule(
        "name",
        "文件名顺序",
        "和文件夹里按「名称」看到的顺序一致：字母不分大小写、数字按数值排。"
        "原件不用改名成 001/002，WI-1032、WI-2001 这类原名直接排",
        lambda paths, base: sorted(paths, key=lambda p: _key_by_name(p, base)),
    ),
    OrderRule(
        "mtime",
        "修改时间顺序",
        "按文件修改时间从早到晚排（原件没有编号、但拷贝/生成有先后的情况用）",
        lambda paths, _base: sorted(
            paths,
            key=lambda p: (_safe_mtime(p), natural_key(p.name)),
        ),
    ),
)
DEFAULT_ORDER = "name"


def _safe_mtime(p: Path) -> float:
    """读不出修改时间就当作 0，别让一份文件把整个预览搞崩。"""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def sort_originals(paths: Sequence[Path], base: Path,
                   rule_key: str = DEFAULT_ORDER) -> list[Path]:
    for rule in ORDER_RULES:
        if rule.key == rule_key:
            return rule.sorter(list(paths), base)
    raise ValueError(f"没有这条排序规则：{rule_key}")


# ══════════════════════════════════════════════════════════════════════════
# ③ 贴合决策表（沿用 v1 已验证的口径）
# ══════════════════════════════════════════════════════════════════════════

FIT_ASPECT_TOLERANCE = 0.15
FIT_FIT = "贴合"
FIT_ASIS = "原样"


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 扫描页方向（人工判定 —— 机器判不出来）
# ══════════════════════════════════════════════════════════════════════════
#
# 现实情况：扫描仪不管纸是横是竖，出来一律竖版。一张**横版表格**被竖着扫进去后，
# 内容就是躺着的（而且可能头朝左、也可能头朝右）。这活儿没法自动判：
# 躺着的字和正着的字在程序看来都是一堆墨迹，判方向必然有误判。
# 所以交给人在表格里点一下 —— 规则表只管"怎么转"，不管"该不该转"。

ROT_ANGLES = (0, 90, 180, 270)
ROT_LABELS = {
    0: "正向",
    90: "右转 90°",
    180: "倒转 180°",
    270: "左转 90°",
}

# 角度一律按 PDF 的 /Rotate 语义：**正数 = 顺时针**（向右转）。
# 实测确认：pypdf 的 Page.rotate(angle) 是顺时针，transfer_rotation_to_content()
# 会把 /Rotate 烘进内容流并自动交换 mediabox 宽高 —— 两者配套才生效。
ROT_LEFT = -90
ROT_RIGHT = 90

# ── 自动方向规则（⑦）—— 用户 2026-10-08 拍板 ──────────────────────────────
# 前提：**扫描件一律是竖着扫的**（扫描仪不管纸横竖，出来都是竖版）。
# 推论：如果一份原件的首页是**横向**的，那它的封面被竖着扫进来后内容必然是躺着的，
#       所以对应的那一页扫描件**默认左转 90°（逆时针，= 270）**把它扳正。
# ⚠️ 这条只是「默认值」，不是结论：人一眼就能看出对不对，改起来也就一个按钮。
# ⚠️ 自动值每次都按当前的【行 → 页】映射重算：页码一动（调页/反转页序），
#    「这一页是谁的封面」就变了，自动值跟着变才对。
AUTO_ROT_LANDSCAPE = 270          # 左转 90°（逆时针）
AUTO_ROT_UPRIGHT = 0              # 竖向原件：不用转

# 用户 2026-10-08 明确：「扫描件我人为定义必须竖着去打印机扫描的，不会存在横向的」。
# 所以下面这条守卫平时永远不会触发；真触发了说明这批纸不是竖扫的（扫描仪设置变了、
# 纸放错了），值得出声提醒一句 —— 静默跳过等于把异常藏起来。
NOTE_SCAN_LANDSCAPE = "这一页是横向的（说好扫描件都竖扫），已跳过自动左转"


def is_landscape(size: tuple[float, float]) -> bool:
    """原件首页是不是横向（宽 > 高）—— 用**显示尺寸**，/Rotate 90 的横页也算横。"""
    if not size or min(size) <= 0:
        return False
    return size[0] > size[1]


def auto_rotations(items: Sequence["Item"],
                   raw_sizes: Sequence[tuple[float, float]] | None = None
                   ) -> dict[int, int]:
    """按当前映射算出每页的**默认**方向：横向原件用的那一页 → 左转 90°。

    ⚠️ 前提「扫描件一律竖扫」不成立时要让路：这一页**本来就是横向**的，
       说明它已经是横着扫出来的（不是躺着的内容），再转一次反而转反。
    """
    out: dict[int, int] = {}
    for it in items:
        if not is_landscape(it.src_size):
            out[it.scan_index] = AUTO_ROT_UPRIGHT
            continue
        raw = (raw_sizes[it.scan_index - 1]
               if raw_sizes and 0 < it.scan_index <= len(raw_sizes) else it.scan_size)
        out[it.scan_index] = AUTO_ROT_UPRIGHT if is_landscape(raw) else AUTO_ROT_LANDSCAPE
    return out


def resolve_rotations(items: Sequence["Item"], manual: dict[int, int],
                      auto_on: bool = True,
                      raw_sizes: Sequence[tuple[float, float]] | None = None
                      ) -> dict[int, int]:
    """最终方向 = 人工 > 自动 > 正向。界面与命令行共用这一个口径（不许各算各的）。"""
    auto = auto_rotations(items, raw_sizes) if auto_on else {}
    return {
        it.scan_index: int(manual.get(it.scan_index, auto.get(it.scan_index, 0))) % 360
        for it in items
    }


def turn_deg(cur: int, delta: int) -> int:
    """左转 -90 / 右转 +90，循环回 0~270（转四次回到原样）。"""
    return (int(cur) + int(delta)) % 360


def rotated_size(size: tuple[float, float], deg: int) -> tuple[float, float]:
    """旋转后的显示尺寸 —— 90/270 时宽高互换，180 不变。"""
    if not size or min(size) <= 0:
        return size
    deg = int(deg) % 360
    return (size[1], size[0]) if deg in (90, 270) else size


def parse_rotations(raw: str) -> dict[int, int]:
    """命令行用的解析：「3:90,5:270」→ {3: 90, 5: 270}（页码 1 起）。"""
    out: dict[int, int] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"看不懂这段旋转设置：{part}（应该写成 页码:角度，例如 3:90）")
        page, deg = part.split(":", 1)
        d = int(deg.strip()) % 360
        if d not in ROT_ANGLES:
            raise ValueError(f"角度只能是 90 的倍数：{part}")
        out[int(page.strip())] = d
    return out


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 状态枚举
# ══════════════════════════════════════════════════════════════════════════

class Status(str, Enum):
    PENDING = "待替换"
    OK = "替换成功"
    FAILED = "替换失败"
    SKIPPED = "已跳过"


@dataclass
class Options:
    """选项开关（界面与命令行共用一套默认值）。"""

    src_dir: str = ""
    scan_pdf: str = ""
    out_dir: str = ""
    # 扫描仪出的是倒序（最后扫的在最前面）时打开
    reverse_order: bool = False
    scale_to_original: bool = True
    overwrite: bool = False
    recursive: bool = False
    suffix: str = ""
    order_rule: str = DEFAULT_ORDER
    keep_metadata: bool = True
    dry_run: bool = False
    # ⑥ 人工判的方向：{扫描页序号(1起): 角度}，没记的就用⑦的自动规则
    rotations: dict[int, int] = field(default_factory=dict)
    # ⑦ 自动方向：原件首页是横向的 → 它那一页默认左转 90°（前提：扫描件一律竖扫）
    auto_rotate: bool = True


# ══════════════════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Item:
    """一份原件 + 它对应的那一页扫描页。"""

    index: int                  # 1 起的排队序号
    src: Path
    src_pages: int = 0          # 0 = 读不出来
    src_size: tuple[float, float] = (0.0, 0.0)
    scan_index: int = 0         # 1 起的扫描页序号
    scan_size: tuple[float, float] = (0.0, 0.0)
    rotation: int = 0           # ⑥ 这一页人工判的方向（0 正向 / 90 右转 / 270 左转 / 180 倒转）
    status: str = Status.PENDING.value
    note: str = ""
    error: str = ""


@dataclass
class Plan:
    """一次预览的结果。blocked=True 时**一个文件都不许输出**。"""

    items: list[Item] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""
    scan_pages: int = 0
    scan_name: str = ""
    # 扫描件各页的**原始**尺寸（旋转前的）。⚠️ 不能靠 item.scan_size 反推 ——
    # 那个字段每次 apply_rotations 都会被改写，拿它当原始值会「转一次变一次」
    scan_raw_sizes: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class SwapResult:
    src: Path
    out: Path
    scan_index: int = 0
    rotation: int = 0
    ok: bool = False
    status: str = Status.PENDING.value
    fit_mode: str = ""
    fit_reason: str = ""
    pages_in: int = 0
    pages_out: int = 0
    note: str = ""


# ══════════════════════════════════════════════════════════════════════════
# 读 PDF
# ══════════════════════════════════════════════════════════════════════════

def _reader(path: Path):
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"PDF 已加密，读不了（{exc}）") from exc
    return reader


def display_size(page) -> tuple[float, float]:
    """页面「显示尺寸」：/Rotate 90/270 的页面按横放后的尺寸返回。"""
    box = page.mediabox
    w, h = float(box.width), float(box.height)
    if abs(float(getattr(page, "rotation", 0) or 0)) % 180 == 90:
        w, h = h, w
    return w, h


def read_pdf_info(path: Path) -> tuple[int, tuple[float, float], str]:
    """(页数, 首页显示尺寸, 错误信息)。出错返回 (0, (0,0), 原因)。"""
    try:
        reader = _reader(path)
        n = len(reader.pages)
        if n == 0:
            return 0, (0.0, 0.0), "里面没有任何页面"
        return n, display_size(reader.pages[0]), ""
    except Exception as exc:  # noqa: BLE001
        return 0, (0.0, 0.0), str(exc) or exc.__class__.__name__


def scan_page_sizes(path: Path) -> tuple[list[tuple[float, float]], str]:
    """扫描件每一页的显示尺寸。用于预览表格。"""
    try:
        reader = _reader(path)
        return [display_size(p) for p in reader.pages], ""
    except Exception as exc:  # noqa: BLE001
        return [], str(exc) or exc.__class__.__name__


# ══════════════════════════════════════════════════════════════════════════
# 收集原件
# ══════════════════════════════════════════════════════════════════════════

def _excluded_dir(out_dir: str) -> Path | None:
    """输出目录要排除在扫描范围外 —— 否则第二次跑会把上次的输出当原件。"""
    s = (out_dir or "").strip()
    return Path(s).expanduser() if s else None


def collect_originals(root: str | Path, options: Options) -> list[Path]:
    """按规则收原件：排除输出目录、排除扫描件自身。"""
    base = Path(root).expanduser()
    if not base.is_dir():
        return []
    out = _excluded_dir(options.out_dir)
    out_r = out.resolve() if out else None
    scan_r = Path(options.scan_pdf).expanduser().resolve() if options.scan_pdf.strip() else None

    hit: list[Path] = []
    it = base.rglob("*") if options.recursive else base.glob("*")
    for p in it:
        if not p.is_file() or p.suffix.lower() not in PDF_EXTS:
            continue
        try:
            pr = p.resolve()
        except OSError:
            continue
        if out_r and _is_under(pr, out_r):
            continue
        if scan_r and pr == scan_r:
            continue
        hit.append(p)
    return sort_originals(hit, base, options.order_rule)


def _is_under(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


# ══════════════════════════════════════════════════════════════════════════
# ② 阻断闸门表 —— 任一条命中，整批拒绝执行
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Ctx:
    """闸门判断要看的东西，一次算好传给所有闸门。"""

    src_dir: Path
    scan_pdf: Path
    originals: list[Path]
    scan_pages: int
    scan_err: str
    broken: list[tuple[Path, str]]     # 读不出页数的原件


def _g_no_src(ctx: Ctx) -> str:
    if not ctx.src_dir.is_dir():
        return f"原件文件夹不存在：{ctx.src_dir}"
    return ""


def _g_empty(ctx: Ctx) -> str:
    if not ctx.originals:
        return "原件文件夹里没有找到任何 PDF（输出文件夹和扫描件本身会被自动排除）"
    return ""


def _g_scan(ctx: Ctx) -> str:
    if not ctx.scan_pdf.is_file():
        return f"扫描件 PDF 不存在：{ctx.scan_pdf}"
    if ctx.scan_err:
        return f"扫描件读不出来：{ctx.scan_err}"
    if ctx.scan_pages <= 0:
        return "扫描件里没有任何页面"
    return ""


def _g_count(ctx: Ctx) -> str:
    n_src, n_scan = len(ctx.originals), ctx.scan_pages
    if n_src == n_scan:
        return ""
    diff = abs(n_src - n_scan)
    return (f"数量对不上：原件 {n_src} 份、扫描件 {n_scan} 页（差 {diff}）。\n"
            f"顺序配对下缺一个或多余一个都会让后面全部错位，所以整批不执行。\n"
            f"请补扫 / 删掉多余的，让两边数量一致再跑。")


def _g_broken(ctx: Ctx) -> str:
    if not ctx.broken:
        return ""
    names = "、".join(p.name for p, _e in ctx.broken[:5])
    more = f" 等 {len(ctx.broken)} 份" if len(ctx.broken) > 5 else ""
    return (f"这几份原件读不出页数，顺序会错乱，整批不执行：{names}{more}\n"
            f"　{ctx.broken[0][1]}")


class Gate(NamedTuple):
    name: str
    check: Callable[[Ctx], str]


GATE_RULES: tuple[Gate, ...] = (
    Gate("原件文件夹", _g_no_src),
    Gate("有没有原件", _g_empty),
    Gate("扫描件能否读取", _g_scan),
    Gate("数量是否相等", _g_count),
    Gate("原件是否都读得出", _g_broken),
)


# ══════════════════════════════════════════════════════════════════════════
# 预览
# ══════════════════════════════════════════════════════════════════════════

def _scan_index_for(i: int, total: int, reverse: bool) -> int:
    """第 i 份原件（1 起）用扫描件第几页（1 起）。"""
    return (total - i + 1) if reverse else i


def plan_batch(options: Options) -> Plan:
    src_dir = Path(options.src_dir).expanduser()
    scan_pdf = Path(options.scan_pdf).expanduser()

    originals = collect_originals(src_dir, options)
    sizes, scan_err = scan_page_sizes(scan_pdf) if scan_pdf.is_file() else ([], "文件不存在")
    ctx = Ctx(
        src_dir=src_dir,
        scan_pdf=scan_pdf,
        originals=originals,
        scan_pages=len(sizes),
        scan_err=scan_err,
        broken=[],
    )
    plan = Plan(scan_pages=len(sizes), scan_name=scan_pdf.name)

    # 读每一份原件的页数/首页尺寸（数量闸门要先知道份数，所以放在闸门之后更省事，
    # 但损坏清单是闸门之一 —— 这里先读，再过闸门）
    infos: list[tuple[int, tuple[float, float], str]] = []
    for p in originals:
        n, size, err = read_pdf_info(p)
        infos.append((n, size, err))
        if n == 0:
            ctx.broken.append((p, err))

    for gate in GATE_RULES:
        why = gate.check(ctx)
        if why:
            plan.blocked = True
            plan.block_reason = why
            return plan

    total = len(originals)
    for i, p in enumerate(originals, start=1):
        n, size, _err = infos[i - 1]
        si = _scan_index_for(i, total, options.reverse_order)
        plan.items.append(Item(
            index=i, src=p, src_pages=n, src_size=size,
            scan_index=si, scan_size=sizes[si - 1] if 0 < si <= len(sizes) else (0.0, 0.0),
            status=Status.PENDING.value,
        ))
    plan.scan_raw_sizes = list(sizes)
    apply_rotations(plan, options.rotations, auto_on=options.auto_rotate)
    return plan


def apply_rotations(plan: Plan, manual: dict[int, int],
                    raw_sizes: Sequence[tuple[float, float]] | None = None,
                    auto_on: bool = True) -> None:
    """把「最终方向」写回 plan（人工 > 自动 > 正向），并同步扫描页的显示尺寸。

    可以反复调用：每次都从**原始**尺寸重新算，不会把上一次的结果当输入。

    ⚠️ 人工值记在**扫描页**上（那张纸确实是横版内容被竖着扫进来的），页码怎么调都跟着纸走。
    ⚠️ 自动值每次按当前的【行→页】映射**重算** —— 这一页到底是谁的封面变了，
       默认方向就得跟着变（横版原件的封面才需要转）。
    """
    raw = list(raw_sizes) if raw_sizes else plan.scan_raw_sizes
    final = resolve_rotations(plan.items, manual, auto_on, raw)
    for it in plan.items:
        it.rotation = final.get(it.scan_index, 0)
        base = raw[it.scan_index - 1] if 0 < it.scan_index <= len(raw) else it.scan_size
        it.scan_size = rotated_size(base, it.rotation)
        # 横向原件 + 横向扫描页：自动左转被跳过，出声说明原因（见 NOTE_SCAN_LANDSCAPE）
        if auto_on and is_landscape(it.src_size) and is_landscape(base) \
                and it.scan_index not in manual:
            it.note = NOTE_SCAN_LANDSCAPE


def reindex(plan: Plan, reverse: bool) -> None:
    """切「正序 / 倒序」：整批重算扫描页序号（原件顺序不动）。

    ⚠️ 这是「重来一遍」而不是「把当前结果再反一次」：手动调过的页码会被覆盖掉 ——
       按钮上写着当前是正序还是倒序，来回点是幂等的，猜不出自己按了几次。
    """
    total = len(plan.items)
    for it in plan.items:
        it.scan_index = _scan_index_for(it.index, total, reverse)


def assign_page(plan: Plan, item_index: int, page: int) -> tuple[bool, str]:
    """把第 item_index 行（1 起）改成用扫描件第 page 页。

    ⭐ 一页只许一个人用：目标页已经被别的行占着时**直接对调**，
       而不是让两行都用同一页（那样必然有一份原件拿不到自己的封面）。
    返回 (有没有变化, 人话说明)。
    """
    n = len(plan.items)
    if not (1 <= item_index <= n) or page <= 0 or page > plan.scan_pages:
        return False, ""
    it = plan.items[item_index - 1]
    if it.scan_index == page:
        return False, ""
    old = it.scan_index
    other = next((x for x in plan.items
                  if x is not it and x.scan_index == page), None)
    it.scan_index = page
    if other is not None:
        other.scan_index = old
        return True, f"和《{other.src.name}》对调了（它改成用第 {old} 页）"
    return True, f"改用第 {page} 页（第 {old} 页现在没人用）"


def swap_pages(plan: Plan, item_index: int, delta: int) -> bool:
    """与相邻行对调扫描页（delta=-1 与上一行、+1 与下一行）。

    行本身不动 —— 行的顺序就是原件在文件夹里的名称顺序，那是唯一的排队依据；
    能调的只有「这一行用哪一页」。
    """
    n = len(plan.items)
    j = item_index + delta
    if not (1 <= item_index <= n) or not (1 <= j <= n):
        return False
    a, b = plan.items[item_index - 1], plan.items[j - 1]
    a.scan_index, b.scan_index = b.scan_index, a.scan_index
    return True


# ══════════════════════════════════════════════════════════════════════════
# ④ 输出文件名后缀
# ══════════════════════════════════════════════════════════════════════════

SUFFIX_FORBIDDEN = frozenset('\\/:*?"<>|') | {chr(i) for i in range(32)}
SUFFIX_MAX_LEN = 40


def clean_output_suffix(raw: str) -> str:
    """后缀规范化唯一口径（界面与命令行共用，避免规则漂移）。

    刻意不做「自动补下划线」的猜测 —— 填什么就是什么。
    """
    s = (raw or "").strip()
    s = "".join(ch for ch in s if ch not in SUFFIX_FORBIDDEN)
    s = s.strip(".")
    return s[:SUFFIX_MAX_LEN]


def out_name_for(src: Path, suffix: str) -> str:
    s = clean_output_suffix(suffix)
    return f"{src.stem}{s}{src.suffix}"


def _unique_out_path(base: Path, used: set[Path]) -> Path:
    """不覆盖模式：重名自动加编号 xxx_2.pdf、xxx_3.pdf。"""
    if base not in used:
        used.add(base)
        return base
    stem, suf, parent = base.stem, base.suffix, base.parent
    k = 2
    while True:
        cand = parent / f"{stem}_{k}{suf}"
        if cand not in used:
            used.add(cand)
            return cand
        k += 1


# ══════════════════════════════════════════════════════════════════════════
# 首页替换（沿用 v1 已验证的贴合逻辑，区别：取扫描件【第 N 页】）
# ══════════════════════════════════════════════════════════════════════════

def decide_fit(src_disp: tuple[float, float], scan_disp: tuple[float, float],
               tolerance: float = FIT_ASPECT_TOLERANCE) -> tuple[str, str]:
    """③ 贴合决策：返回 (FIT_FIT / FIT_ASIS, 人话原因)。"""
    if min(src_disp) <= 0 or min(scan_disp) <= 0:
        return FIT_ASIS, "尺寸信息缺失，原样保留扫描页"
    r_src = src_disp[0] / src_disp[1]
    r_scan = scan_disp[0] / scan_disp[1]
    diff = abs(r_src - r_scan) / max(r_src, r_scan)
    if diff <= tolerance:
        return FIT_FIT, f"版面比例接近（宽高比差异 {diff:.0%}），按原件首页尺寸等比缩放居中"
    return FIT_ASIS, (f"版面比例不一致（原件 {r_src:.2f} vs 扫描页 {r_scan:.2f}），"
                      f"扫描页原样作为第 1 页，不硬塞进原页面")


def _prepare_scan_page(scan_page, tw: float, th: float):
    """把扫描页等比缩放并居中到 (tw, th) 上。"""
    from pypdf import Transformation

    try:  # pypdf >= 3.16：把 /Rotate 烘进内容流，避免旋转页错位
        scan_page.transfer_rotation_to_content()
    except Exception:  # noqa: BLE001
        pass

    box = scan_page.mediabox
    sw, sh = float(box.width), float(box.height)
    if sw <= 0 or sh <= 0:
        return scan_page
    k = min(tw / sw, th / sh)
    ox = (tw - sw * k) / 2.0
    oy = (th - sh * k) / 2.0
    scan_page.add_transformation(Transformation().scale(k, k).translate(ox, oy))
    scan_page.mediabox.lower_left = (0, 0)
    scan_page.mediabox.upper_right = (tw, th)
    return scan_page


def swap_first_page(src_path: Path, scan_path: Path, scan_index: int,
                    out_path: Path, options: Options,
                    rotation: int = 0) -> SwapResult:
    """核心动作：输出 = [扫描件第 scan_index 页] + [原件第 2..N 页]。

    ⚠️ 扫描件**每份原件重新打开一次**：_prepare_scan_page 会就地改页面对象，
       复用同一个 reader 的 page 会让第二份输出被缩放两次。

    ⑥ rotation：人工判的方向（正数顺时针）。必须先 rotate 再 transfer_rotation_to_content
       —— 后者会把 /Rotate 烘进内容流并交换 mediabox，之后 display_size 才是转好的尺寸，
       贴合决策要用转好的尺寸去比，否则会拿竖着的尺寸去套横着的原件。
    """
    from pypdf import PdfReader, PdfWriter

    res = SwapResult(src=src_path, out=out_path, scan_index=scan_index,
                     rotation=int(rotation) % 360)
    try:
        src_reader = _reader(src_path)
        n_in = len(src_reader.pages)
        if n_in == 0:
            raise RuntimeError("原 PDF 内没有任何页面")
        res.pages_in = n_in

        scan_reader = _reader(scan_path)
        if not (1 <= scan_index <= len(scan_reader.pages)):
            raise RuntimeError(f"扫描件只有 {len(scan_reader.pages)} 页，取不到第 {scan_index} 页")
        scan_page = scan_reader.pages[scan_index - 1]

        # ⑥ 先转向，再把旋转烘进内容流（此后 mediabox 已是转好的尺寸）
        if res.rotation:
            scan_page.rotate(res.rotation)
        try:
            scan_page.transfer_rotation_to_content()
        except Exception:  # noqa: BLE001
            pass

        mode, reason = (FIT_ASIS, "已关闭缩放，扫描页原样作为第 1 页")
        if options.scale_to_original:
            mode, reason = decide_fit(display_size(src_reader.pages[0]), display_size(scan_page))
        res.fit_mode, res.fit_reason = mode, reason

        writer = PdfWriter()
        if mode == FIT_FIT:
            tw, th = display_size(src_reader.pages[0])
            writer.add_blank_page(width=tw, height=th)
            writer.pages[-1].merge_page(_prepare_scan_page(scan_page, tw, th))
        else:
            writer.add_page(scan_page)

        for idx, page in enumerate(src_reader.pages):
            if idx == 0:
                continue
            writer.add_page(page)

        if options.keep_metadata and src_reader.metadata:
            try:
                writer.add_metadata({k: str(v) for k, v in src_reader.metadata.items()})
            except Exception:  # noqa: BLE001
                pass

        res.pages_out = len(writer.pages)

        if options.dry_run:
            res.ok = True
            res.status = Status.OK.value
            res.note = "演练：没有真的写文件"
            return res

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".part")
        with open(tmp, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, out_path)

        res.ok = True
        res.status = Status.OK.value
        res.note = reason if mode == FIT_ASIS else ""
        return res
    except Exception as exc:  # noqa: BLE001
        res.ok = False
        res.status = Status.FAILED.value
        res.note = str(exc) or exc.__class__.__name__
        return res


def run_batch(plan: Plan, options: Options, out_dir: Path
              ) -> tuple[list[SwapResult], list[str]]:
    """按预览的顺序逐个替换。返回 (结果, 提示语)。"""
    if plan.blocked:
        return [], [plan.block_reason or "被阻断，未执行"]

    used: set[Path] = set()
    if not options.overwrite and out_dir.is_dir():
        used = set(out_dir.rglob("*")) if options.recursive else set(out_dir.glob("*"))

    base = Path(options.src_dir).expanduser()
    results: list[SwapResult] = []
    for it in plan.items:
        if it.status == Status.SKIPPED.value:
            results.append(SwapResult(src=it.src, out=Path(""), scan_index=it.scan_index,
                                      ok=True, status=Status.SKIPPED.value,
                                      note="你手动忽略了这一行"))
            continue
        target = out_dir / _out_sub(it.src, base, options.recursive) \
            / out_name_for(it.src, options.suffix)
        if not options.overwrite:
            target = _unique_out_path(target, used)
        results.append(swap_first_page(it.src, Path(options.scan_pdf).expanduser(),
                                       it.scan_index, target, options,
                                       rotation=it.rotation))
    return results, []


def _out_sub(src: Path, base: Path, recursive: bool) -> Path:
    """递归模式下输出复刻子目录结构，避免不同子目录的同名文件互相打架。"""
    if not recursive:
        return Path("")
    try:
        rel = src.relative_to(base)
    except ValueError:
        return Path("")
    return rel.parent if str(rel.parent) != "." else Path("")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="seq_core.py",
        description=f"{TOOL_NAME} v{TOOL_VERSION}（命令行）",
    )
    p.add_argument("--src", required=True, help="原件文件夹")
    p.add_argument("--scan", required=True, help="一次扫完的多页扫描件 PDF")
    p.add_argument("--out", default="", help="输出文件夹（留空用 原件文件夹/输出_已替换）")
    p.add_argument("--reverse", action="store_true", help="扫描件页序倒序")
    p.add_argument("--no-scale", action="store_true", help="不缩放，扫描页原样作为第 1 页")
    p.add_argument("--overwrite", action="store_true", help="覆盖输出目录里的同名文件")
    p.add_argument("--recursive", action="store_true", help="连同子文件夹一起处理")
    p.add_argument("--suffix", default="", help="输出文件名后缀")
    p.add_argument("--order", default=DEFAULT_ORDER,
                   choices=[r.key for r in ORDER_RULES],
                   help="原件排队规则（默认 name = 文件夹里按名称看到的顺序）")
    p.add_argument("--rotate", default="",
                   help="人工判的方向：「页码:角度,页码:角度」，例 3:90,5:270（正数向右转）")
    p.add_argument("--no-auto-rotate", action="store_true",
                   help="关掉自动方向（默认：原件首页是横向的，那一页自动左转 90°）")
    p.add_argument("--dry-run", action="store_true", help="只演练不写文件")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    a = _build_parser().parse_args(argv)
    src = Path(a.src).expanduser()
    out = Path(a.out).expanduser() if a.out.strip() else src / AUTO_OUT_NAME
    try:
        rots = parse_rotations(a.rotate)
    except ValueError as exc:
        print(f"旋转设置写错了：{exc}")
        return 2
    opts = Options(src_dir=str(src), scan_pdf=a.scan, out_dir=str(out),
                   reverse_order=a.reverse, scale_to_original=not a.no_scale,
                   overwrite=a.overwrite, recursive=a.recursive,
                   suffix=a.suffix, order_rule=a.order, dry_run=a.dry_run,
                   rotations=rots, auto_rotate=not a.no_auto_rotate)
    plan = plan_batch(opts)
    if plan.blocked:
        print("整批不执行：")
        print("  " + plan.block_reason.replace("\n", "\n  "))
        return 2
    print(f"原件 {len(plan.items)} 份，扫描件 {plan.scan_pages} 页，输出到 {out}")
    results, _notes = run_batch(plan, opts, out)
    ok = sum(1 for r in results if r.status == Status.OK.value)
    bad = [r for r in results if r.status == Status.FAILED.value]
    print(f"完成 {ok} 份，失败 {len(bad)} 份")
    for r in bad:
        print(f"  [失败] {r.src.name} —— {r.note}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
