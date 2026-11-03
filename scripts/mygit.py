#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mygit 兼容入口（Python 版）

推荐用法（与 bun run mygit 完全一致）:
    bun run mygit ["提交信息"]

本脚本存在两个目的:
    1. 老环境里没有 Bun/Node 时，仍可提交并推送（内置兜底实现）。
    2. 有 Bun/Node 时直接转发给 scripts/mygit.mjs，避免两套逻辑行为不一致。

直接运行:
    python3 scripts/mygit.py ["提交信息"]
    python3 scripts/mygit.py --help
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MJS = os.path.join(ROOT, "scripts", "mygit.mjs")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run(cmd):
    """执行命令，参数为列表（不做 shell 拼接，避开引号/中文转义问题）。"""
    print(f"> {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=ROOT)


def delegate():
    """优先转发给 Node/Bun 版实现，返回退出码；无可用运行时返回 None。"""
    if not os.path.isfile(MJS):
        return None
    for name in ("bun", "node"):
        exe = shutil.which(name)
        if exe:
            try:
                return subprocess.call([exe, MJS] + sys.argv[1:], cwd=ROOT)
            except OSError:
                continue
    return None


def fallback():
    """无 Bun/Node 时的精简实现：add -A -> commit -> push。"""
    argued = " ".join(sys.argv[1:]).strip()
    message = argued or f"chore: 自动提交 {now()}"

    if run(["git", "add", "-A"]) != 0:
        print("git add 失败，已中止。", file=sys.stderr)
        return 1

    rc = run(["git", "commit", "-m", message])
    if rc != 0:
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=ROOT
        ).returncode
        if staged == 0:
            print("没有待提交的改动（工作区干净），跳过提交与推送。")
            return 0
        print("git commit 失败，已跳过推送。", file=sys.stderr)
        return rc

    # 没有 upstream 时自动建立
    has_upstream = (
        subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )
    if has_upstream:
        push_cmd = ["git", "push"]
    else:
        branch = (
            subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            ).stdout.strip()
            or "master"
        )
        push_cmd = ["git", "push", "-u", "origin", branch]

    if run(push_cmd) != 0:
        print("git push 失败。", file=sys.stderr)
        return 1

    print("提交并推送成功。")
    return 0


def main():
    # 仍保留 .venv 自动切换（存在时使用项目独立解释器）
    venv_py = os.path.join(ROOT, ".venv", "bin", "python")
    if os.path.isfile(venv_py) and os.path.realpath(sys.executable) != os.path.realpath(venv_py):
        os.execv(venv_py, [venv_py, os.path.abspath(__file__)] + sys.argv[1:])

    rc = delegate()
    if rc is None:
        rc = fallback()
    return rc


if __name__ == "__main__":
    sys.exit(main())
