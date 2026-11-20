#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/desktop_common.py - 桌面项/图标修复公共模块

被以下工具复用：
    scripts/fix_desktop_launcher.py   通用桌面项修复（StartupWMClass、图标重建）
    install/install_antigravity.py    Antigravity 系列应用安装脚本

职责：
    * sudo 口令读取（.env 的 LOCAL_SUDO_PASS / NODE123_RDP_PASS / SSH_NODE123_PASS）
    * 统一的子进程封装（可透传 sudo 口令）
    * 多尺寸图标生成 + hicolor 图标缓存刷新
    * .desktop 文件定位与字段读取
"""

import getpass
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_THEME_DIR = Path("/usr/share/icons/hicolor")
# 仅使用 hicolor/index.theme 中登记过的尺寸：本机未登记 1024x1024，只放 1024 等于没有图标
ICON_SIZES = (512, 256, 128, 64, 48, 32)
USER_APPLICATIONS_DIR = Path.home() / ".local/share/applications"
SYSTEM_APPLICATIONS_DIR = Path("/usr/share/applications")


def sudo_password(cli_value: str | None = None) -> str:
    """按 参数 → .env(LOCAL_SUDO_PASS / NODE123_RDP_PASS / SSH_NODE123_PASS) → 交互输入 的顺序取口令。"""
    if cli_value:
        return cli_value
    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file)
        except ImportError:  # 允许系统解释器下无 python-dotenv
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value.strip().strip('"'))
    for key in ("LOCAL_SUDO_PASS", "NODE123_RDP_PASS", "SSH_NODE123_PASS"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return getpass.getpass("请输入本机 sudo 口令：")


def run(command: list[str] | str, sudo_pass: str | None = None, check: bool = True) -> str:
    stdin_data = None
    if sudo_pass:
        if isinstance(command, list):
            command = ["sudo", "-S", "-p", "", *command]
        else:
            command = f"sudo -S -p '' {command}"
        stdin_data = sudo_pass + "\n"
    result = subprocess.run(
        command, shell=isinstance(command, str), input=stdin_data, text=True, capture_output=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败（{result.returncode}）：{command}\n{result.stdout}\n{result.stderr}")
    return (result.stdout or "").strip()


def generate_icons(icon_name: str, master: Path, sudo_pass: str, sizes: tuple[int, ...] = ICON_SIZES) -> None:
    """把母版 PNG 缩放成 hicolor 已登记的多种尺寸，并刷新图标缓存。"""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        raise SystemExit("缺少 Pillow，请执行：.venv/bin/pip install -r requirements.txt")

    with Image.open(master) as source, tempfile.TemporaryDirectory() as tmp:
        image = source.convert("RGBA")
        for size in sizes:
            scaled = image.copy()
            scaled.thumbnail((size, size), Image.LANCZOS)
            canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            canvas.paste(scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2))
            tmp_file = Path(tmp) / f"{size}.png"
            canvas.save(tmp_file)
            run(
                ["install", "-Dm644", str(tmp_file), str(ICON_THEME_DIR / f"{size}x{size}/apps/{icon_name}.png")],
                sudo_pass=sudo_pass,
            )
    refresh_icon_cache(sudo_pass)
    logging.getLogger("desktop_common").info(
        "图标已安装：%s（尺寸 %s）", icon_name, "/".join(str(s) for s in sizes)
    )


def refresh_icon_cache(sudo_pass: str) -> None:
    run(["gtk-update-icon-cache", "-f", "-t", str(ICON_THEME_DIR)], sudo_pass=sudo_pass, check=False)


def find_desktop_file(desktop_id: str) -> Path | None:
    """按 ID（含或不含 .desktop 后缀）在用户级/系统级目录中查找。"""
    name = desktop_id if desktop_id.endswith(".desktop") else f"{desktop_id}.desktop"
    for directory in (USER_APPLICATIONS_DIR, SYSTEM_APPLICATIONS_DIR):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def read_key(path: Path, key: str) -> str | None:
    if not path or not path.is_file():
        return None
    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return None


def which(program: str) -> str | None:
    return shutil.which(program)
