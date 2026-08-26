#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用 git 提交与推送脚本（跨平台）

用法:
    python scripts/mygit.py ["提交信息"]
    bun run mygit ["提交信息"]           # 通过 package.json 调用

说明:
    - 优先切换进项目自带虚机环境 .venv (取得 venv 内 python.exe) 再执行,
      从而使用项目独立的 Python 运行环境。
    - 默认提交信息包含当前日期时间;自动执行 git add -A -> commit -> push.
"""

import os
import subprocess
import sys
from datetime import datetime


def project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def venv_python(root):
    """返回 .venv 中的解释器路径;不存在则返回 None。"""
    if sys.platform == "win32":
        candidate = os.path.join(root, ".venv", "Scripts", "python.exe")
    else:
        candidate = os.path.join(root, ".venv", "bin", "python")
    return candidate if os.path.isfile(candidate) else None


def ensure_in_venv():
    """若存在 .venv 且当前解析器不在其中,则用其自动重新加载本脚本。"""
    root = project_root()
    venv = venv_python(root)
    if venv:
        try:
            sys.executable  # noqa
            if os.path.abspath(sys.executable) != os.path.abspath(venv):
                args = [venv, os.path.abspath(__file__)] + sys.argv[1:]
                os.execv(venv, args)
        except (AttributeError, OSError):
            pass


def run(cmd):
    print(f"> {cmd}")
    subprocess.run(cmd, shell=True, check=True)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    ensure_in_venv()  # 自动切换到项目独立 .venv 环境

    # 优先切换到项目根目录运行，保证 git 操作针对本项目
    root = project_root()
    if os.path.isdir(root):
        os.chdir(root)

    argued = " ".join(sys.argv[1:]).strip()
    message = argued or f"docs: 自动提交 {now()}"

    try:
        run("git add -A")
        run(f'git commit -m "{message}"')
        run("git push")
        print("提交并推送成功.")
        return 0
    except subprocess.CalledProcessError as e:
        print(f"执行失败: {e}", file=sys.stderr)
        return e.returncode


if __name__ == "__main__":
    sys.exit(main())
