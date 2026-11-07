#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/check_x11_sockets.py - 检查远端 X11 socket 是否有对应的活跃 Xorg 进程

背景：
    xrdp 会话结束后可能残留 /tmp/.X11-unix/X* socket（无对应 Xorg 进程），
    这些僵死 socket 会导致下次 xrdp 连接时「登录成功但黑屏」。
    本脚本逐一比对 socket 与 Xorg 进程，列出需要清理的僵死项。

用法:
    python scripts/check_x11_sockets.py --host 172.19.51.123 --user bot --password ***
"""

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REMOTE_SCRIPT = r"""
set -u
echo '=== X11 socket 与 Xorg 进程比对 ==='
DEAD_LIST=""
for s in /tmp/.X11-unix/X*; do
    [ -e "$s" ] || continue
    n="$(basename "$s")"
    d="${n#X}"
    owner="$(stat -c '%U' "$s" 2>/dev/null)"
    if pgrep -f "Xorg :$d" >/dev/null 2>&1; then
        echo "  $n (属主 $owner) -> 有对应 Xorg 进程（正常）"
    elif [ "$owner" != "$(id -un)" ]; then
        # 属主不是当前用户，多为 GDM greeter 的本地显示，不属于僵死残留
        echo "  $n (属主 $owner) -> 非本用户会话（本地 greeter），忽略"
    else
        echo "  $n (属主 $owner) -> 本用户会话但无 Xorg 进程（僵死残留）"
        DEAD_LIST="$DEAD_LIST $d"
    fi
done

if [ -n "$DEAD_LIST" ]; then
    echo "=== 发现僵死 socket:$DEAD_LIST ==="
    echo '（可用 scripts/rdp_session_reset.py --node node123 清理）'
else
    echo '=== 无僵死 socket，状态干净 ==='
fi

echo '=== 当前用户图形会话进程 ==='
pgrep -u "$(id -u)" -a -x gnome-shell 2>/dev/null || echo '(无 gnome-shell)'
pgrep -u "$(id -u)" -a -f 'gnome-session-binary' 2>/dev/null || echo '(无 gnome-session-binary)'
pgrep -u "$(id -u)" -a -f 'Xorg :' 2>/dev/null || echo '(无 Xorg)'
"""


def run_ssh(host: str, user: str, password: str, script: str, timeout: int = 60) -> str:
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
    parser = argparse.ArgumentParser(description="检查远端 X11 僵死 socket")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    password = args.password or os.getenv("SSH_PASS", "")
    print(run_ssh(args.host, args.user, password, REMOTE_SCRIPT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
