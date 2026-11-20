#!/usr/bin/env python3
"""本机（cube-fe5e / NODE123）Cursor 卸载与重装工具（AppImage 备用安装方式）。

当前本机 Cursor 以 deb 包安装（`/usr/share/cursor`，3.30.14）；本脚本用于
deb 包不可用/需要并行试用新版本时，按《Cursor安装说明.md》的方式把 AppImage
解包到 ~/squashfs-root 独立运行。

流程：
    1. 停止正在运行的 Cursor；
    2. 备份（默认）并移除旧安装目录 ~/squashfs-root；
    3. 解包指定 AppImage 到 ~/squashfs-root；
    4. chrome-sandbox 设为 root:root + 4755（需要 sudo）；
    5. 校正桌面快捷方式并刷新菜单缓存。

示例：
    .venv/bin/python install/install_cursor_local.py --appimage ~/Downloads/install/Cursor-3.27.12-aarch64.AppImage
    .venv/bin/python install/install_cursor_local.py --check
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("install_cursor_local")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREFIX = Path.home() / "squashfs-root"
DESKTOP_FILE = Path.home() / ".local/share/applications/cursor.desktop"
LAUNCHER = Path.home() / ".local/bin/cursor"

DESKTOP_TEMPLATE = """[Desktop Entry]
Name=Cursor
GenericName=Text Editor
Comment=AI-powered code editor
Exec=/usr/bin/env GTK_IM_MODULE=ibus QT_IM_MODULE=ibus XMODIFIERS=@im=ibus IBUS_ENABLE_SYNC_MODE=1 {launcher} %F
Icon={icon}
Type=Application
Categories=Development;IDE;
MimeType=application/x-cursor-workspace;text/plain;inode/directory;x-scheme-handler/cursor;
Terminal=false
StartupWMClass=Cursor
Actions=new-empty-window;
Keywords=cursor;

[Desktop Action new-empty-window]
Name=New Empty Window
Exec=/usr/bin/env GTK_IM_MODULE=ibus QT_IM_MODULE=ibus XMODIFIERS=@im=ibus IBUS_ENABLE_SYNC_MODE=1 {launcher} --new-window %F
Icon={icon}
"""


def sudo_password(cli_value: str | None) -> str:
    """按 参数 → LOCAL_SUDO_PASS → NODE123_RDP_PASS → 交互输入 的顺序取口令。"""
    if cli_value:
        return cli_value
    load_dotenv(PROJECT_ROOT / ".env")
    for key in ("LOCAL_SUDO_PASS", "NODE123_RDP_PASS"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return getpass.getpass("请输入本机 sudo 口令：")


def run(command: list[str] | str, sudo_pass: str | None = None, check: bool = True) -> str:
    stdin_data = None
    if sudo_pass:
        command = ["sudo", "-S", "-p", "", *command] if isinstance(command, list) else f"sudo -S -p '' {command}"
        stdin_data = sudo_pass + "\n"
    result = subprocess.run(
        command,
        shell=isinstance(command, str),
        input=stdin_data,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败（{result.returncode}）：{command}\n{result.stdout}\n{result.stderr}")
    return (result.stdout or "").strip()


def stop_running(sudo_pass: str) -> None:
    # 普通用户进程直接结束；AppRun/cursor 子进程一并清理
    run("pkill -f 'squashfs-root/usr/share/cursor/cursor' || true", check=False)
    run("pkill -f 'squashfs-root/AppRun' || true", check=False)
    time.sleep(1)
    logger.info("已停止正在运行的 Cursor 进程（如有）")


def backup_old(prefix: Path) -> Path | None:
    if not prefix.exists():
        return None
    version = "unknown"
    for candidate in (
        prefix / "usr/share/cursor/resources/app/package.json",
        prefix / "resources/app/package.json",
    ):
        if candidate.is_file():
            try:
                version = json.loads(candidate.read_text(encoding="utf-8")).get("version", version)
            except Exception:  # noqa: BLE001
                pass
            break
    backup = prefix.with_name(f"{prefix.name}-{version}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.move(str(prefix), str(backup))
    logger.info("旧版本 %s 已卸载（备份保留于 %s）", version, backup)
    return backup


def extract_appimage(appimage: Path, prefix: Path) -> None:
    if not appimage.is_file():
        raise SystemExit(f"AppImage 不存在：{appimage}")
    appimage.chmod(appimage.stat().st_mode | 0o111)
    temp_dir = Path.home() / f".cache/cursor-local-install-{int(time.time())}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info("开始解包 %s", appimage)
    started = time.time()
    run(f"cd {temp_dir} && {appimage} --appimage-extract > extract.log 2>&1")
    produced = temp_dir / "squashfs-root"
    if not produced.is_dir():
        raise SystemExit("解包失败：未生成 squashfs-root")
    shutil.move(str(produced), str(prefix))
    shutil.rmtree(temp_dir, ignore_errors=True)
    logger.info("解包完成并安装到 %s（耗时 %.1fs）", prefix, time.time() - started)


def setup_sandbox(prefix: Path, sudo_pass: str) -> None:
    sandbox = prefix / "usr/share/cursor/chrome-sandbox"
    if not sandbox.is_file():
        raise SystemExit(f"缺少 chrome-sandbox：{sandbox}")
    run(["chown", "root:root", str(sandbox)], sudo_pass=sudo_pass)
    run(["chmod", "4755", str(sandbox)], sudo_pass=sudo_pass)
    stat = run(f"stat -c '%U:%G:%a' {sandbox}")
    if stat != "root:root:4755":
        raise SystemExit(f"chrome-sandbox 权限异常：{stat}")
    logger.info("chrome-sandbox 权限：%s", stat)


def update_desktop(prefix: Path) -> None:
    icon = prefix / "usr/share/pixmaps/co.anysphere.cursor.png"
    if not icon.is_file():
        raise SystemExit(f"未找到图标：{icon}")
    if not LAUNCHER.is_file():
        logger.warning("未找到启动包装器 %s，桌面项将直接调用 AppRun", LAUNCHER)
        launcher = str(prefix / "AppRun")
    else:
        launcher = str(LAUNCHER)
        LAUNCHER.chmod(LAUNCHER.stat().st_mode | 0o755)
    DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_FILE.write_text(DESKTOP_TEMPLATE.format(launcher=launcher, icon=icon), encoding="utf-8")
    DESKTOP_FILE.chmod(0o644)
    run("update-desktop-database ~/.local/share/applications/ >/dev/null 2>&1 || true", check=False)
    logger.info("桌面快捷方式已更新：%s", DESKTOP_FILE)


def installed_version(prefix: Path) -> str | None:
    for candidate in (
        prefix / "usr/share/cursor/resources/app/package.json",
        prefix / "resources/app/package.json",
    ):
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8")).get("version")
            except Exception:  # noqa: BLE001
                return None
    return None


def check(prefix: Path) -> bool:
    version = installed_version(prefix)
    sandbox = prefix / "usr/share/cursor/chrome-sandbox"
    stat = run(f"stat -c '%U:%G:%a' {sandbox}", check=False) if sandbox.exists() else "缺失"
    ok = bool(version) and (prefix / "AppRun").is_file() and stat == "root:root:4755" and DESKTOP_FILE.is_file()
    logger.info(
        "安装检查：版本 %s，sandbox %s，AppRun %s，桌面项 %s → %s",
        version or "未安装",
        stat,
        "存在" if (prefix / "AppRun").exists() else "缺失",
        "存在" if DESKTOP_FILE.exists() else "缺失",
        "✅ 正常" if ok else "⚠️ 不完整",
    )
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="本机 Cursor 卸载与解包安装（aarch64）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--appimage", type=Path, default=Path.home() / "Downloads/install/Cursor-3.27.12-aarch64.AppImage")
    parser.add_argument("--prefix", type=Path, default=DEFAULT_PREFIX, help="安装目录")
    parser.add_argument("--sudo-pass", default=None, help="sudo 口令（默认读 .env 的 LOCAL_SUDO_PASS / NODE123_RDP_PASS）")
    parser.add_argument("--keep-old", action="store_true", help="保留旧版本目录（默认备份后移除）")
    parser.add_argument("--check", action="store_true", help="仅检查当前安装")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    prefix: Path = args.prefix.expanduser()
    appimage: Path = args.appimage.expanduser()

    if args.check:
        return 0 if check(prefix) else 1

    if os.uname().machine != "aarch64":
        raise SystemExit(f"当前架构 {os.uname().machine} 与 aarch64 AppImage 不匹配")

    password = sudo_password(args.sudo_pass)
    stop_running(password)
    if not args.keep_old:
        backup_old(prefix)
    elif prefix.exists():
        logger.warning("--keep-old 已指定，但安装目录已存在，解包将失败，请先手动处理")
    extract_appimage(appimage, prefix)
    setup_sandbox(prefix, password)
    update_desktop(prefix)
    ok = check(prefix)
    logger.info("安装完成：Cursor %s ✅", installed_version(prefix) or "未知版本")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
