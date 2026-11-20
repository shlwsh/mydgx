#!/usr/bin/env python3
"""本机（cube-fe5e / NODE123）Antigravity 系列应用卸载与重装工具。

支持两个相互独立的应用：
    ide   Antigravity IDE  →  安装包 “Antigravity IDE/”，装到 /opt/antigravity-ide
    app   Antigravity 桌面应用 →  安装包 “Antigravity-arm64/”，装到 /opt/antigravity

流程：
    1. 停止正在运行的对应应用进程；
    2. 备份旧安装目录（默认保留，可用 --purge-old 删除）；
    3. 解包 tar.gz 中的对应目录到目标路径；
    4. chrome-sandbox 设为 root:root + 4755（需要 sudo）；
    5. 校正 /usr/local/bin 命令入口与桌面快捷方式；
    6. 校验产物与版本。

典型用法：
    .venv/bin/python install/install_antigravity.py --app ide --tarball "$HOME/Downloads/Antigravity IDE.tar.gz"
    .venv/bin/python install/install_antigravity.py --app app --tarball "$HOME/Downloads/Antigravity (1).tar.gz"
    .venv/bin/python install/install_antigravity.py --app app --check
详细说明见同目录《Antigravity_安装与重装说明.md》。
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
import tarfile
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("install_antigravity")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APPLICATIONS_DIR = Path.home() / ".local/share/applications"

# 桌面项模板：IDE 支持“新窗口”动作与目录/文件参数；桌面应用沿用官方 %U 单入口
DESKTOP_IDE = """[Desktop Entry]
Type=Application
Name=Antigravity IDE
GenericName=Antigravity IDE
Comment=Antigravity IDE - AI Code Editor
Exec={prefix}/{binary} %F
Icon={icon}
Terminal=false
Categories=TextEditor;Development;IDE;
MimeType=text/plain;inode/directory;
StartupNotify=true
StartupWMClass={wmclass}
Actions=new-empty-window;

[Desktop Action new-empty-window]
Name=New Empty Window
Exec={prefix}/{binary} --new-window %F
Icon={icon}
"""

DESKTOP_APP = """[Desktop Entry]
Type=Application
Name=Antigravity
GenericName=Antigravity
Comment=Antigravity - Agentic Desktop Application
Exec={prefix}/{binary} %U
Icon={icon}
Terminal=false
Categories=Development;IDE;
StartupNotify=true
StartupWMClass={wmclass}
"""

PROFILES: dict[str, dict] = {
    "ide": {
        "label": "Antigravity IDE",
        "member": "Antigravity IDE",
        "tarball": Path.home() / "Downloads/Antigravity IDE.tar.gz",
        "prefix": Path("/opt/antigravity-ide"),
        "binary": "antigravity-ide",
        "link": Path("/usr/local/bin/antigravity-ide"),
        "desktop": APPLICATIONS_DIR / "antigravity-ide.desktop",
        "icon": "antigravity-ide",
        "wmclass": "antigravity-ide",
        "version_kind": "product_json",
        "desktop_template": DESKTOP_IDE,
    },
    "app": {
        "label": "Antigravity",
        "member": "Antigravity-arm64",
        "tarball": Path.home() / "Downloads/Antigravity (1).tar.gz",
        "prefix": Path("/opt/antigravity"),
        "binary": "antigravity",
        "link": Path("/usr/local/bin/antigravity"),
        "desktop": APPLICATIONS_DIR / "antigravity.desktop",
        "icon": "antigravity",
        "wmclass": "antigravity",
        "version_kind": "version_file",
        "desktop_template": DESKTOP_APP,
    },
}


def sudo_password(cli_value: str | None) -> str:
    """按 参数 → LOCAL_SUDO_PASS → NODE123_RDP_PASS → SSH_NODE123_PASS → 交互输入 的顺序取口令。"""
    if cli_value:
        return cli_value
    load_dotenv(PROJECT_ROOT / ".env")
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
        command,
        shell=isinstance(command, str),
        input=stdin_data,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败（{result.returncode}）：{command}\n{result.stdout}\n{result.stderr}")
    return (result.stdout or "").strip()


def version_text(prefix: Path, profile: dict) -> str:
    """从 product.json（IDE）或 version 文件（桌面应用）读取版本。"""
    if profile["version_kind"] == "product_json":
        candidate = prefix / "resources/app/product.json"
        try:
            info = json.loads(candidate.read_text(encoding="utf-8"))
            return f"IDE {info.get('ideVersion') or '?'} / VSCode {info.get('version') or '?'}"
        except Exception:  # noqa: BLE001
            return "未安装"
    candidate = prefix / "version"
    try:
        return f"{candidate.read_text(encoding='utf-8').strip()}"
    except Exception:  # noqa: BLE001
        return "未安装"


def short_version(text: str) -> str:
    """提取用于备份目录命名的版本号。"""
    if not text or text == "未安装":
        return "unknown"
    head = text.split("/")[0].replace("IDE ", "").strip()
    return head or "unknown"


def stop_running(profile: dict) -> None:
    pattern = f"{profile['prefix']}/{profile['binary']}"
    run(f"pkill -f '{pattern}' || true", check=False)
    run(f"pkill -f \"{profile['prefix']}/bin/{profile['binary']}\" || true", check=False)
    time.sleep(1)
    logger.info("已停止正在运行的 %s 进程（如有）", profile["label"])


def backup_old(prefix: Path, profile: dict, sudo_pass: str, purge: bool) -> Path | None:
    if not prefix.exists():
        return None
    version = short_version(version_text(prefix, profile))
    backup = prefix.with_name(f"{prefix.name}-{version}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    run(["mv", str(prefix), str(backup)], sudo_pass=sudo_pass)
    if purge:
        run(["rm", "-rf", str(backup)], sudo_pass=sudo_pass)
        logger.info("旧版本 %s 已删除（路径 %s）", version, backup)
        return None
    logger.info("旧版本 %s 已备份至 %s", version, backup)
    return backup


def extract_tarball(tarball: Path, staging: Path, profile: dict) -> Path:
    """解包到暂存目录，返回解包后的应用根目录。"""
    if not tarball.is_file():
        raise SystemExit(f"安装包不存在：{tarball}")
    member = profile["member"]
    logger.info("开始解包 %s", tarball)
    started = time.time()
    staging.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name in (member, member + "/") or m.name.startswith(member + "/")]
        if not members:
            raise SystemExit(f"安装包内未找到 `{member}/` 目录，是否用错了 --app？")
        try:
            tar.extractall(staging, members=members, filter="data")
        except TypeError:  # Python < 3.12 无 filter 参数
            tar.extractall(staging, members=members)
    root = staging / member
    binary = profile["binary"]
    if not (root / binary).is_file() and not (root / "bin" / binary).is_file():
        raise SystemExit(f"解包结果缺少可执行文件：{root}/{binary}")
    logger.info("解包完成（耗时 %.1fs，%s）", time.time() - started, version_text(root, profile))
    return root


def install(extracted: Path, prefix: Path, sudo_pass: str) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    run(["mv", str(extracted), str(prefix)], sudo_pass=sudo_pass)
    run(["chown", "-R", f"{os.getuid()}:{os.getgid()}", str(prefix)], sudo_pass=sudo_pass)
    logger.info("已安装到 %s", prefix)


def setup_sandbox(prefix: Path, sudo_pass: str) -> None:
    sandbox = prefix / "chrome-sandbox"
    if not sandbox.is_file():
        raise SystemExit(f"缺少 chrome-sandbox：{sandbox}")
    run(["chown", "root:root", str(sandbox)], sudo_pass=sudo_pass)
    run(["chmod", "4755", str(sandbox)], sudo_pass=sudo_pass)
    stat = run(f"stat -c '%U:%G:%a' {sandbox}")
    if stat != "root:root:4755":
        raise SystemExit(f"chrome-sandbox 权限异常：{stat}")
    logger.info("chrome-sandbox 权限：%s", stat)


def setup_cli_link(prefix: Path, profile: dict, sudo_pass: str) -> None:
    target = prefix / "bin" / profile["binary"]
    if not target.is_file():
        target = prefix / profile["binary"]
    link = profile["link"]
    run(["ln", "-sfn", str(target), str(link)], sudo_pass=sudo_pass)
    resolved = run(f"readlink -f {link}", check=False)
    if resolved != str(target):
        raise SystemExit(f"符号链接校正失败：{link} -> {resolved}")
    logger.info("命令行入口：%s -> %s", link, target)


def setup_desktop(prefix: Path, profile: dict) -> None:
    desktop = profile["desktop"]
    icon = Path("/usr/share/icons/hicolor/512x512/apps") / f"{profile['icon']}.png"
    if not icon.is_file():
        logger.warning("未找到系统图标 %s，将回退到应用内置图标", icon)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    back = desktop.with_suffix(".desktop.bak")
    if desktop.is_file() and not back.exists():
        shutil.copy2(desktop, back)
    desktop.write_text(
        profile["desktop_template"].format(prefix=prefix, binary=profile["binary"], icon=profile["icon"], wmclass=profile["wmclass"]),
        encoding="utf-8",
    )
    desktop.chmod(0o644)
    run("update-desktop-database ~/.local/share/applications/ >/dev/null 2>&1 || true", check=False)
    logger.info("桌面快捷方式已更新：%s", desktop)


def check(profile: dict) -> bool:
    prefix: Path = profile["prefix"]
    binary = prefix / profile["binary"]
    sandbox = prefix / "chrome-sandbox"
    stat = run(f"stat -c '%U:%G:%a' {sandbox}", check=False) if sandbox.exists() else "缺失"
    link = profile["link"]
    link_ok = run(f"readlink -f {link}", check=False).startswith(str(prefix))
    desktop = profile["desktop"]
    ok = binary.is_file() and stat == "root:root:4755" and link_ok and desktop.is_file()
    logger.info(
        "%s 检查：版本 %s，sandbox %s，主程序 %s，命令入口 %s，桌面项 %s → %s",
        profile["label"],
        version_text(prefix, profile),
        stat,
        "存在" if binary.is_file() else "缺失",
        "正常" if link_ok else "异常",
        "存在" if desktop.is_file() else "缺失",
        "✅ 正常" if ok else "⚠️ 不完整",
    )
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="本机 Antigravity 系列应用重装（tar.gz 解包到 /opt）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--app", choices=sorted(PROFILES), default="ide", help="目标应用：ide=Antigravity IDE，app=Antigravity 桌面应用")
    parser.add_argument("--tarball", type=Path, default=None, help="安装包路径（默认按 --app 取对应默认包）")
    parser.add_argument("--prefix", type=Path, default=None, help="安装目录（默认按 --app 取默认路径）")
    parser.add_argument("--sudo-pass", default=None, help="sudo 口令（默认读 .env 的 LOCAL_SUDO_PASS / NODE123_RDP_PASS / SSH_NODE123_PASS）")
    parser.add_argument("--purge-old", action="store_true", help="重装后删除旧版本备份（默认保留）")
    parser.add_argument("--check", action="store_true", help="仅检查当前安装")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    profile = dict(PROFILES[args.app])
    if args.prefix:
        profile["prefix"] = args.prefix.expanduser()
    tarball: Path = (args.tarball or profile["tarball"]).expanduser()

    if args.check:
        return 0 if check(profile) else 1

    password = sudo_password(args.sudo_pass)
    prefix: Path = profile["prefix"]
    stop_running(profile)
    backup_old(prefix, profile, password, args.purge_old)

    with tempfile.TemporaryDirectory(prefix="ag-", dir=str(Path.home() / ".cache")) as tmp:
        extracted = extract_tarball(tarball, Path(tmp), profile)
        install(extracted, prefix, password)

    setup_sandbox(prefix, password)
    setup_cli_link(prefix, profile, password)
    setup_desktop(prefix, profile)
    ok = check(profile)
    logger.info("安装完成：%s %s", profile["label"], version_text(prefix, profile))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
