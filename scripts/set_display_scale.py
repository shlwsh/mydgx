#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调整 X11 / RDP 会话的等效显示缩放（如 125% 放大）。

原理与限制
----------
* 真正的“调低分辨率、等效放大”只能在显示器/RandR 支持的模式上完成；
  xorgxrdp（xrdp 的 Xorg 后端）会拒绝服务端的 RRSetScreenSize 请求，
  会话分辨率必须由 RDP 客户端（mstsc / FreeRDP / Remmina）指定，
  即服务端无法把 2504x1323 直接改成 2000x1058。
* 因此本脚本采用两级策略：
  1) 先尝试按缩放比例新增 RandR 模式并切换（普通 GPU / dummy 驱动可用）；
  2) 若被 xorgxrdp 拒绝，则退化为“文本级 125%”：
     GNOME text-scaling-factor + Xft.dpi，仅放大文字（图标/面板不变）。
     需要界面整体放大时，请用 --reconnect-hint 给出的客户端参数重连。

用法示例
--------
    .venv/bin/python scripts/set_display_scale.py --percent 125
    .venv/bin/python scripts/set_display_scale.py --percent 125 --display :10.0
    .venv/bin/python scripts/set_display_scale.py --reconnect-hint
    .venv/bin/python scripts/set_display_scale.py --revert
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys

logger = logging.getLogger("display_scale")

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"


def run(cmd: list[str], display: str, check: bool = False) -> subprocess.CompletedProcess:
    """执行命令并返回结果，DISPLAY 显式注入。"""
    env = dict(os.environ, DISPLAY=display)
    logger.debug("执行: %s", " ".join(cmd))
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=check)


def current_geometry(display: str) -> tuple[int, int] | None:
    """读取当前屏幕尺寸。"""
    res = run(["xrandr", "--query"], display)
    if res.returncode != 0:
        logger.error("无法读取 X 显示 %s: %s", display, res.stderr.strip())
        return None
    m = re.search(r"current (\d+) x (\d+)", res.stdout)
    if not m:
        logger.error("xrandr 输出中未找到当前分辨率")
        return None
    return int(m.group(1)), int(m.group(2))


def output_name(display: str) -> str | None:
    """取第一个 connected 的输出名（如 rdp0 / eDP-1）。"""
    res = run(["xrandr", "--query"], display)
    for line in res.stdout.splitlines():
        if " connected" in line:
            return line.split()[0]
    return None


def build_modeline(width: int, height: int, refresh: int) -> tuple[str, str] | None:
    """用 gtf 生成 modeline；gtf 缺失时返回 None。"""
    if not shutil.which("gtf"):
        return None
    res = run(["gtf", str(width), str(height), str(refresh)], display=os.environ.get("DISPLAY", ":0"))
    if res.returncode != 0:
        return None
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("Modeline "):
            parts = line[len("Modeline "):].split()
            name = parts[0].strip('"')
            return name, " ".join(parts[1:])
    return None


def try_resize(display: str, width: int, height: int, refresh: int,
               output: str) -> bool:
    """尝试真正切换分辨率（新增模式 -> 绑定 -> 应用）。"""
    mode = build_modeline(width, height, refresh)
    if mode is None:
        logger.warning("未找到 gtf，跳过分辨率切换尝试")
        return False
    name, modeline = mode
    if run(["xrandr", "--newmode", name] + modeline.split(), display).returncode != 0:
        # 模式已存在时 newmode 会报错，属可忽略情况
        logger.debug("newmode 返回非零（可能已存在）: %s", name)
    run(["xrandr", "--addmode", output, name], display)
    res = run(["xrandr", "--output", output, "--mode", name], display)
    if res.returncode == 0:
        logger.info("已切换到 %sx%s（%s%%，真实分辨率缩放）", width, height, name)
        return True

    stderr = (res.stderr or "").strip()
    reason = next((ln.strip() for ln in stderr.splitlines()
                   if "X Error" in ln or "BadMatch" in ln), "未知错误")
    logger.warning("分辨率切换被拒绝: %s", reason)
    if "BadMatch" in stderr:
        logger.warning("原因：xorgxrdp 只接受由 RDP 客户端发起的分辨率变更，"
                       "服务端 RRSetScreenSize 会被拒绝")
    # 清理刚加入的模式，避免污染会话
    run(["xrandr", "--delmode", output, name], display)
    run(["xrandr", "--rmmode", name], display)
    return False


def apply_text_scale(percent: int, display: str) -> bool:
    """文本级缩放：GNOME text-scaling-factor + Xft.dpi。"""
    factor = percent / 100.0
    dpi = round(96 * factor)
    res = run(["gsettings", "set", "org.gnome.desktop.interface",
               "text-scaling-factor", f"{factor:.2f}"], display)
    if res.returncode != 0:
        logger.error("设置 text-scaling-factor 失败: %s", res.stderr.strip())
        return False
    run(["xrandr", "--dpi", str(dpi)], display)
    logger.info("已应用文本级放大: text-scaling-factor=%.2f, Xft.dpi=%d", factor, dpi)
    logger.info("注意：图标、面板等非文本元素不会随之放大")
    return True


def revert(display: str) -> int:
    """恢复 100%（文本级缩放）。"""
    run(["gsettings", "set", "org.gnome.desktop.interface",
         "text-scaling-factor", "1.0"], display)
    run(["xrandr", "--dpi", "96"], display)
    logger.info("已恢复 text-scaling-factor=1.0, Xft.dpi=96")
    return 0


def reconnect_hint(width: int, height: int, node: str) -> None:
    """打印客户端侧真正实现 125% 的方式。"""
    print(f"""
真实 125% 放大（界面整体变大）需由 RDP 客户端指定较小分辨率，服务端无法代劳：

  FreeRDP : xfreerdp /v:{node} /size:{width}x{height} /rfx /bpp:24
  Windows : 在 .rdp 文件里写 desktopwidth:i:{width} / desktopheight:i:{height}
            或连接前把「显示」选项卡的远程桌面大小改为 {width}x{height}
  Remmina : resolution={width}x{height}

本项目脚本可直接使用：
  .venv/bin/python scripts/rdp_connect.py --node node0 --size {width}x{height}
""")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="调整 X11/RDP 会话的等效显示缩放（默认 125%）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="说明：xorgxrdp 下无法由服务端改分辨率，脚本会自动退化为文本级放大。",
    )
    parser.add_argument("--percent", type=int, default=125,
                        help="放大比例，默认 125（即 1.25 倍，界面上等效 125%%）")
    parser.add_argument("--display", default=os.environ.get("DISPLAY", ":10.0"),
                        help="目标 X 显示，默认取 $DISPLAY 或 :10.0")
    parser.add_argument("--refresh", type=int, default=50,
                        help="新增模式使用的刷新率，默认 50Hz（与 xorgxrdp 一致）")
    parser.add_argument("--no-resize", action="store_true",
                        help="跳过分辨率切换，直接应用文本级放大")
    parser.add_argument("--reconnect-hint", action="store_true",
                        help="只打印客户端重连参数，不修改任何设置")
    parser.add_argument("--revert", action="store_true",
                        help="恢复 100%% 缩放")
    parser.add_argument("--node", default="node0",
                        help="--reconnect-hint 时提示的节点名，默认 node0")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format=LOG_FORMAT)

    if args.percent < 50 or args.percent > 400:
        logger.error("--percent 需在 50~400 之间")
        return 2

    geo = current_geometry(args.display)
    if geo is None:
        return 1
    cur_w, cur_h = geo
    factor = args.percent / 100.0
    target_w, target_h = round(cur_w / factor), round(cur_h / factor)

    if args.reconnect_hint:
        reconnect_hint(target_w, target_h, args.node)
        return 0

    if args.revert:
        return revert(args.display)

    logger.info("当前分辨率 %sx%s，目标 %sx%s（放大 %d%%）",
                cur_w, cur_h, target_w, target_h, args.percent)

    if not args.no_resize:
        output = output_name(args.display)
        if not output:
            logger.error("未找到可用输出，无法切换分辨率")
        elif try_resize(args.display, target_w, target_h, args.refresh, output):
            return 0

    if apply_text_scale(args.percent, args.display):
        logger.info("如需界面整体放大，请按以下参数重连客户端：")
        reconnect_hint(target_w, target_h, args.node)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
