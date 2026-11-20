#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/check_desktop_integration.py - 工作站桌面集成自检（图标 + 窗口关联）

背景：
    本机出现过两类“应用没有图标”的问题，表现相似但根因完全不同：
    1) 图标名在图标主题里查不到（例如只把 PNG 放在 hicolor 未登记的尺寸目录，
       或 Icon= 指向的绝对路径不存在）→ Dock/桌面项显示通用灰色图标；
    2) 运行中的窗口 WM_CLASS 与桌面项 ID 不一致且桌面项未声明 StartupWMClass
       → GNOME 无法把窗口关联到桌面项，Dock 显示通用图标且拿不到应用名。

功能：
    --favorites  检查 GNOME 收藏（Dock）应用：桌面项是否存在、Exec/Icon 是否可用、
                 图标名能否被图标主题解析（多个尺寸逐一验证）；
    --windows    检查当前运行窗口：WM_CLASS 能否关联到某个桌面项（关联失败即无图标）；
    默认同时执行两项。

用法示例：
    .venv/bin/python scripts/check_desktop_integration.py
    .venv/bin/python scripts/check_desktop_integration.py --favorites
    .venv/bin/python scripts/check_desktop_integration.py --windows -v
"""

import argparse
import logging
import os
import re
import subprocess
import sys

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("check_desktop_integration")

# 这些 WM_CLASS 属于窗口宿主/桌面组件，本就没有对应桌面项，不算异常
IGNORED_WM_CLASS = {"gjs", "gnome-shell", "nautilus-desktop", "xmessage"}
ICON_PROBE_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def _gi():
    """延迟导入 GI，避免仅 --windows 模式下因 Gtk 缺库而失败。"""
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gio, Gtk

    return Gio, Gtk


def ensure_gi_available() -> None:
    """PyGObject 只安装在系统 Python 中；项目 .venv 内没有时自动切换到 /usr/bin/python3。"""
    try:
        import gi  # noqa: F401
        return
    except ImportError:
        pass
    system_python = "/usr/bin/python3"
    if sys.executable == system_python or not os.path.isfile(system_python):
        return
    logger.info("当前解释器缺少 PyGObject，切换到系统解释器 %s 重新执行", system_python)
    os.execv(system_python, [system_python, *sys.argv])


def run(command: list[str], check: bool = False) -> str:
    result = subprocess.run(command, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败（{result.returncode}）：{command}\n{result.stderr}")
    return (result.stdout or "").strip()


def favorites() -> list[str]:
    raw = run(["gsettings", "get", "org.gnome.shell", "favorite-apps"])
    return re.findall(r"'([^']+)'", raw)


def check_favorites() -> int:
    """返回异常数量。"""
    Gio, Gtk = _gi()
    theme = Gtk.IconTheme.get_default()
    apps = {a.get_id(): a for a in Gio.AppInfo.get_all()}
    fav_ids = favorites()
    logger.info("检查 %d 个 Dock 收藏应用", len(fav_ids))

    bad = 0
    for fid in fav_ids:
        app = apps.get(fid)
        if app is None:
            logger.error("❌ %s：桌面项缺失（被删除或位于非标准目录）", fid)
            bad += 1
            continue

        problems = []
        exec_line = app.get_commandline() or ""
        executable = exec_line.split()[0] if exec_line else ""
        if executable and not os.path.isabs(executable):
            if not any(
                os.access(os.path.join(p, executable), os.X_OK) for p in os.environ.get("PATH", "").split(":")
            ):
                problems.append(f"Exec 不可执行（{executable}）")
        elif executable and not os.access(executable, os.X_OK):
            problems.append(f"Exec 路径不可执行（{executable}）")

        icon = app.get_icon()
        icon_name = icon.to_string() if icon else ""
        if not icon_name:
            problems.append("Icon 字段为空")
        elif icon_name.startswith("/"):
            if not os.path.isfile(icon_name):
                problems.append(f"图标路径不存在（{icon_name}）")
        else:
            missing = [size for size in ICON_PROBE_SIZES if not theme.lookup_icon(icon_name, size, 0)]
            if missing:
                problems.append(f"图标名 {icon_name} 在尺寸 {missing} 查不到（图标主题未索引该尺寸目录？）")

        if problems:
            logger.error("❌ %s：%s", fid, "；".join(problems))
            bad += 1
        else:
            logger.info("✅ %s（%s）", fid, app.get_display_name())

    if bad:
        logger.error("收藏应用异常 %d 个（共 %d 个）", bad, len(fav_ids))
    else:
        logger.info("全部 %d 个收藏应用图标与桌面项正常", len(fav_ids))
    return bad


def _desktop_index() -> dict[str, str]:
    """WM_CLASS（小写）→ 桌面项 ID 的映射：桌面项文件名与 StartupWMClass 都参与匹配。"""
    Gio, _ = _gi()
    index: dict[str, str] = {}
    for info in Gio.AppInfo.get_all():
        if not isinstance(info, Gio.DesktopAppInfo):
            continue
        fid = info.get_id()
        if not fid:
            continue
        index.setdefault(fid[:-8].lower(), fid)
        startup = info.get_startup_wm_class()
        if startup:
            index.setdefault(startup.lower(), fid)
    return index


def check_windows(strict: bool = False) -> int:
    """返回无法关联的窗口数量。"""
    index = _desktop_index()
    root = run(["xprop", "-root", "_NET_CLIENT_LIST"])
    window_ids = re.findall(r"0x[0-9a-f]+", root)
    if not window_ids:
        logger.warning("未取到窗口列表（会话不是 X11 或 xprop 不可用）")
        return 0

    bad = 0
    for wid in window_ids:
        props = run(["xprop", "-id", wid, "WM_CLASS", "_NET_WM_NAME"])
        match = re.search(r'WM_CLASS\(STRING\) = "([^"]*)", "([^"]*)"', props)
        if not match:
            continue
        instance, klass = match.group(1), match.group(2)
        title_match = re.search(r'_NET_WM_NAME\(UTF8_STRING\) = "([^"]*)"', props)
        title = (title_match.group(1) if title_match else "")[:40]

        fid = index.get(instance.lower()) or index.get(klass.lower())
        if fid:
            logger.debug("✅ %s/%s → %s（%s）", instance, klass, fid, title)
            continue
        if instance.lower() in IGNORED_WM_CLASS or klass.lower() in IGNORED_WM_CLASS:
            logger.debug("跳过窗口宿主：%s/%s（%s）", instance, klass, title)
            continue

        level = logger.error if strict else logger.warning
        level("⚠️ 窗口无法关联桌面项：WM_CLASS=%s/%s 标题=%s（Dock 将显示通用图标）", instance, klass, title or "-")
        bad += 1

    if bad:
        logger.warning("有 %d 个窗口无法关联桌面项，可在对应 .desktop 中补 StartupWMClass", bad)
    else:
        logger.info("所有运行窗口都能关联到桌面项")
    return bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="工作站桌面集成自检：Dock 收藏图标是否可用、运行窗口能否关联桌面项",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--favorites", action="store_true", help="仅检查 Dock 收藏应用")
    parser.add_argument("--windows", action="store_true", help="仅检查运行窗口的桌面项关联")
    parser.add_argument("--strict", action="store_true", help="窗口无法关联时也返回非 0（默认仅警告）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    logging.getLogger().setLevel(logging.DEBUG if args.verbose else logging.INFO)
    ensure_gi_available()
    do_all = not (args.favorites or args.windows)

    bad = 0
    if do_all or args.favorites:
        bad += check_favorites()
    if do_all or args.windows:
        unlinked = check_windows(args.strict)
        bad += unlinked if args.strict else 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
