#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/check_systemd_user_instance.py - 检查 systemd 用户实例与用户 D-Bus 是否健康

背景：
    gnome-session 依赖 systemd 用户实例（--user）与用户 D-Bus 总线来拉起
    gnome-shell 及各类 GNOME 组件。若用户实例异常（如 user@1000.service 未注册、
    D-Bus 会话总线不可用），gnome-session 会静默退出且无任何输出。

用法:
    python scripts/check_systemd_user_instance.py --host 172.19.51.123 --user bot --password ***
"""

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REMOTE_SCRIPT = r"""
set -u
echo '=== 1) systemd 用户实例 ==='
systemctl --user is-system-running 2>&1
echo '--- user@1000.service 在系统层 ---'
systemctl status user@1000.service --no-pager 2>&1 | head -n 12
echo '--- 系统层 user-$(id -u).slice ---'
systemctl status "user-$(id -u).slice" --no-pager 2>&1 | head -n 10

echo '=== 2) 用户 D-Bus 总线 ==='
echo "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-<空>}"
ls -la "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/" 2>&1 | head -n 15
echo '--- bus socket ---'
ls -la "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/bus" 2>&1
echo '--- dbus-daemon 进程 ---'
pgrep -a -u "$(id -u)" 'dbus-daemon' 2>/dev/null || echo '(无用户 dbus-daemon)'

echo '=== 3) busctl 连通性 ==='
busctl --user list >/dev/null 2>&1 && echo 'busctl --user 可用 ✅' || echo 'busctl --user 不可用 ❌'
busctl --user list 2>&1 | head -n 8

echo '=== 4) 关键 GNOME 组件是否在 systemd 用户实例中注册 ==='
systemctl --user list-units --all --no-pager 2>&1 | grep -iE 'gnome|graphical|gnome-shell' | head -n 15

echo '=== 5) /etc/dbus-1 与 systemd 集成 ==='
ls -la /etc/dbus-1/system.d/ 2>&1 | head -n 8
ls /usr/share/dbus-1/system-services/ 2>&1 | grep -iE 'systemd|dbus' | head -n 8
echo '--- dbus-daemon 是否以 systemd 激活方式运行 ---'
pgrep -a 'dbus-daemon' 2>/dev/null | head -n 6

echo '=== 6) libpam / pam_systemd 配置 ==='
grep -rn 'pam_systemd' /etc/pam.d/ 2>&1 | head -n 10

echo '=== 7) 用户家目录权限与属主 ==='
ls -ld "$HOME" 2>&1
echo '=== 8) 磁盘与 inode ==='
df -h "$HOME" 2>&1 | tail -n 2
df -i "$HOME" 2>&1 | tail -n 2

echo '=== 9) 内存 ==='
free -h 2>&1 | head -n 3
"""


def run_ssh(host: str, user: str, password: str, script: str, timeout: int = 90) -> str:
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
    parser = argparse.ArgumentParser(description="检查 systemd 用户实例与 D-Bus 健康度")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    password = args.password or os.getenv("SSH_PASS", "")
    print(run_ssh(args.host, args.user, password, REMOTE_SCRIPT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
