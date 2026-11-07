#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/check_dconf_health.py - 检查 dconf 配置存储健康度（Node123 的 gsettings 读空异常）

背景：
    Node123 上 `gsettings get org.gnome.shell enabled-extensions` 返回空，
    而基线 Node0 返回正常列表。dconf/dconf-service 异常会导致 gnome-shell
    启动时配置读取失败而退出。

用法:
    python scripts/check_dconf_health.py --host 172.19.51.123 --user bot --password ***
"""

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REMOTE_SCRIPT = r"""
set -u
echo '=== 1) dconf-service 进程 ==='
pgrep -u "$(id -u)" -a -f 'dconf-service' 2>/dev/null || echo '(无 dconf-service)'

echo '=== 2) ~/.config/dconf 目录 ==='
ls -la "$HOME/.config/dconf" 2>&1
echo '--- user 文件 ---'
stat -c '%a %U:%G %s %n' "$HOME/.config/dconf/user" 2>&1

echo '=== 3) dconf 读写测试 ==='
echo '--- read ---'
dconf read /org/gnome/shell/enabled-extensions 2>&1
echo '--- write ---'
dconf write /org/gnome/shell/enabled-extensions "['probe-test']" 2>&1
echo '--- read again ---'
dconf read /org/gnome/shell/enabled-extensions 2>&1
echo '--- reset ---'
dconf reset -f /org/gnome/shell/enabled-extensions 2>&1

echo '=== 4) gsettings 读取 ==='
gsettings get org.gnome.shell enabled-extensions 2>&1
gsettings get org.gnome.desktop.interface gtk-theme 2>&1
gsettings get org.gnome.desktop.session idle-delay 2>&1

echo '=== 5) GSettings 后端 ==='
echo "GSETTINGS_BACKEND=${GSETTINGS_BACKEND:-<空>}"
echo "DCONF_PROFILE=${DCONF_PROFILE:-<空>}"
which dconf 2>&1

echo '=== 6) dconf 强制重建 user 文件 ==='
if [ -f "$HOME/.config/dconf/user" ]; then
    cp "$HOME/.config/dconf/user" /tmp/dconf_user.bak 2>/dev/null
    echo "已备份到 /tmp/dconf_user.bak"
    rm -f "$HOME/.config/dconf/user"
    sleep 2
    dconf write /org/gnome/shell/enabled-extensions "['probe-test2']" 2>&1
    echo '--- 重建后读取 ---'
    dconf read /org/gnome/shell/enabled-extensions 2>&1
    echo '--- 重建后文件 ---'
    stat -c '%a %U:%G %s %n' "$HOME/.config/dconf/user" 2>&1
fi

echo '=== 7) dconf-service 重启后重试 ==='
pkill -u "$(id -u)" -f 'dconf-service' 2>/dev/null
sleep 2
dconf read /org/gnome/shell/enabled-extensions 2>&1
pgrep -u "$(id -u)" -a -f 'dconf-service' 2>/dev/null || echo '(dconf-service 未自动拉起)'

echo '=== 检查结束 ==='
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
    parser = argparse.ArgumentParser(description="检查 dconf 健康度")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    password = args.password or os.getenv("SSH_PASS", "")
    print(run_ssh(args.host, args.user, password, REMOTE_SCRIPT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
