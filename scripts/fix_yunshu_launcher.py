#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/fix_yunshu_launcher.py - 云枢（yunshu 零信任客户端）Dock 图标关联修复

背景：
    云枢窗口的 WM_CLASS 为 ("yunshucross", "YunshuCross")，而它安装的
    /usr/share/applications/yunshu.desktop 文件 ID 是 yunshu，且未声明
    StartupWMClass。GNOME 无法把运行中的窗口关联到桌面项，于是 Dock /
    任务栏回退成通用齿轮图标，名称也拿不到。

    另有两处规范问题：Name= 为空（只提供了 Name[zh_CN]），Version= 填的
    是应用版本号（该键应为 desktop 规范版本）。

功能：
    1. 在 ~/.local/share/applications/yunshu.desktop 写入覆盖项（不动系统文件，
       厂商升级后仍然生效），补上 StartupWMClass 与合法的 Name/Version；
    2. 自动从正在运行的云枢窗口探测 WM_CLASS，避免硬编码；
    3. 校验语法、刷新 desktop 数据库，并在 --check 下输出当前关联状态。

用法示例：
    scripts/fix_yunshu_launcher.py            # 应用修复（幂等）
    scripts/fix_yunshu_launcher.py --check    # 只检查，不修改
"""

import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("fix_yunshu_launcher")

SYSTEM_DESKTOP = Path("/usr/share/applications/yunshu.desktop")
USER_DESKTOP = Path.home() / ".local/share/applications/yunshu.desktop"
DEFAULT_WM_CLASS = "YunshuCross"
DEFAULT_ICON = "/usr/share/icons/hicolor/scalable/apps/yunshu.png"
DEFAULT_EXEC = "/opt/apps/yunshu/files/yunshu-cross --disable-http2"

TEMPLATE = """# 云枢（yunshu Linux ZeroTrust Client）本地覆盖项，由 scripts/fix_yunshu_launcher.py 生成
# 原因：窗口 WM_CLASS 与 desktop 文件 ID 不一致，系统文件未声明 StartupWMClass，
#       GNOME 无法关联运行窗口，Dock 显示通用齿轮图标。
[Desktop Entry]
Type=Application
Name=云枢
Name[zh_CN]=云枢
GenericName=云枢
GenericName[zh_CN]=云枢
Comment=yunshu Linux ZeroTrust Client
Icon={icon}
Exec={exec_line}
StartupWMClass={wmclass}
Categories=Network;
Keywords=yunshu;
X-GNOME-Autostart-Delay=1
Terminal=false
X-Ayatana-Desktop-Shortcuts=NewWindow
"""


def run(command: list[str], check: bool = True) -> str:
    result = subprocess.run(command, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败（{result.returncode}）：{command}\n{result.stderr}")
    return (result.stdout or "").strip()


def detect_wm_class() -> str | None:
    """从正在运行的云枢窗口读取 WM_CLASS 的 class 部分（第二个值）。"""
    try:
        output = run(["xdotool", "search", "--class", "Yunshu"], check=False)
    except FileNotFoundError:
        output = ""
    window_ids = [wid for wid in output.split() if wid.strip()]
    for wid in window_ids:
        props = run(["xprop", "-id", wid, "WM_CLASS"], check=False)
        match = re.search(r'=\s*"([^"]*)",\s*"([^"]*)"', props)
        if match:
            return match.group(2)
    return None


def read_key(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def check() -> bool:
    wmclass = read_key(USER_DESKTOP, "StartupWMClass")
    live = detect_wm_class()
    icon = read_key(USER_DESKTOP, "Icon")
    ok = bool(wmclass) and USER_DESKTOP.is_file() and icon is not None and Path(icon).is_file()
    if live:
        ok = ok and live.lower() == (wmclass or "").lower()
    logger.info(
        "云枢检查：覆盖项 %s，StartupWMClass=%s，实时 WM_CLASS=%s，图标 %s → %s",
        "存在" if USER_DESKTOP.is_file() else "缺失",
        wmclass or "未设置",
        live or "未运行/未取到",
        "存在" if icon and Path(icon).is_file() else "缺失",
        "✅ 正常" if ok else "⚠️ 异常",
    )
    return ok


def apply() -> bool:
    wmclass = detect_wm_class() or DEFAULT_WM_CLASS
    icon = read_key(SYSTEM_DESKTOP, "Icon") or DEFAULT_ICON
    exec_line = read_key(SYSTEM_DESKTOP, "Exec") or DEFAULT_EXEC
    logger.info("探测到 WM_CLASS：%s（图标 %s）", wmclass, icon)

    USER_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    if USER_DESKTOP.is_file():
        USER_DESKTOP.with_suffix(".desktop.bak").write_text(
            USER_DESKTOP.read_text(encoding="utf-8"), encoding="utf-8"
        )
    USER_DESKTOP.write_text(
        TEMPLATE.format(icon=icon, exec_line=exec_line, wmclass=wmclass), encoding="utf-8"
    )
    USER_DESKTOP.chmod(0o644)

    validate = run(["desktop-file-validate", str(USER_DESKTOP)], check=False)
    if validate:
        logger.warning("desktop 校验告警：%s", validate)
    run(["update-desktop-database", str(USER_DESKTOP.parent)], check=False)
    logger.info("已写入覆盖项：%s", USER_DESKTOP)
    return check()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="修复云枢应用在 Dock 中无图标的问题（补 StartupWMClass）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--check", action="store_true", help="仅检查当前状态，不修改文件")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.check:
        return 0 if check() else 1
    return 0 if apply() else 1


if __name__ == "__main__":
    sys.exit(main())
