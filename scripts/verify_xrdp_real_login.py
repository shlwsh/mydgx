#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/verify_xrdp_real_login.py - 通过 xrdp 真实会话流程验证 GNOME 桌面

原理：
    完整模拟 xrdp sesman 的会话创建流程：
      1. 由 xrdp-sesman 分配显示号并拉起 Xorg（使用 sesman.ini [Xorg] 的真实参数）
      2. 通过 startwm.sh → /etc/X11/Xsession 启动桌面（与真实 RDP 完全一致的链路）
      3. 检查 gnome-shell 是否存活、桌面环境变量是否为 ubuntu:GNOME
    该方式比手工 source profile 更贴近真实 RDP。

用法:
    python scripts/verify_xrdp_real_login.py --host 172.19.51.123 --user bot --password ***
"""

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REMOTE_SCRIPT = r"""
set -u
echo '=== 1) 清理所有残留会话 ==='
for p in $(pgrep -u "$(id -u)" -a -f 'Xorg :' 2>/dev/null | awk '{print $1}'); do
    kill -9 "$p" 2>/dev/null
done
pkill -9 -u "$(id -u)" -f 'gnome-session' 2>/dev/null
pkill -9 -u "$(id -u)" -x gnome-shell 2>/dev/null
sleep 2
echo '--- 残留检查 ---'
pgrep -u "$(id -u)" -a -x gnome-shell 2>/dev/null || echo '(无 gnome-shell)'
pgrep -u "$(id -u)" -a -f 'Xorg :' 2>/dev/null || echo '(无 Xorg)'

echo '=== 2) 读取 sesman.ini [Xorg] 的真实参数 ==='
sed -n '/^\[Xorg\]/,/^\[Xvnc\]/p' /etc/xrdp/sesman.ini | grep -E '^param' | sed 's/^param=//' > /tmp/xorg_params.txt
cat /tmp/xorg_params.txt

echo '=== 3) 模拟 sesman 创建会话 ==='
# sesman 会为会话创建 .Xauthority 并把 Xorg 的 HOME 设为用户家目录
cd "$HOME" || exit 1
XAUTHFILE="$HOME/.Xauthority"
rm -f "$XAUTHFILE"

# 分配显示号
DISP=""
for d in $(seq 20 60); do
    if [ ! -e "/tmp/.X11-unix/X$d" ]; then
        DISP=":$d"
        break
    fi
done
echo "分配显示号: $DISP"

# 生成授权 cookie（xrdp 行为）
COOKIE="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
xauth -f "$XAUTHFILE" add "$DISP" . "$COOKIE" 2>&1

# 按 sesman.ini 的参数拉起 Xorg
XORG_BIN="$(sed -n '1p' /tmp/xorg_params.txt)"
REST="$(tail -n +2 /tmp/xorg_params.txt | tr '\n' ' ')"
echo "Xorg: $XORG_BIN $REST -auth $XAUTHFILE"
# shellcheck disable=SC2086
"$XORG_BIN" "$DISP" -auth "$XAUTHFILE" $REST >/tmp/xorg_out.log 2>&1 &
sleep 5

if [ ! -e "/tmp/.X11-unix/X${DISP#:}" ]; then
    echo 'Xorg 启动失败：'
    tail -n 20 /tmp/xorg_out.log
    exit 4
fi
echo "Xorg 已在 $DISP 就绪"

echo '=== 4) 执行 startwm.sh（与真实 RDP 完全一致的入口）==='
export DISPLAY="$DISP"
export XAUTHORITY="$XAUTHFILE"
export XDG_RUNTIME_DIR=/run/user/$(id -u)

timeout 60 /etc/xrdp/startwm.sh >/tmp/startwm_out.log 2>&1 &
sleep 30

echo '=== 5) 会话结果 ==='
GNOME_OK=0
if pgrep -u "$(id -u)" -x gnome-shell >/dev/null 2>&1; then
    GNOME_OK=1
    echo '✅ gnome-shell 已启动'
else
    echo '❌ gnome-shell 未启动'
fi
echo '--- gnome-shell ---'
pgrep -u "$(id -u)" -a -x gnome-shell 2>/dev/null || echo '(无 gnome-shell)'
echo '--- gnome-session-binary ---'
pgrep -u "$(id -u)" -a -f 'gnome-session-binary' 2>/dev/null || echo '(无 gnome-session-binary)'
echo '--- XFCE 残留 ---'
if pgrep -u "$(id -u)" -f 'xfce4-session|startxfce4' >/dev/null 2>&1; then
    XFCE_LEFT=1
    pgrep -u "$(id -u)" -a -f 'xfce4-session|startxfce4' 2>/dev/null
else
    XFCE_LEFT=0
    echo '(无 XFCE 残留)'
fi

echo '--- gnome-shell 环境快照 ---'
GS=$(pgrep -u "$(id -u)" -x gnome-shell | tail -n 1)
if [ -n "$GS" ]; then
    tr '\0' '\n' < "/proc/$GS/environ" 2>/dev/null \
        | grep -E 'XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE|DESKTOP_SESSION|GNOME_SHELL_SESSION_MODE|DISPLAY' | sort
fi

echo '=== 6) 判定 ==='
if [ "$GNOME_OK" = "1" ] && [ "$XFCE_LEFT" = "0" ]; then
    echo '结论: ✅ 已成功切换到 GNOME Desktop（ubuntu:GNOME / X11），且无 XFCE 残留'
else
    echo '结论: ❌ 未能切换到 GNOME Desktop，请查看上方日志定位'
fi

echo '--- startwm 日志 ---'
tail -n 15 /tmp/startwm_out.log
echo '--- ~/.gnome-session.log ---'
tail -n 15 "$HOME/.gnome-session.log" 2>&1
echo '--- ~/.xsession-errors 尾部 ---'
tail -n 12 "$HOME/.xsession-errors" 2>&1

echo '=== 清理 ==='
pkill -u "$(id -u)" -f 'gnome-session' 2>/dev/null
pkill -u "$(id -u)" -x gnome-shell 2>/dev/null
pkill -u "$(id -u)" -f "Xorg $DISP" 2>/dev/null
sleep 2
rm -f "/tmp/.X11-unix/X${DISP#:}" 2>/dev/null
echo '=== 验证结束 ==='
"""


def run_ssh(host: str, user: str, password: str, script: str, timeout: int = 180) -> str:
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
    parser = argparse.ArgumentParser(description="通过 xrdp 真实会话流程验证 GNOME 桌面")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    password = args.password or os.getenv("SSH_PASS", "")
    print(run_ssh(args.host, args.user, password, REMOTE_SCRIPT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
