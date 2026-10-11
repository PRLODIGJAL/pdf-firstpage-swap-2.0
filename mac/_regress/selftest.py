#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心自测 —— 全部产物写在系统临时目录，不污染桌面。

跑法：.venv/bin/python -B _regress/selftest.py
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import seq_core as C  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK ' if cond else 'NG '} {name}" + (f"  <{detail}>" if detail and not cond else ""))


def _raises(fn) -> bool:
    """这个调用应该抛异常（错了要炸，不能静默退回默认）。"""
    try:
        fn()
    except Exception:  # noqa: BLE001
        return True
    return False


# ── 纯标准库构造 PDF（每页写一句不同的文字，好验证内容有没有被换掉）─────────
def build_pdf(path: Path, pages: list[tuple[float, float, str]]) -> None:
    objs: dict[int, bytes] = {}
    page_nums = [4 + 2 * i for i in range(len(pages))]
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{n} 0 R" for n in page_nums)
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for i, (w, h, label) in enumerate(pages):
        pn, cn = page_nums[i], page_nums[i] + 1
        objs[pn] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {cn} 0 R >>"
        ).encode()
        stream = f"BT /F1 28 Tf 60 100 Td ({label}) Tj ET".encode()
        objs[cn] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n")
    xref_pos = out.tell()
    max_num = max(objs) + 1
    out.write(f"xref\n0 {max_num}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for num in range(1, max_num):
        off = offsets.get(num)
        out.write((f"{off:010d} 00000 n \n" if off else "0000000000 65535 f \n").encode())
    out.write(
        f"trailer\n<< /Size {max_num} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out.getvalue())


def page_texts(pdf: Path) -> list[str]:
    from pypdf import PdfReader

    return [(p.extract_text() or "").strip() for p in PdfReader(str(pdf)).pages]


def _disp(pdf: Path) -> list[tuple[float, float]]:
    """每页的「显示尺寸」（含 /Rotate 修正），用来验旋转有没有真的生效。"""
    from pypdf import PdfReader

    return [(round(float(b[0]), 1), round(float(b[1]), 1))
            for b in (C.display_size(p) for p in PdfReader(str(pdf)).pages)]


def _has_rot_matrix(pdf: Path, page: int = 0) -> bool:
    """内容流里有没有带 90° 旋转分量的 cm 矩阵（a b c d e f cm 里的 b、c 非零）。

    只比 mediabox 是不够的 —— 框变大了但内容没转，拼出来还是躺着的。
    """
    import re
    from pypdf import PdfReader

    try:
        data = PdfReader(str(pdf)).pages[page].get_contents().get_data().decode("latin-1")
    except Exception:  # noqa: BLE001
        return False
    for m in re.finditer(r"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+"
                         r"([-\d.]+)\s+([-\d.]+)\s+cm", data):
        try:
            _a, b, c, _d, _e, _f = (float(m.group(i)) for i in range(1, 7))
        except ValueError:
            continue
        if abs(b) > 1e-6 and abs(c) > 1e-6:
            return True
    return False


def page_sizes(pdf: Path) -> list[tuple[float, float]]:
    from pypdf import PdfReader

    out = []
    for p in PdfReader(str(pdf)).pages:
        b = p.mediabox
        out.append((round(float(b.width), 1), round(float(b.height), 1)))
    return out


def make_corpus(root: Path, n_original: int, pages_each: int,
                n_scan: int, size=(595.0, 842.0)) -> tuple[Path, Path]:
    """造语料：原件 001..N（每份 pages_each 页），扫描件 n_scan 页。"""
    src_d = root / "原件"
    src_d.mkdir(parents=True, exist_ok=True)
    for i in range(1, n_original + 1):
        build_pdf(src_d / f"{i:03d}.pdf",
                  [(size[0], size[1], f"ORI{i}-P{j}") for j in range(1, pages_each + 1)])
    scan = root / "扫描件_签字版.pdf"
    build_pdf(scan, [(size[0], size[1], f"SCAN-{k}") for k in range(1, n_scan + 1)])
    return src_d, scan


def main() -> int:
    root = Path(tempfile.gettempdir()) / "pdfseq_selftest"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    print(f"临时工作目录：{root}\n")

    # ── [1] 文件名顺序（＝文件夹里按「名称」看到的顺序） ──────────────────
    print("[1] 排序：文件名顺序")
    names = ["010.pdf", "002.pdf", "001.pdf", "100.pdf", "020.pdf"]
    paths = sorted((Path(n) for n in names), key=lambda p: C.natural_key(p.name))
    got = [p.name for p in paths]
    check("010 不会排到 002 前面（不是字典序）",
          got == ["001.pdf", "002.pdf", "010.pdf", "020.pdf", "100.pdf"], str(got))
    mixed = ["WI-9.pdf", "WI-10.pdf", "WI-100.pdf", "WI-2.pdf"]
    got2 = [p.name for p in sorted((Path(n) for n in mixed),
                                   key=lambda p: C.natural_key(p.name))]
    check("带前缀的编号也按数值排",
          got2 == ["WI-2.pdf", "WI-9.pdf", "WI-10.pdf", "WI-100.pdf"], str(got2))

    # 2026-10-08 用户拍板：不改名成 001/002，直接用 WI-xxxx 原名排
    real = ["WI-3852.pdf", "WI-1032.pdf", "WI-2001.pdf", "WI-999.pdf"]
    got1b = [p.name for p in sorted((Path(n) for n in real),
                                    key=lambda p: C.natural_key(p.name))]
    check("真实 WI 原名按名称排（999 在 1032 前、3852 在最后）",
          got1b == ["WI-999.pdf", "WI-1032.pdf", "WI-2001.pdf", "WI-3852.pdf"], str(got1b))
    case_mixed = ["banana.pdf", "Apple.pdf", "cherry.pdf"]
    got1c = [p.name for p in sorted((Path(n) for n in case_mixed),
                                    key=lambda p: C.natural_key(p.name))]
    check("字母不分大小写（和资源管理器一致）",
          got1c == ["Apple.pdf", "banana.pdf", "cherry.pdf"], str(got1c))
    check("默认排序规则就是「文件名顺序」", C.DEFAULT_ORDER == "name", C.DEFAULT_ORDER)
    check("排序表里能查到文件名顺序这条",
          any(r.key == "name" for r in C.ORDER_RULES))
    check("改名成 001/002 不再是前提（排序键不吃「必须是纯数字」这一套）",
          [p.name for p in sorted((Path(n) for n in ["WI-1032.pdf", "WI-9.pdf"]),
                                  key=lambda p: C.natural_key(p.name))]
          == ["WI-9.pdf", "WI-1032.pdf"])

    # 备用规则：按修改时间（原件没有编号时的退路）
    w_mt = root / "按时间排"
    w_mt.mkdir(parents=True)
    a_p, b_p, c_p = w_mt / "a.pdf", w_mt / "b.pdf", w_mt / "c.pdf"
    for p in (a_p, b_p, c_p):
        p.write_bytes(b"%PDF-1.4\n")
    # 故意让修改时间的先后（b → c → a）和文件名顺序（a → b → c）不一样
    for k, p in enumerate([b_p, c_p, a_p]):
        os.utime(p, (1_700_000_000 + k * 100, 1_700_000_000 + k * 100))
    got_mt = [p.name for p in C.sort_originals([a_p, b_p, c_p], w_mt, "mtime")]
    check("备用规则：按修改时间从早到晚（b → c → a）",
          got_mt == ["b.pdf", "c.pdf", "a.pdf"], str(got_mt))
    got_nm = [p.name for p in C.sort_originals([c_p, a_p, b_p], w_mt, "name")]
    check("同一批文件用文件名顺序又是另一回事（a → b → c）",
          got_nm == ["a.pdf", "b.pdf", "c.pdf"], str(got_nm))
    check("传一个不存在的规则会直接报错（不静默退回默认）",
          _raises(lambda: C.sort_originals([a_p], w_mt, "不存在的规则")))

    # ── [2] 收集原件：排除输出目录、排除扫描件自身 ────────────────────────
    print("\n[2] 收集原件")
    w = root / "收集"
    src_d, scan = make_corpus(w, 3, 2, 3)
    out_d = w / "输出"
    out_d.mkdir(parents=True)
    build_pdf(out_d / "上次产物.pdf", [(595, 842, "OLD")])
    opts = C.Options(src_dir=str(src_d), scan_pdf=str(scan), out_dir=str(out_d))
    got3 = [p.name for p in C.collect_originals(src_d, opts)]
    check("只收原件，输出目录里的成品不吃回来",
          got3 == ["001.pdf", "002.pdf", "003.pdf"], str(got3))

    # 扫描件就放在原件文件夹里（用户最可能的摆法）—— 它绝不能被当成一份原件
    w2 = root / "扫描件混在里面"
    src2, scan2 = make_corpus(w2, 3, 2, 3)
    shutil.move(str(scan2), str(src2 / "zz扫描件.pdf"))
    opts2 = C.Options(src_dir=str(src2), scan_pdf=str(src2 / "zz扫描件.pdf"))
    got4 = [p.name for p in C.collect_originals(src2, opts2)]
    check("扫描件本身放在原件文件夹里会被排除",
          got4 == ["001.pdf", "002.pdf", "003.pdf"], str(got4))

    # 递归
    w3 = root / "递归"
    src3, scan3 = make_corpus(w3, 2, 2, 4)
    (src3 / "子目录").mkdir()
    build_pdf(src3 / "子目录" / "003.pdf", [(595, 842, "ORI3-P1"), (595, 842, "ORI3-P2")])
    build_pdf(src3 / "子目录" / "004.pdf", [(595, 842, "ORI4-P1"), (595, 842, "ORI4-P2")])
    opts3 = C.Options(src_dir=str(src3), scan_pdf=str(scan3), recursive=True)
    got5 = [p.name for p in C.collect_originals(src3, opts3)]
    check("递归：子文件夹里的原件也进队", len(got5) == 4, str(got5))
    opts3b = C.Options(src_dir=str(src3), scan_pdf=str(scan3), recursive=False)
    check("不递归：只收当前这一层", len(C.collect_originals(src3, opts3b)) == 2)

    # ── [3] 阻断闸门 ─────────────────────────────────────────────────────
    print("\n[3] 阻断闸门（有问题就整批不输出）")
    g = root / "闸门"
    src_a, scan_a = make_corpus(g, 5, 2, 5)

    p1 = C.plan_batch(C.Options(src_dir=str(g / "不存在"), scan_pdf=str(scan_a)))
    check("原件文件夹不存在 → 阻断", p1.blocked and "不存在" in p1.block_reason, p1.block_reason)

    empty = g / "空文件夹"
    empty.mkdir()
    p2 = C.plan_batch(C.Options(src_dir=str(empty), scan_pdf=str(scan_a)))
    check("原件文件夹里没有 PDF → 阻断", p2.blocked, p2.block_reason)

    p3 = C.plan_batch(C.Options(src_dir=str(src_a), scan_pdf=str(g / "没有这个.pdf")))
    check("扫描件不存在 → 阻断", p3.blocked and "不存在" in p3.block_reason, p3.block_reason)

    bad = g / "坏扫描件.pdf"
    bad.write_bytes(b"not a pdf at all")
    p4 = C.plan_batch(C.Options(src_dir=str(src_a), scan_pdf=str(bad)))
    check("扫描件读不出来 → 阻断", p4.blocked, p4.block_reason)

    # 数量对不上（用两个独立目录造语料，避免互相覆盖）
    src_many, scan_short = make_corpus(g / "少页", 5, 2, 3)
    p5 = C.plan_batch(C.Options(src_dir=str(src_many), scan_pdf=str(scan_short)))
    check("原件 5 份 vs 扫描件 3 页 → 阻断",
          p5.blocked and "5" in p5.block_reason and "3" in p5.block_reason, p5.block_reason)
    check("阻断原因里写清差几份", "差 2" in p5.block_reason, p5.block_reason)

    src_eq, scan_eq = make_corpus(g / "相等", 5, 2, 5)
    p5b = C.plan_batch(C.Options(src_dir=str(src_eq), scan_pdf=str(scan_eq)))
    check("两边都是 5 → 不阻断", not p5b.blocked, p5b.block_reason)

    src_few, scan_many = make_corpus(g / "多页", 3, 2, 5)
    p5c = C.plan_batch(C.Options(src_dir=str(src_few), scan_pdf=str(scan_many)))
    check("扫描件比原件多也照样阻断",
          p5c.blocked and "差 2" in p5c.block_reason, p5c.block_reason)

    src6, scan6 = make_corpus(g / "坏原件", 3, 2, 3)
    (src6 / "002.pdf").write_bytes(b"broken")
    p6 = C.plan_batch(C.Options(src_dir=str(src6), scan_pdf=str(scan6)))
    check("原件读不出页数 → 阻断（顺序会错乱）",
          p6.blocked and "002.pdf" in p6.block_reason, p6.block_reason)

    # ── [4] 顺序配对 ─────────────────────────────────────────────────────
    print("\n[4] 顺序配对")
    w4 = root / "配对"
    src_b, scan_b = make_corpus(w4, 4, 2, 4)
    plan = C.plan_batch(C.Options(src_dir=str(src_b), scan_pdf=str(scan_b)))
    check("配对数量正确", len(plan.items) == 4)
    check("第 N 份原件 ← 扫描件第 N 页",
          [it.scan_index for it in plan.items] == [1, 2, 3, 4],
          str([it.scan_index for it in plan.items]))
    check("原件顺序就是 001/002/003/004",
          [it.src.name for it in plan.items] == ["001.pdf", "002.pdf", "003.pdf", "004.pdf"])
    C.reindex(plan, True)
    check("倒序后 001 用最后一页",
          [it.scan_index for it in plan.items] == [4, 3, 2, 1],
          str([it.scan_index for it in plan.items]))
    C.reindex(plan, False)
    check("切回正序", [it.scan_index for it in plan.items] == [1, 2, 3, 4])

    # 调页码：行不动（行的顺序＝名称序，那是唯一的排队依据），只换「用哪一页」
    check("与下一行对调：两行的页码互换",
          C.swap_pages(plan, 1, 1) is True
          and [it.scan_index for it in plan.items] == [2, 1, 3, 4],
          str([it.scan_index for it in plan.items]))
    check("对调时行的顺序一动不动",
          [it.src.name for it in plan.items] == ["001.pdf", "002.pdf", "003.pdf", "004.pdf"])
    C.swap_pages(plan, 1, 1)
    check("再对调一次换回来（同一个按钮点两次 = 没变）",
          [it.scan_index for it in plan.items] == [1, 2, 3, 4])
    check("首行往上一行对调会失败（它没有上一行）", C.swap_pages(plan, 1, -1) is False)
    check("末行往下一行对调会失败（它没有下一行）", C.swap_pages(plan, 4, 1) is False)
    check("指定超出扫描件范围的页码 → 拒绝", C.assign_page(plan, 1, 99)[0] is False)

    changed, note = C.assign_page(plan, 4, 2)
    check("把第 4 行改成用第 2 页 → 原先用第 2 页的那行自动改成第 4 页",
          changed and [it.scan_index for it in plan.items] == [1, 4, 3, 2],
          str([it.scan_index for it in plan.items]))
    check("一页不会被两行同时用（4 行 4 个不同页码）",
          len({it.scan_index for it in plan.items}) == 4)
    check("对调说明里点名了另一份原件", "002.pdf" in note, note)
    check("改成自己已经在用的页码 → 无事发生",
          C.assign_page(plan, 1, 1)[0] is False)

    # ── [5] 替换结果 ─────────────────────────────────────────────────────
    print("\n[5] 替换结果")
    w5 = root / "替换"
    src_c, scan_c = make_corpus(w5, 3, 3, 3)
    out_c = w5 / "输出"
    before = {p.name: page_texts(p) for p in src_c.glob("*.pdf")}
    plan_c = C.plan_batch(C.Options(src_dir=str(src_c), scan_pdf=str(scan_c),
                                    out_dir=str(out_c)))
    results, _ = C.run_batch(plan_c, C.Options(src_dir=str(src_c), scan_pdf=str(scan_c),
                                              out_dir=str(out_c)), out_c)
    check("全部成功", all(r.ok for r in results),
          str([(r.src.name, r.note) for r in results if not r.ok]))
    for i, r in enumerate(results, start=1):
        texts = page_texts(r.out)
        check(f"001…00{i}：第 1 页是扫描件第 {i} 页",
              texts and f"SCAN-{i}" in texts[0], str(texts[:1]))
        check(f"001…00{i}：第 2 页起还是原件的内容",
              texts[1:] == before[f"{i:03d}.pdf"][1:], str(texts[1:]))
        check(f"001…00{i}：页数没变（3 页）", len(texts) == 3, str(len(texts)))
    check("原件本身没被改动",
          {p.name: page_texts(p) for p in src_c.glob("*.pdf")} == before)
    check("输出第 1 页尺寸 = 原件首页尺寸（贴合）",
          page_sizes(results[0].out)[0] == (595.0, 842.0), str(page_sizes(results[0].out)[0]))

    # 关掉缩放
    out_c2 = w5 / "输出_原样"
    opts_asis = C.Options(src_dir=str(src_c), scan_pdf=str(scan_c), out_dir=str(out_c2),
                          scale_to_original=False, overwrite=True)
    plan_c2 = C.plan_batch(opts_asis)
    results2, _ = C.run_batch(plan_c2, opts_asis, out_c2)
    check("关掉缩放后依然成功", all(r.ok for r in results2))
    check("关掉缩放：第 1 页是扫描页原尺寸",
          page_sizes(results2[0].out)[0] == (595.0, 842.0))

    # 不同尺寸：验证「贴合」真的在缩放
    w6 = root / "异形"
    src_d2 = w6 / "原件"
    src_d2.mkdir(parents=True)
    for i in range(1, 3):
        build_pdf(src_d2 / f"{i:03d}.pdf",
                  [(400.0, 400.0, f"ORI{i}-P1"), (400.0, 400.0, f"ORI{i}-P2")])
    scan_d2 = w6 / "扫描件.pdf"
    build_pdf(scan_d2, [(800.0, 800.0, "SCAN-1"), (800.0, 800.0, "SCAN-2")])
    out_d2 = w6 / "输出"
    opts_d = C.Options(src_dir=str(src_d2), scan_pdf=str(scan_d2), out_dir=str(out_d2),
                       overwrite=True)
    plan_d = C.plan_batch(opts_d)
    res_d, _ = C.run_batch(plan_d, opts_d, out_d2)
    check("比例一致时缩放到原件首页尺寸",
          page_sizes(res_d[0].out)[0] == (400.0, 400.0), str(page_sizes(res_d[0].out)[0]))

    # 比例差太多 → 原样
    w7 = root / "比例不同"
    src_e = w7 / "原件"
    src_e.mkdir(parents=True)
    for i in range(1, 3):
        build_pdf(src_e / f"{i:03d}.pdf",
                  [(842.0, 595.0, f"ORI{i}-P1"), (842.0, 595.0, f"ORI{i}-P2")])
    scan_e = w7 / "扫描件.pdf"
    build_pdf(scan_e, [(595.0, 842.0, "SCAN-1"), (595.0, 842.0, "SCAN-2")])
    out_e = w7 / "输出"
    # 关掉⑦自动方向：竖向扫描页套不进横向原件 → 原样保留，不硬塞
    opts_e = C.Options(src_dir=str(src_e), scan_pdf=str(scan_e), out_dir=str(out_e),
                       overwrite=True, auto_rotate=False)
    plan_e = C.plan_batch(opts_e)
    res_e, _ = C.run_batch(plan_e, opts_e, out_e)
    check("关掉自动方向：版面比例差太多 → 原样保留，不硬塞",
          res_e[0].fit_mode == C.FIT_ASIS and page_sizes(res_e[0].out)[0] == (595.0, 842.0),
          f"{res_e[0].fit_mode}/{page_sizes(res_e[0].out)[0]}")

    # 默认（开着自动方向）：扫描页先左转 90° 变横向 → 正好贴合，不再是一片白边
    out_e2 = w7 / "输出_自动方向"
    opts_e2 = C.Options(src_dir=str(src_e), scan_pdf=str(scan_e), out_dir=str(out_e2),
                        overwrite=True)
    plan_e2 = C.plan_batch(opts_e2)
    res_e2, _ = C.run_batch(plan_e2, opts_e2, out_e2)
    check("自动左转后：比例吻合，从「原样」变成「贴合」",
          res_e2[0].fit_mode == C.FIT_FIT and page_sizes(res_e2[0].out)[0] == (842.0, 595.0),
          f"{res_e2[0].fit_mode}/{page_sizes(res_e2[0].out)[0]}")

    # ── [6] 输出命名与覆盖 ───────────────────────────────────────────────
    print("\n[6] 输出命名 / 覆盖")
    w8 = root / "命名"
    src_f, scan_f = make_corpus(w8, 2, 2, 2)
    out_f = w8 / "输出"
    of = C.Options(src_dir=str(src_f), scan_pdf=str(scan_f), out_dir=str(out_f),
                   suffix="_已签字首页", overwrite=True)
    pf = C.plan_batch(of)
    rf, _ = C.run_batch(pf, of, out_f)
    check("后缀生效", (out_f / "001_已签字首页.pdf").is_file(), str(sorted(p.name for p in out_f.glob("*"))))
    rf2, _ = C.run_batch(C.plan_batch(of), of, out_f)
    check("覆盖模式：不产生 xxx_2", len(list(out_f.glob("*.pdf"))) == 2,
          str(sorted(p.name for p in out_f.glob("*.pdf"))))

    out_f2 = w8 / "输出2"
    on = C.Options(src_dir=str(src_f), scan_pdf=str(scan_f), out_dir=str(out_f2))
    C.run_batch(C.plan_batch(on), on, out_f2)
    C.run_batch(C.plan_batch(on), on, out_f2)
    check("不覆盖模式：重名自动加编号",
          (out_f2 / "001.pdf").is_file() and (out_f2 / "001_2.pdf").is_file(),
          str(sorted(p.name for p in out_f2.glob("*.pdf"))))

    # ── [7] 后缀规范化 ───────────────────────────────────────────────────
    print("\n[7] 后缀规范化")
    check("去掉路径分隔符", C.clean_output_suffix("_a/b.pdf") == "_ab.pdf",
          C.clean_output_suffix("_a/b.pdf"))
    check("去掉首尾空白与点", C.clean_output_suffix("  _x.  ") == "_x",
          C.clean_output_suffix("  _x.  "))
    check("限长 40", len(C.clean_output_suffix("_" + "x" * 60)) == 40)
    check("空后缀", C.clean_output_suffix("") == "")
    check("不做自动补下划线的猜测", C.clean_output_suffix("已替换") == "已替换")

    # ── [8] 递归输出结构 ─────────────────────────────────────────────────
    print("\n[8] 递归：输出复刻子目录")
    w9 = root / "递归输出"
    src_g = w9 / "原件"
    (src_g / "批次B").mkdir(parents=True)
    for i in range(1, 3):
        build_pdf(src_g / f"{i:03d}.pdf", [(595, 842, f"A{i}-P1"), (595, 842, f"A{i}-P2")])
    for i in range(3, 5):
        build_pdf(src_g / "批次B" / f"{i:03d}.pdf",
                  [(595, 842, f"B{i}-P1"), (595, 842, f"B{i}-P2")])
    scan_g = w9 / "扫描件.pdf"
    build_pdf(scan_g, [(595, 842, f"SCAN-{k}") for k in range(1, 5)])
    out_g = w9 / "输出"
    og = C.Options(src_dir=str(src_g), scan_pdf=str(scan_g), out_dir=str(out_g),
                   recursive=True, overwrite=True)
    pg = C.plan_batch(og)
    check("递归下数量对得上（4 vs 4）", not pg.blocked, pg.block_reason)
    rg, _ = C.run_batch(pg, og, out_g)
    check("顶层原件输出到输出目录根", (out_g / "001.pdf").is_file())
    check("子目录原件输出到同名子目录", (out_g / "批次B" / "003.pdf").is_file(),
          str(sorted(str(p) for p in out_g.rglob("*.pdf"))))

    # ── [9] 演练不写文件 ─────────────────────────────────────────────────
    print("\n[9] dry_run")
    w10 = root / "演练"
    src_h, scan_h = make_corpus(w10, 2, 2, 2)
    out_h = w10 / "输出"
    oh = C.Options(src_dir=str(src_h), scan_pdf=str(scan_h), out_dir=str(out_h), dry_run=True)
    ph = C.plan_batch(oh)
    rh, _ = C.run_batch(ph, oh, out_h)
    check("演练报告成功", all(r.ok for r in rh))
    check("演练不产生任何文件", not out_h.exists() or not list(out_h.glob("*.pdf")))

    # ── [10] 跳过 ────────────────────────────────────────────────────────
    print("\n[10] 手动忽略")
    ph.items[1].status = C.Status.SKIPPED.value
    out_i = w10 / "输出2"
    oi = C.Options(src_dir=str(src_h), scan_pdf=str(scan_h), out_dir=str(out_i))
    ri, _ = C.run_batch(ph, oi, out_i)
    check("被忽略的那行不计入输出",
          [r.out.name for r in ri if r.out.name] == ["001.pdf"],
          str([r.out.name for r in ri]))

    # ── [11] ⑥ 人工判方向（横版表格被竖着扫进来） ────────────────────────
    print("\n[11] 扫描页方向（左转 / 右转）")
    w11 = root / "方向"
    src11 = w11 / "原件"
    src11.mkdir(parents=True)
    # 原件首页是横向 842×595（横版表格）；扫描页被竖着扫进来，是竖向 595×842
    build_pdf(src11 / "001.pdf", [(842, 595, "ORI1-P1"), (842, 595, "ORI1-P2")])
    scan11 = w11 / "扫描件.pdf"
    build_pdf(scan11, [(595, 842, "SCAN-1")])
    out11 = w11 / "输出"

    check("角度语义：正数=右转、负数=左转", (C.ROT_RIGHT, C.ROT_LEFT) == (90, -90))
    check("转四次回到原样",
          C.turn_deg(C.turn_deg(C.turn_deg(C.turn_deg(0, 90), 90), 90), 90) == 0)
    check("左转一次 = 270°", C.turn_deg(0, C.ROT_LEFT) == 270)
    check("90/270 宽高互换，180 不变",
          C.rotated_size((595, 842), 90) == (842.0, 595.0)
          and C.rotated_size((595, 842), 180) == (595.0, 842.0))

    # ⚠️ 这一节专门验证「不转」的对照，所以关掉⑦自动方向（否则横向原件会自动左转）
    base = C.Options(src_dir=str(src11), scan_pdf=str(scan11), out_dir=str(out11),
                     overwrite=True, auto_rotate=False)
    p_straight = C.plan_batch(base)
    check("不转时：预览里扫描页尺寸是竖的 595×842",
          p_straight.items[0].scan_size == (595.0, 842.0), str(p_straight.items[0].scan_size))
    r_straight, _ = C.run_batch(p_straight, base, out11)
    check("不转也能出（只是套不进横版原件，原样保留）", r_straight[0].ok, r_straight[0].note)
    check("不转时输出首页仍是竖的 595×842",
          _disp(out11 / "001.pdf")[0] == (595.0, 842.0), str(_disp(out11 / "001.pdf")))
    check("不转时内容流里没有旋转矩阵",
          not _has_rot_matrix(out11 / "001.pdf"))

    rots = C.Options(src_dir=str(src11), scan_pdf=str(scan11), out_dir=str(out11),
                     rotations={1: 90}, overwrite=True)
    p_rot = C.plan_batch(rots)
    check("右转 90° 后预览里的尺寸就宽高互换了",
          p_rot.items[0].scan_size == (842.0, 595.0), str(p_rot.items[0].scan_size))
    check("方向标签是「右转 90°」",
          C.ROT_LABELS[p_rot.items[0].rotation] == "右转 90°", str(p_rot.items[0].rotation))
    r_rot, _ = C.run_batch(p_rot, rots, out11)
    check("转完替换成功", r_rot[0].ok, r_rot[0].note)
    check("输出首页变成横向 842×595（正好贴合原件的横版首页）",
          _disp(out11 / "001.pdf")[0] == (842.0, 595.0), str(_disp(out11 / "001.pdf")))
    check("内容是真转了，不只是框变了（内容流里有旋转矩阵）",
          _has_rot_matrix(out11 / "001.pdf"))
    check("原件本身没被转（原件只读）",
          _disp(src11 / "001.pdf") == [(842.0, 595.0), (842.0, 595.0)])
    check("第 2 页起仍是原件内容",
          page_texts(out11 / "001.pdf")[1:2] == ["ORI1-P2"], str(page_texts(out11 / "001.pdf")))

    p_rot2 = C.plan_batch(C.Options(src_dir=str(src11), scan_pdf=str(scan11),
                                    out_dir=str(out11), rotations={1: 270}))
    check("左转 270° 同样是横向（宽高互换）",
          p_rot2.items[0].scan_size == (842.0, 595.0), str(p_rot2.items[0].scan_size))
    check("左转的标签对得上", C.ROT_LABELS[270] == "左转 90°")

    check("命令行能解析旋转设置", C.parse_rotations("3:90,5:270") == {3: 90, 5: 270})
    check("角度不是 90 的倍数会报错", _raises(lambda: C.parse_rotations("3:45")))
    check("页码格式写错会报错", _raises(lambda: C.parse_rotations("3")))

    # ── [12] ⑦ 自动方向（用户 2026-10-08 拍板的默认规则）────────────────
    print("\n[12] 自动方向：原件横向 → 它那一页默认左转 90°")
    w12 = root / "自动方向"
    src12 = w12 / "原件"
    src12.mkdir(parents=True)
    # 第 2 份是横向原件（842×595），其余竖向；扫描件一律竖扫 595×842
    for i, sz in ((1, (595, 842)), (2, (842, 595)), (3, (595, 842))):
        build_pdf(src12 / f"{i:03d}.pdf",
                  [(sz[0], sz[1], f"O{i}-P1"), (sz[0], sz[1], f"O{i}-P2")])
    scan12 = w12 / "扫描件.pdf"
    build_pdf(scan12, [(595, 842, f"S-{k}") for k in range(1, 4)])
    o12 = C.Options(src_dir=str(src12), scan_pdf=str(scan12), overwrite=True)

    check("原件首页横竖判得出来",
          [C.is_landscape(it.src_size) for it in
           C.plan_batch(o12).items] == [False, True, False])
    p12 = C.plan_batch(o12)
    check("横向原件那一页默认左转 90°（270），其余正向",
          [it.rotation for it in p12.items] == [0, 270, 0],
          str([it.rotation for it in p12.items]))
    check("预览里那一页的尺寸已经宽高互换",
          p12.items[1].scan_size == (842.0, 595.0), str(p12.items[1].scan_size))

    # 自动值跟着【行→页】映射走：页码一动，「这一页是谁的封面」变了，默认值跟着变。
    # 期望按**行**看：002 是横向原件，不管它改用哪一页，它那一行都得是左转 90°。
    C.swap_pages(p12, 2, -1)          # 第 2 行（横向原件）改用第 1 页
    C.apply_rotations(p12, {})
    check("页码对调后，横向原件那一行仍是左转 90°（跟原件走，不跟页码走）",
          [it.rotation for it in p12.items] == [0, 270, 0],
          str([it.rotation for it in p12.items]))
    check("对调后第 1 页归横向原件用 → 第 1 页被标成左转",
          p12.items[1].scan_index == 1 and p12.items[1].rotation == 270)

    # 人工 > 自动：给第 1 页指定右转 90°，压过「横向原件默认左转」
    C.apply_rotations(p12, {1: 90})
    check("人工判的方向压过自动值", p12.items[1].rotation == 90,
          str(p12.items[1].rotation))
    check("人工只管自己那一页（第 2 页仍走自动）", p12.items[0].rotation == 0,
          str(p12.items[0].rotation))
    check("反复 apply 不会把上一次的结果当输入（从原始尺寸重算）",
          C.plan_batch(o12).items[1].scan_size == (842.0, 595.0))

    p12b = C.plan_batch(C.Options(src_dir=str(src12), scan_pdf=str(scan12),
                                  auto_rotate=False))
    check("关掉自动方向后全部正向", [it.rotation for it in p12b.items] == [0, 0, 0])

    # 守卫：这一页「本来就是横向」→ 已经是横着扫出来的，不能再转一次
    scan12L = w12 / "扫描件_横.pdf"
    build_pdf(scan12L, [(842, 595, "S1"), (842, 595, "S2"), (842, 595, "S3")])
    p12L = C.plan_batch(C.Options(src_dir=str(src12), scan_pdf=str(scan12L)))
    check("扫描页本来就是横向 → 不再自动左转（否则会转反）",
          [it.rotation for it in p12L.items] == [0, 0, 0],
          str([it.rotation for it in p12L.items]))
    check("并在「说明」列出声：这批纸不是竖扫的",
          C.NOTE_SCAN_LANDSCAPE in p12L.items[1].note, p12L.items[1].note)

    # 界面③「整批转」的底层口径：在当前方向基础上再转 90°
    p12c = C.plan_batch(o12)
    C.apply_rotations(p12c, {it.scan_index: C.turn_deg(it.rotation, C.ROT_RIGHT)
                             for it in p12c.items})
    check("整批右转：横向那页 270→0，其余 0→90",
          [it.rotation for it in p12c.items] == [90, 0, 90],
          str([it.rotation for it in p12c.items]))

    # 真的跑一遍：自动左转要落到输出文件里
    out12 = w12 / "输出"
    o12d = C.Options(src_dir=str(src12), scan_pdf=str(scan12), out_dir=str(out12),
                     overwrite=True)
    r12, _ = C.run_batch(C.plan_batch(o12d), o12d, out12)
    check("自动方向下全部替换成功", all(r.ok for r in r12), str([r.note for r in r12]))
    check("横向原件那份：输出首页是横向 842×595（转过来了）",
          _disp(out12 / "002.pdf")[0] == (842.0, 595.0), str(_disp(out12 / "002.pdf")))
    check("横向原件那份：内容是真的转了（内容流里有旋转矩阵）",
          _has_rot_matrix(out12 / "002.pdf"))
    check("竖向原件那份：输出首页仍是竖向 595×842（没被连坐）",
          _disp(out12 / "001.pdf")[0] == (595.0, 842.0), str(_disp(out12 / "001.pdf")))
    check("竖向原件那份：内容流里没有旋转矩阵",
          not _has_rot_matrix(out12 / "001.pdf"))

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  失败：" + n)
    shutil.rmtree(root, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
