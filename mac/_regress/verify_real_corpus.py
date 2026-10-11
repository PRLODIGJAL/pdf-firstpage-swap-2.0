#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实语料验证 —— 拿桌面上真实的 WI 文件跑一遍，不写回原处。

做法：真实原件排队 → 用它们各自的首页拼成一份「扫描件」→ 批量替换 →
      逐页比对内容/页数/尺寸，并确认原件一个字节都没变。

跑法：.venv/bin/python -B _regress/verify_real_corpus.py
产物全部在 /tmp，桌面上的文件只读。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import seq_core as C  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK ' if cond else 'NG '} {name}" + (f"  <{detail}>" if detail and not cond else ""))


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def texts(pdf: Path) -> list[str]:
    from pypdf import PdfReader

    return [(pg.extract_text() or "").strip() for pg in PdfReader(str(pdf)).pages]


def sizes(pdf: Path) -> list[tuple[float, float]]:
    """纸面尺寸（mediabox 原样，不看 /Rotate）。"""
    from pypdf import PdfReader

    out = []
    for pg in PdfReader(str(pdf)).pages:
        b = pg.mediabox
        out.append((round(float(b.width), 1), round(float(b.height), 1)))
    return out


def disp_sizes(pdf: Path) -> list[tuple[float, float]]:
    """显示尺寸（带 /Rotate 90/270 的页按横放后的尺寸算）—— 贴合就是对齐这个。

    ⚠️ 真实 WI 的封面常常是 /Rotate 90 的横向页：纸面 595×842，显示却是 842×595。
       工具按显示尺寸贴合，所以测试也必须按显示尺寸比，否则会误报。
    """
    from pypdf import PdfReader

    return [tuple(round(v, 1) for v in C.display_size(pg))
            for pg in PdfReader(str(pdf)).pages]


CORPUS = [Path("/Users/langzi/Desktop/成功1"), Path("/Users/langzi/Desktop/成功2")]


def main() -> int:
    reals = sorted({p for d in CORPUS if d.is_dir()
                    for p in d.glob("*.pdf") if p.is_file()})
    if not reals:
        print("  跳过：桌面上的真实语料（成功1 / 成功2）不在了")
        return 0
    print(f"真实原件 {len(reals)} 份")

    tmp = Path(tempfile.gettempdir()) / "pdfseq_real"
    if tmp.exists():
        shutil.rmtree(tmp)
    src = tmp / "原件"
    src.mkdir(parents=True)
    before: dict[str, str] = {}
    for p in reals:
        dst = src / p.name
        shutil.copy2(p, dst)
        before[dst.name] = md5(dst)

    # ── 用真实首页拼一份「扫描件」（页数 = 原件份数）─────────────────────
    from pypdf import PdfReader, PdfWriter

    ordered = C.collect_originals(src, C.Options(src_dir=str(src)))
    writer = PdfWriter()
    for p in ordered:
        r = PdfReader(str(p))
        if r.is_encrypted:
            try:
                r.decrypt("")
            except Exception:  # noqa: BLE001
                pass
        writer.add_page(r.pages[0])
    scan = tmp / "扫描件_签字版.pdf"
    with open(scan, "wb") as fh:
        writer.write(fh)
    print(f"扫描件 {len(ordered)} 页 → {scan.name}\n")

    out = tmp / "输出"
    opts = C.Options(src_dir=str(src), scan_pdf=str(scan), out_dir=str(out))
    plan = C.plan_batch(opts)
    check("真实原件全部读得出，没被阻断", not plan.blocked, plan.block_reason)
    if plan.blocked:
        return 1
    check("配对份数 = 原件份数", len(plan.items) == len(ordered), str(len(plan.items)))

    scan_texts = texts(scan)
    ori_texts = {p.name: texts(p) for p in ordered}
    ori_disp = {p.name: disp_sizes(p) for p in ordered}

    results, _ = C.run_batch(plan, opts, out)
    bad = [r for r in results if not r.ok]
    check("全部替换成功", not bad, str([(r.src.name, r.note) for r in bad]))
    if bad:
        return 1

    for r in results:
        t = texts(r.out)
        o = ori_texts[r.src.name]
        i = r.scan_index
        check(f"{r.src.name[:28]}：第 1 页 = 扫描件第 {i} 页",
              t and t[0] == scan_texts[i - 1], str(t[:1])[:60])
        check(f"{r.src.name[:28]}：第 2 页起与原件逐页一致",
              t[1:] == o[1:], f"{len(t)} vs {len(o)}")
        check(f"{r.src.name[:28]}：页数不变", len(t) == len(o), f"{len(t)} vs {len(o)}")
        s = sizes(r.out)
        if r.fit_mode == C.FIT_FIT:
            want = ori_disp[r.src.name][0]
            check(f"{r.src.name[:28]}：首页尺寸贴合原件（按显示尺寸，含 /Rotate）",
                  s[0] == want, f"{s[0]} vs {want}")
        else:
            check(f"{r.src.name[:28]}：比例不一致时原样保留（有说明）", bool(r.note))

    check("原件一个字节都没动",
          {p.name: md5(p) for p in src.glob("*.pdf")} == before)
    check("输出份数 = 原件份数",
          len(list(out.glob("*.pdf"))) == len(ordered), str(len(list(out.glob("*.pdf")))))

    # 数量对不上必须整批拒绝（真实文件上再验一次）
    w2 = PdfWriter()
    r0 = PdfReader(str(scan))
    w2.add_page(r0.pages[0])
    short = tmp / "少一页.pdf"
    with open(short, "wb") as fh:
        w2.write(fh)
    p2 = C.plan_batch(C.Options(src_dir=str(src), scan_pdf=str(short)))
    check("真实文件下：少一页就整批拒绝", p2.blocked and "差" in p2.block_reason, p2.block_reason)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for n in FAIL:
        print("  失败：" + n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
