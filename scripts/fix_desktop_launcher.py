#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/fix_desktop_launcher.py - 通用桌面项（启动器）图标修复工具

解决三类“应用没有图标”的问题：
    1. 图标名在图标主题中查不到：只把 PNG 放在 hicolor 未登记的尺寸目录（如本机
       未登记 1024x1024），或 Icon= 指向的绝对路径不存在；
    2. 运行窗口无法关联桌面项：WM_CLASS 与桌面项 ID 不一致且未声明 StartupWMClass，
       GNOME 只能显示通用灰色图标；
    3. 桌面项 Name= 为空、Version= 误填应用版本号等规范问题。

做法：
    在 ~/.local/share/applications/ 写入同名覆盖项（不改厂商系统文件，应用升级后
    仍生效），按需补 StartupWMClass / Name / Icon，并从母版 PNG 重建多尺寸图标。

用法示例：
    # 自动探测运行窗口的 WM_CLASS 并写入覆盖项（云枢这类问题）
    .venv/bin/python scripts/fix_desktop_launcher.py --desktop yunshu.desktop

    # 仅重建图标（图标名查不到、Dock 显示灰块时）
    .venv/bin/python scripts/fix_desktop_launcher.py --desktop antigravity-ide.desktop \
        --icon-master install/assets/antigravity-ide.png

    # 应用未运行、需要手工指定 WM_CLASS 时
    .venv/bin/python scripts/fix_desktop_launcher.py --desktop yunshu.desktop \
        --wm-class YunshuCross --name 云枢

    # 只检查，不改文件
    .venv/bin/python scripts/fix_desktop_launcher.py --desktop yunshu.desktop --check
"""

import argparse
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_common import (  # noqa: E402
    LOG_FORMAT,
    USER_APPLICATIONS_DIR,
    find_desktop_file,
    generate_icons,
    read_key,
    run,
    sudo_password,
    which,
)

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("fix_desktop_launcher")

SPEC_VERSION_LINE = "Version"  # 该键应为 desktop 规范版本，厂商常误填应用版本号，覆盖时剔除


def detect_wm_class(keyword: str | None = None) -> str | None:
    """从运行中的窗口读取 WM_CLASS 的 class 部分（第二个值）。

    同一应用可能有多个（隐藏的）辅助窗口，WM_CLASS 与主窗口未必一致
    （如 Antigravity IDE：辅助窗口 antigravity-ide，主窗口 Antigravity IDE），
    因此按“归一化后包含关键字”筛选候选，再取面积最大的窗口。
    """
    if not which("xprop"):
        return None
    normalize = lambda text: re.sub(r"[-_\s]+", "", text.lower())  # noqa: E731
    key = normalize(keyword) if keyword else ""

    root = run(["xprop", "-root", "_NET_CLIENT_LIST"], check=False)
    best: tuple[int, str] | None = None
    for wid in re.findall(r"0x[0-9a-f]+", root):
        match = re.search(r'WM_CLASS\(STRING\) = "([^"]*)", "([^"]*)"', run(["xprop", "-id", wid, "WM_CLASS"], check=False))
        if not match:
            continue
        instance, klass = match.group(1), match.group(2)
        if key and key not in normalize(instance) and key not in normalize(klass):
            continue
        info = run(["xwininfo", "-id", wid], check=False)
        width = re.search(r"Width:\s*(\d+)", info)
        height = re.search(r"Height:\s*(\d+)", info)
        area = (int(width.group(1)) if width else 0) * (int(height.group(1)) if height else 0)
        if best is None or area > best[0]:
            best = (area, klass)
    return best[1] if best else None


def localized_value(source: Path, key: str) -> str | None:
    """取首个本地化键值，如 Name[zh_CN]、GenericName[zh_CN]，用作无基础键时的兜底。"""
    for line in source.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}[") and "=" in stripped:
            return stripped.split("=", 1)[1].strip()
    return None


def _insert_in_main_group(lines: list[str], extra: list[str]) -> None:
    """把新增键插入 [Desktop Entry] 组内（插在下一个组头之前的空行之前）。"""
    end = len(lines)
    started = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[Desktop Entry]":
            started = True
            continue
        if started and stripped.startswith("["):
            end = index
            break
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    lines[end:end] = extra


def build_override(source: Path, wm_class: str | None, name: str | None, icon: str | None) -> str:
    """以现有桌面项为模板生成覆盖内容；未提供的新值一律保留原行。"""
    lines: list[str] = []
    for line in source.read_text(encoding="utf-8", errors="ignore").splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key == SPEC_VERSION_LINE:
            continue
        if key == "StartupWMClass" and wm_class:
            continue
        if key.startswith("Name") and name:
            continue
        if key == "Icon" and icon:
            continue
        lines.append(line)
    if name:
        for index, line in enumerate(lines):
            if line.strip() == "[Desktop Entry]":
                lines[index + 1 : index + 1] = [f"Name={name}", f"Name[zh_CN]={name}"]
                break
    # 只有 GenericName[xx] 而无 GenericName 时，desktop 校验会报错，这里补基础键
    if not any(line.strip().startswith("GenericName=") for line in lines):
        generic = localized_value(source, "GenericName")
        if generic:
            for index, line in enumerate(lines):
                if line.strip().startswith("GenericName["):
                    lines.insert(index, f"GenericName={generic}")
                    break
    if icon:
        # 必须写进 [Desktop Entry] 组：文件末尾可能已是 [Desktop Action ...] 组
        extra = [f"Icon={icon}"]
        if wm_class:
            extra.append(f"StartupWMClass={wm_class}")
        _insert_in_main_group(lines, extra)
    elif wm_class:
        _insert_in_main_group(lines, [f"StartupWMClass={wm_class}"])
    return "\n".join(lines).rstrip() + "\n"


def check(desktop_id: str) -> bool:
    target = find_desktop_file(desktop_id)
    if target is None:
        logger.error("%s 检查：未找到桌面项", desktop_id)
        return False
    icon = read_key(target, "Icon") or ""
    executable = (read_key(target, "Exec") or "").split()
    binary = executable[0] if executable else ""
    icon_ok = Path(icon).is_file() if icon.startswith("/") else bool(icon)
    exec_ok = not binary or Path(binary).is_file() or bool(which(binary))
    ok = icon_ok and exec_ok
    logger.info(
        "%s 检查：位置 %s，Icon=%s，StartupWMClass=%s，Exec %s → %s",
        desktop_id,
        target,
        icon or "缺失",
        read_key(target, "StartupWMClass") or "未设置",
        "可执行" if exec_ok else f"不可执行（{binary}）",
        "✅ 正常" if ok else "⚠️ 异常",
    )
    return ok


def apply(desktop_id: str, wm_class: str | None, name: str | None, icon_master: Path | None,
          icon_name: str | None, sudo_pass: str | None) -> bool:
    user_path = USER_APPLICATIONS_DIR / desktop_id
    found = find_desktop_file(desktop_id)
    if found is None:
        logger.error("未找到桌面项：%s（请确认应用已安装）", desktop_id)
        return False

    source = user_path if user_path.is_file() else found
    if name is None:
        # Name= 为空是常见厂商问题，用本地化名称兜底，避免桌面项无显示名
        if not (read_key(source, "Name") or "").strip():
            name = localized_value(source, "Name")

    icon: str | None = icon_name
    if icon_master:
        master = icon_master.expanduser()
        if not master.is_file():
            logger.error("图标母版不存在：%s", master)
            return False
        if icon is None:
            existing = read_key(source, "Icon") or ""
            icon = Path(existing).stem if existing.startswith("/") else (existing or desktop_id[: -len(".desktop")])
        generate_icons(icon, master, sudo_pass or sudo_password())

    content = build_override(source, wm_class, name, icon)
    USER_APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    if user_path.is_file():
        backup = user_path.with_name(user_path.name + ".bak")
        backup.write_text(user_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        logger.info("已备份原覆盖项：%s", backup)
    user_path.write_text(content, encoding="utf-8")
    user_path.chmod(0o644)
    logger.info("已写入覆盖项：%s", user_path)

    warning = run(["desktop-file-validate", str(user_path)], check=False)
    if warning:
        logger.warning("desktop 校验告警：%s", warning)
    run(["update-desktop-database", str(USER_APPLICATIONS_DIR)], check=False)
    return check(desktop_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="通用桌面项图标修复：补 StartupWMClass、重建多尺寸图标、规范 Name",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--desktop", required=True, help="目标桌面项 ID 或文件名，如 yunshu.desktop")
    parser.add_argument("--wm-class", default=None, help="StartupWMClass 取值（默认自动从运行窗口探测）")
    parser.add_argument("--name", default=None, help="桌面项 Name（修正 Name= 为空）")
    parser.add_argument("--icon-master", type=Path, default=None, help="图标母版 PNG；指定后重建多尺寸图标")
    parser.add_argument("--icon-name", default=None, help="图标名（默认沿用桌面项 Icon 字段）")
    parser.add_argument("--sudo-pass", default=None, help="sudo 口令（默认读 .env）")
    parser.add_argument("--check", action="store_true", help="仅检查，不修改文件")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    desktop_id = args.desktop if args.desktop.endswith(".desktop") else f"{args.desktop}.desktop"

    if args.check:
        return 0 if check(desktop_id) else 1

    wm_class = args.wm_class
    if wm_class is None:
        wm_class = detect_wm_class(desktop_id[: -len(".desktop")])
        if wm_class:
            logger.info("自动探测到 WM_CLASS：%s", wm_class)
        else:
            logger.info("未探测到运行窗口，保留现有 StartupWMClass 设置")
    return 0 if apply(desktop_id, wm_class, args.name, args.icon_master, args.icon_name, args.sudo_pass) else 1


if __name__ == "__main__":
    sys.exit(main())
