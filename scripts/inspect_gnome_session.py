#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/inspect_gnome_session.py - 采集节点 GNOME 会话基线（对比 XFCE / GNOME）

用途：
    为将某节点远程桌面统一到 GNOME Desktop 前，采集两机现状差异，
    确保切换目标机具备 gnome-session / ubuntu.session / 扩展等全部组件。

用法:
    python scripts/inspect_gnome_session.py --host 172.19.51.123 --user bot --password ***
"""

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from env_helper import setup_logger, init_env
except ImportError:
    def setup_logger(name="GNOME-Inspect", level=20):
        import logging

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        return logging.getLogger(name)

    def init_env():
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


logger = setup_logger("GNOME-Inspect")

REMOTE_SCRIPT = r"""
echo '=== 主机 ==='
hostname
echo '=== 关键 GNOME 二进制 ==='
for b in gnome-session gnome-shell gnome-session-binary ubuntu-session gsettings; do
    printf '%s -> ' "$b"
    command -v "$b" || echo MISSING
done
echo '=== 关键 dpkg 包 ==='
dpkg -l gnome-shell gnome-session ubuntu-desktop-minimal ubuntu-desktop ubuntu-session 2>/dev/null \
    | awk '/^(ii|un)/{print $1, $2, $3}'
echo '=== ubuntu-dock 扩展包 ==='
dpkg -l 2>/dev/null | grep -E 'ubuntu-dock|gnome-shell-extension' | awk '{print $1, $2, $3}' | head -n 12
echo '=== 会话入口文件 ==='
ls -la "$HOME"/.xsession "$HOME"/.xsessionrc "$HOME"/.gnomerc 2>&1
echo '=== gnome-shell 首启标记 ==='
ls -la "$HOME"/.config/gnome-shell/ 2>&1
echo '=== 当前远程会话桌面类型 ==='
for p in $(pgrep -u "$(id -u)" -x gnome-shell 2>/dev/null; pgrep -u "$(id -u)" -x xfce4-session 2>/dev/null); do
    echo "--- pid=$p"
    tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null \
        | grep -E 'XDG_CURRENT_DESKTOP|GNOME_SHELL_SESSION_MODE|XDG_SESSION_TYPE|DESKTOP_SESSION' || true
done
echo '=== sesman.ini [Xorg] 段 ==='
sed -n '/^\[Xorg\]/,/^\[Xvnc\]/p' /etc/xrdp/sesman.ini 2>/dev/null | grep -E '^param' | head -n 12
echo '=== GDM Wayland 开关 ==='
grep -nE '^WaylandEnable|^AutomaticLogin' /etc/gdm3/custom.conf 2>/dev/null || echo '(无显式设置)'
echo '=== colord 豁免 ==='
ls -l /etc/polkit-1/localauthority/50-local.d/ 2>/dev/null | grep -i color || echo '(无 colord 规则)'
"""


def run_ssh(host: str, user: str, password: str, script: str, timeout: int = 60) -> str:
    """通过 SSH 执行远程脚本（base64 编码规避多层转义问题）。"""
    import paramiko

    payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, port=22, username=user, password=password or None, timeout=12)
        _, stdout, stderr = client.exec_command(f"echo {payload} | base64 -d | bash", timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        if err.strip():
            out += f"\n[stderr]\n{err}"
        return out
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="采集节点 GNOME 会话基线")
    parser.add_argument("--host", required=True, help="目标主机 IP")
    parser.add_argument("--user", required=True, help="SSH 用户名")
    parser.add_argument("--password", default="", help="SSH 口令（缺省读环境变量 SSH_PASS）")
    args = parser.parse_args()

    init_env()
    password = args.password or os.getenv("SSH_PASS", "")

    logger.info("=" * 60)
    logger.info(f"采集 GNOME 会话基线: {args.user}@{args.host}")
    logger.info("=" * 60)
    print(run_ssh(args.host, args.user, password, REMOTE_SCRIPT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
