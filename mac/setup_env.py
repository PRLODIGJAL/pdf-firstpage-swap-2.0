#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 首页替换工具 v2.0 —— 环境自检与启动器（Windows / macOS 共用这一份）。

双击 .bat / .command 会调到这里，由它统一负责三件事：
  1. 检查工具自带的虚拟环境还能不能用；
  2. 不能用就现场重建（建 venv → 装依赖），全程打印中文进度；
  3. 就绪后用虚拟环境里的解释器拉起 seq_app.py。

为什么必须有这个文件
--------------------
虚拟环境**不能跨机复制**。它里面记着创建时那台机器的 Python 绝对路径，
二进制也不通用（macOS 是 .so、Windows 是 .pyd）。把这个文件夹从一台电脑
拷到另一台，双击只会得到一句看不懂的报错。与其让使用者去查，不如让它自己修。

用法
----
    python setup_env.py            自检 → 按需安装 → 启动（正常双击走这条）
    python setup_env.py --check    只自检并打印结论，不安装、不启动
    python setup_env.py --setup    只把环境装好，不启动界面
    python setup_env.py --rebuild  不管现状，强制重建环境（可配 --setup 一起用）

【软件更新后要不要重装】不用。装好的东西在本文件夹的 .venv/ 里，
更新只是换掉几个 .py —— 别连 .venv 一起删就行。
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(HERE, ".venv")
APP = os.path.join(HERE, "seq_app.py")

# 依赖清单：pypdf 负责读写 PDF 页面。tkinter 是标准库自带的界面。
# v2 的扫描件是一份多页 PDF，不需要把图片转成 PDF，所以**不装 pillow**。
# 版本下限：pypdf 3.16 才有 Transformation / transfer_rotation_to_content
# 这两个我们依赖的接口。
NEED = ("pypdf>=3.16",)
CHECK_IMPORTS = "import pypdf, tkinter"

# 国内直连 PyPI 常常很慢，先走清华镜像；失败再退回官方源。
MIRRORS = (
    ("清华镜像", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("官方源", None),
)


def say(msg=""):
    print(msg, flush=True)


def rule(title):
    say("")
    say("=" * 58)
    say(title)
    say("=" * 58)


def venv_python(venv_dir):
    """虚拟环境里的解释器路径（Windows 在 Scripts 下，其余在 bin 下）。"""
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def run(args, **kw):
    """跑一个子进程并把输出接回来。返回 (成功?, 输出文本)。"""
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", **kw)
    except OSError as e:
        return False, str(e)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out


def probe(py):
    """这个解释器能不能跑起来、装的依赖全不全、它是不是本环境的解释器。

    返回 (可用?, 说明)。只认「能 import 全部依赖」且「sys.prefix 就是它自己
    所在的虚拟环境目录」—— 后者能挡住「从别的电脑拷过来的 venv」：
    那种环境里即使文件还在，也会去指原作者机器上的路径。

    一律用 os.path.lexists 判存在：跨机拷来的环境里，解释器常常是个**断链**
    （指向原作者机器上的路径），此时 os.path.exists 返回 False，
    会被误判成「没装过」，于是跳过清理直接去建 —— 然后在旧目录上建失败。
    """
    if not py or not os.path.lexists(py):
        return False, "解释器不存在"
    code = (CHECK_IMPORTS + ";import sys;print('PREFIX='+sys.prefix)")
    ok, out = run([py, "-c", code])
    if not ok:
        first = ""
        for ln in out.strip().splitlines():
            if ln.strip() and "PREFIX=" not in ln:
                first = ln.strip()
        return False, ("依赖缺失或无法运行" + ("（" + first[:110] + "）" if first else ""))
    prefix = ""
    for ln in out.splitlines():
        if ln.startswith("PREFIX="):
            prefix = ln[7:].strip()
    if os.path.normcase(os.path.realpath(prefix)) != os.path.normcase(os.path.realpath(VENV)):
        return False, "环境来自另一台电脑（内部路径指向 %s）" % (prefix or "?")
    return True, "就绪"


def drop_venv():
    """删掉现有的虚拟环境目录，返回「现在这个位置是不是干净的」。

    只删那种确实像 venv 的（有 pyvenv.cfg，或有 bin/Scripts 目录，
    或解释器路径存在——含断链）；万一是用户自己放的、恰好叫 .venv 的普通
    文件夹，就不动它，并如实说明，免得误删。
    """
    if not os.path.lexists(VENV):
        return True
    marks = (os.path.join(VENV, "pyvenv.cfg"),
             os.path.join(VENV, "bin"),
             os.path.join(VENV, "Scripts"),
             venv_python(VENV))
    if not any(os.path.lexists(m) for m in marks):
        say("  跳过：%s 不像虚拟环境，未删除" % VENV)
        say("        请手动删掉这个文件夹后重试。")
        return False
    say("  删除旧环境 %s" % VENV)
    try:
        shutil.rmtree(VENV)
    except OSError as e:
        say("  [失败] 删除失败：%s" % e)
        say("         请手动删掉这个文件夹后重试：%s" % VENV)
        return False
    return True


def make_venv(base):
    say("  用 %s 创建虚拟环境…" % base)
    ok, out = run([base, "-m", "venv", VENV])
    if not ok:
        say("  [失败] 创建虚拟环境出错：")
        say("  " + out.strip()[:600])
        return False
    return True


def install_deps(py):
    """装依赖。逐个镜像源试，任何一个成功即返回。"""
    for name, url in MIRRORS:
        args = [py, "-m", "pip", "install", "--disable-pip-version-check",
                "--no-input"] + list(NEED)
        if url:
            args += ["-i", url]
        say("")
        say("  正在从「%s」安装依赖（首次约 1～3 分钟，请勿关窗口）…" % name)
        ok, out = run(args)
        if ok:
            say("  安装完成（%s）" % name)
            return True
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-6:]
        say("  [失败] 用「%s」没装上，末尾几行输出：" % name)
        for ln in tail:
            say("    " + ln[:150])
        say("  换个源再试…")
    return False


def ensure_ready(force=False):
    """确保有一个可用环境。返回可用解释器路径，失败返回 None。"""
    py = venv_python(VENV)

    if not force:
        ok, why = probe(py)
        if ok:
            say("环境检查：可用 ✓（%s）" % os.path.relpath(py, HERE))
            return py
        if os.path.lexists(py) or os.path.lexists(VENV):
            say("环境检查：不可用 —— %s" % why)
            say("            需要重建一次，这一步不碰你的 PDF 文件。")
        else:
            say("环境检查：还没装过，需要先准备一次")

    rule("准备运行环境")
    say("这一步只做一次。装好以后，以后双击就直接开，不用再等。")
    say("")

    base = sys.executable
    say("  本机 Python：%s" % base)
    say("  版本：%d.%d.%d" % sys.version_info[:3])
    if sys.version_info < (3, 9):
        say("  [失败] 版本太低，需要 Python 3.9 或更高。")
        return None
    say("")

    # 走到这里说明现有环境不可用（或用户要求强制重建）：**一律先清干净**。
    # 不能只看「解释器在不在」—— 跨机拷来的环境里它是断链，exists() 会返回
    # False，于是跳过清理，最后在旧目录上建 venv 直接失败。
    if not drop_venv():
        return None

    if not make_venv(base):
        return None

    py = venv_python(VENV)
    if not os.path.exists(py):
        say("  [失败] 环境建好了但找不到解释器：%s" % py)
        return None

    # pip 太旧会装不上新包；升级失败不致命，继续往下走。
    say("  升级 pip…")
    run([py, "-m", "pip", "install", "--upgrade", "pip",
         "--disable-pip-version-check", "--no-input", "-q"])

    if not install_deps(py):
        say("")
        say("  [失败] 依赖没装上。常见原因：")
        say("    · 网络不通或被公司网络策略挡住 —— 换个网络再试")
        say("    · 装的是 Microsoft Store 版 Python —— 建议到 python.org 重装")
        return None

    say("")
    say("  复核装好没有…")
    ok, why = probe(py)
    if not ok:
        say("  [失败] 装完仍然不可用：%s" % why)
        return None
    say("  复核通过 ✓")
    return py


def launch(py):
    """用虚拟环境的解释器拉起界面。"""
    if not os.path.exists(APP):
        say("[失败] 找不到 seq_app.py：%s" % APP)
        say("       本文件必须和 seq_app.py 放在同一个文件夹里。")
        return 1
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"       # 免得 Windows 控制台用 cp936 打印报错
    env["PYTHONUTF8"] = "1"
    say("")
    say("启动界面…（关掉界面窗口即可退出）")
    say("")
    try:
        return subprocess.run([py, APP], cwd=HERE, env=env).returncode
    except KeyboardInterrupt:
        return 0


def main():
    args = sys.argv[1:]
    say("PDF 首页替换工具 v2.0   ——   %s" % HERE)
    say("系统：%s    Python：%s" % (sys.platform, sys.executable))

    if "--check" in args:
        rule("环境自检（只看不动）")
        py = venv_python(VENV)
        say("虚拟环境目录：%s" % VENV)
        say("是否存在：%s" % ("是" if os.path.isdir(VENV) else "否"))
        ok, why = probe(py)
        say("可用性：%s" % ("可用 ✓" if ok else "不可用 —— " + why))
        if ok:
            ok2, out = run([py, "-c", "import pypdf;print('pypdf %s' % pypdf.__version__)"])
            if ok2:
                say("依赖版本：%s" % out.strip())
        return 0 if ok else 1

    py = ensure_ready(force="--rebuild" in args)
    if not py:
        rule("没能准备好环境")
        say()
        say("请把上面这几行报错截图发给我，我按情况处理。")
        say()
        return 1

    if "--setup" in args:
        rule("环境已就绪")
        say()
        say("以后双击启动脚本就能直接用，这一步不用再做。")
        say()
        return 0

    return launch(py)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
