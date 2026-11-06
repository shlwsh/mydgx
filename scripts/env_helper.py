#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/env_helper.py - 项目脚本运行环境与通用配置助手

功能：
1. 自动检测并切换到项目根目录下的独立虚拟环境 (.venv)；
2. 统一加载根目录下的 .env 敏感配置文件；
3. 提供符合项目规范的标准日志记录器初始化函数；
4. 暴露项目根目录与常用路径常量，方便各类脚本复用。
"""

import os
import sys
import subprocess
import logging

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_venv():
    """
    检查当前 Python 解释器是否位于项目根目录的 .venv 中。
    如果发现存在 .venv 且当前解释器不是该虚拟环境，则透明地以虚拟环境解释器重新启动当前脚本。
    """
    if sys.platform == "win32":
        venv_python = os.path.join(ROOT_DIR, ".venv", "Scripts", "python.exe")
    else:
        venv_python = os.path.join(ROOT_DIR, ".venv", "bin", "python")

    if os.path.isfile(venv_python):
        curr_py = os.path.realpath(sys.executable)
        target_py = os.path.realpath(venv_python)
        if curr_py != target_py and not os.getenv("DGX_VENV_BOOTSTRAPPED"):
            env = os.environ.copy()
            env["DGX_VENV_BOOTSTRAPPED"] = "1"
            rc = subprocess.call([venv_python] + sys.argv, cwd=ROOT_DIR, env=env)
            sys.exit(rc)


# 在模块顶层即执行环境引导，确保后续依赖导入前已在虚拟环境中
ensure_venv()

try:
    from dotenv import load_dotenv
except ImportError:
    # 极端情况下若虚拟环境缺失依赖，给出清晰指引
    print(
        f"[警告] 当前环境 ({sys.executable}) 缺少 python-dotenv 依赖。\n"
        f"请先执行安装：{sys.executable} -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple",
        file=sys.stderr,
    )
    load_dotenv = None


def init_env():
    """统一加载根目录 .env 文件"""
    env_file = os.path.join(ROOT_DIR, ".env")
    if os.path.isfile(env_file) and load_dotenv:
        load_dotenv(env_file)
    return env_file


def setup_logger(name="DGX-Ops", level=logging.INFO):
    """获取符合项目规范的标准格式化日志记录器"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(name)
