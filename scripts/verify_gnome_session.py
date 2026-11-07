#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/verify_gnome_session.py - 静态巡检节点 xrdp 会话的 GNOME Desktop 配置状态

巡检项（对齐 /etc/X11/Xsession 的真实选择逻辑）：
    1. 会话入口：确认 ~/.xsession 是 GNOME 入口（含 `gnome-session --session=ubuntu`）；
    2. 环境变量：确认 ~/.xsessionrc 被正确 source（ubuntu:GNOME / X11 后端）；
    3. 会话管理器：确认 x-session-manager 指向 gnome-session；
    4. 组件可用性：确认 gnome-shell / gnome-session / ubuntu.session 齐备；
    5. GDM 开关：确认 WaylandEnable=false 且未启用自动登录（避免与远程会话争抢 D-Bus 总线名）；
    6. colord 豁免 / sesman.ini 参数与双机基线对齐。

> 注意：本脚本为**静态配置巡检**，不实际创建会话。
> 需要端到端验证（真实拉起 gnome-shell）请用 `scripts/verify_xrdp_real_login.py`。

用法:
    python scripts/verify_gnome_session.py --host 172.19.51.123 --user bot --password ***
"""

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from env_helper import setup_logger, init_env
except ImportError:
    def setup_logger(name="GNOME-Verify", level=20):
        import logging

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        return logging.getLogger(name)

    def init_env():
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


logger = setup_logger("GNOME-Verify")

REMOTE_SCRIPT = r"""
echo '=== 1) 会话入口文件 ==='
ls -la "$HOME"/.xsession "$HOME"/.xsessionrc "$HOME"/.xsession-gnome 2>&1
if [ -f "$HOME/.xsession" ] && [ -x "$HOME/.xsession" ]; then
    if grep -q 'gnome-session --session=ubuntu' "$HOME/.xsession" 2>/dev/null; then
        echo '判定: ✅ GNOME（~/.xsession 为 GNOME 入口，显式指定 --session=ubuntu）'
    elif grep -q 'startxfce4' "$HOME/.xsession" 2>/dev/null; then
        echo '判定: ❌ XFCE（~/.xsession 执行 startxfce4，会话被锁定在 XFCE4）'
    else
        echo '判定: ⚠️ 未知（~/.xsession 存在但未匹配 GNOME/XFCE 特征）'
        grep -n 'exec' "$HOME/.xsession" 2>/dev/null
    fi
else
    echo '判定: ⚠️ 无 ~/.xsession（会话将回退到 x-session-manager，需确认 ubuntu.session 可被选中）'
fi

echo '=== 2) ~/.xsessionrc 内容 ==='
cat "$HOME/.xsessionrc" 2>&1

echo '=== 3) source 后的环境变量 ==='
( set -a; . "$HOME/.xsessionrc" 2>/dev/null; set +a
  echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP}"
  echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE}"
  echo "GNOME_SHELL_SESSION_MODE=${GNOME_SHELL_SESSION_MODE}"
  echo "GDK_BACKEND=${GDK_BACKEND}"
  echo "LIBGL_ALWAYS_SOFTWARE=${LIBGL_ALWAYS_SOFTWARE}" )

echo '=== 4) 会话管理器指向 ==='
readlink -f /etc/alternatives/x-session-manager 2>&1

echo '=== 5) GNOME 组件 ==='
for b in gnome-session gnome-shell gsettings gnome-shell-extension-tool; do
    printf '%s -> ' "$b"; command -v "$b" || echo MISSING
done
ls -l /usr/share/gnome-session/sessions/ubuntu.session 2>&1

echo '=== 6) 运行态桌面进程 ==='
echo '-- xrdp 会话内的 Xorg --'
pgrep -u "$(id -u)" -a -f '/usr/lib/xorg/Xorg' 2>/dev/null || echo '(无 Xorg)'
echo '-- gnome-shell --'
pgrep -u "$(id -u)" -a -x gnome-shell 2>/dev/null || echo '(无 gnome-shell)'
echo '-- xfce4-session（应为空）--'
pgrep -u "$(id -u)" -a -f 'xfce4-session|startxfce4' 2>/dev/null || echo '(无 XFCE 残留)'

echo '=== 7) GDM 开关（Wayland 与自动登录）==='
grep -nE '^WaylandEnable|^[[:space:]]*AutomaticLogin' /etc/gdm3/custom.conf 2>/dev/null || echo '(未显式设置)'

echo '=== 8) colord 豁免 ==='
ls -l /etc/polkit-1/localauthority/50-local.d/ 2>/dev/null | grep -i color || echo '(无)'

echo '=== 9) sesman.ini [Xorg] 参数 ==='
sed -n '/^\[Xorg\]/,/^\[Xvnc\]/p' /etc/xrdp/sesman.ini 2>/dev/null | grep -E '^param'
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
    parser = argparse.ArgumentParser(description="验证节点 xrdp 会话是否已切换到 GNOME Desktop")
    parser.add_argument("--host", required=True, help="目标主机 IP")
    parser.add_argument("--user", required=True, help="SSH 用户名")
    parser.add_argument("--password", default="", help="SSH 口令（缺省读环境变量 SSH_PASS）")
    args = parser.parse_args()

    init_env()
    password = args.password or os.getenv("SSH_PASS", "")

    logger.info("=" * 60)
    logger.info(f"验证 GNOME 会话: {args.user}@{args.host}")
    logger.info("=" * 60)
    print(run_ssh(args.host, args.user, password, REMOTE_SCRIPT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
