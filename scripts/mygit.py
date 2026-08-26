#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用 git 提交与推送脚本（跨平台）

用法:
    python scripts/mygit.py ["提交信息"]
    bun run mygit ["提交信息"]       # 通过 package.json 调用

说明:
    默认提交信息包含当前日期时间;自动执行 git add -A -> commit -> push.
"""

import subprocess
import sys
from datetime import datetime


def run(cmd):
    print(f"> {cmd}")
    subprocess.run(cmd, shell=True, check=True)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
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
