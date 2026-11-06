#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/repair_and_solidify_rdp.py - 双工作站远程桌面 (xrdp) 终极修复与防闪退固化脚本

解决问题：
1. 彻底解决“输入密码后瞬间闪退”问题：
   - 还原 /etc/xrdp/startwm.sh 为经 Xsession.d 完整初始化的官方标准启动链；
   - 规范 ~/.xsessionrc 导出 Ubuntu GNOME 桌面环境规范；
   - 强制清除系统遗留的 /tmp/.X*-lock 锁文件及僵尸进程；
   - 在 sesman.ini 中配置 KillDisconnected=true，会话断开后立即优雅回收资源，确保下次连接始终为全新干净的 Display :10 会话，彻底根除会话冲突；
2. 彻底禁用锁屏与休眠，防止无屏幕环境下桌面锁死或黑屏；
3. 同步加固 Node0 (70) 与 Node1 (159)。
"""

import os
import sys
import argparse

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

import paramiko

logger = setup_logger("SolidifyRDP")

STANDARD_STARTWM = r'''#!/bin/sh
# xrdp X session start script (Canonical Ubuntu Xsession Standard)

if test -r /etc/profile; then
	. /etc/profile
fi

if test -r ~/.profile; then
	. ~/.profile
fi

test -x /etc/X11/Xsession && exec /etc/X11/Xsession
exec /bin/sh /etc/X11/Xsession
'''

XSESSIONRC_CONTENT = r'''export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_DATA_DIRS=/usr/share/ubuntu:/usr/local/share:/usr/share:/var/lib/snapd/desktop
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg
'''

REMOTE_REPAIR_SCRIPT = r'''#!/usr/bin/env bash
set -euo pipefail

echo "=========================================="
echo "开始执行远程桌面终极修复与稳定性加固"
echo "=========================================="

# 1. 停用 xrdp 服务以进行深度清理
echo "--> 1. 停止 xrdp 进程与服务"
systemctl stop xrdp xrdp-sesman 2>/dev/null || true

# 2. 强力清理所有残留锁与僵尸进程
echo "--> 2. 彻底清理 Xorg 僵尸进程与所有 /tmp/.X*-lock 锁文件"
pkill -9 -f "Xorg.*xrdp" 2>/dev/null || true
pkill -9 -f "xrdp-chansrv" 2>/dev/null || true
pkill -9 -f "xrdp-sesman" 2>/dev/null || true
pkill -9 -f "gnome-session" 2>/dev/null || true
pkill -9 -f "gnome-shell" 2>/dev/null || true

# 关键：彻底清除 /tmp 下所有 .X 开头的锁文件与 socket
rm -rf /tmp/.X* /tmp/.xorgxrdp* /run/xrdp/sockdir/* 2>/dev/null || true
echo "已清除所有 X11 锁文件"

# 3. 部署标准 startwm.sh
echo "--> 3. 部署标准 /etc/xrdp/startwm.sh"
cat << 'EOF' > /etc/xrdp/startwm.sh
#!/bin/sh
if test -r /etc/profile; then
	. /etc/profile
fi

if test -r ~/.profile; then
	. ~/.profile
fi

test -x /etc/X11/Xsession && exec /etc/X11/Xsession
exec /bin/sh /etc/X11/Xsession
EOF
chmod 755 /etc/xrdp/startwm.sh

# 4. 优化 /etc/xrdp/sesman.ini 配置 KillDisconnected=true (根治重连冲突)
echo "--> 4. 优化 sesman.ini (配置 KillDisconnected=true 保证会话纯净)"
if [ -f /etc/xrdp/sesman.ini ]; then
    # 确保 KillDisconnected=true
    if grep -q "^KillDisconnected=" /etc/xrdp/sesman.ini; then
        sed -i 's/^KillDisconnected=.*/KillDisconnected=true/g' /etc/xrdp/sesman.ini
    elif grep -q "\[Sessions\]" /etc/xrdp/sesman.ini; then
        sed -i '/\[Sessions\]/a KillDisconnected=true' /etc/xrdp/sesman.ini
    fi
    # 确保 DisconnectedTimeLimit=0 (立即释放)
    if grep -q "^DisconnectedTimeLimit=" /etc/xrdp/sesman.ini; then
        sed -i 's/^DisconnectedTimeLimit=.*/DisconnectedTimeLimit=0/g' /etc/xrdp/sesman.ini
    fi
fi

# 5. 修正 xrdp 用户与证书权限
echo "--> 5. 检查并修正 xrdp 证书与权限"
adduser xrdp ssl-cert 2>/dev/null || true
if [ -f /etc/xrdp/key.pem ]; then
    chmod 640 /etc/xrdp/key.pem
    chown root:ssl-cert /etc/xrdp/key.pem
fi

# 6. 配置 Polkit 免弹窗认证
echo "--> 6. 固化 Polkit 色彩管理免认证"
mkdir -p /etc/polkit-1/localauthority/50-local.d
cat << 'EOF' > /etc/polkit-1/localauthority/50-local.d/45-allow-colord.pkla
[Allow Colord all Users]
Identity=unix-user:*
Action=org.freedesktop.color-manager.create-device;org.freedesktop.color-manager.create-profile;org.freedesktop.color-manager.delete-device;org.freedesktop.color-manager.delete-profile;org.freedesktop.color-manager.modify-device;org.freedesktop.color-manager.modify-profile
ResultAny=no
ResultInactive=no
ResultActive=yes
EOF

# 7. 为所有实际用户部署 .xsessionrc 与禁用锁屏
echo "--> 7. 部署 ~/.xsessionrc 并彻底禁用锁屏/休眠"
for homedir in /home/*; do
    [ -d "$homedir" ] || continue
    u="$(basename "$homedir")"
    
    # 写入 .xsessionrc
    cat << 'EOF' > "$homedir/.xsessionrc"
export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_DATA_DIRS=/usr/share/ubuntu:/usr/local/share:/usr/share:/var/lib/snapd/desktop
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg
EOF
    chown "$u:$u" "$homedir/.xsessionrc"
    chmod 644 "$homedir/.xsessionrc"

    # 清理旧残留错误日志
    rm -f "$homedir/.xsession-errors"* "$homedir/.xorgxrdp."*.log "$homedir/.Xauthority" 2>/dev/null || true

    # 使用 sudo -u 设置 gsettings 禁用锁屏与息屏
    sudo -u "$u" dbus-launch gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
    sudo -u "$u" dbus-launch gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
    sudo -u "$u" dbus-launch gsettings set org.gnome.desktop.lockdown disable-lock-screen true 2>/dev/null || true
    echo "已固化用户 $u 桌面环境与无锁屏策略"
done

# 8. 屏蔽系统睡眠休眠目标
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true

# 9. 启动并启用 xrdp
echo "--> 8. 启动 xrdp 服务"
systemctl daemon-reload
systemctl restart xrdp xrdp-sesman
systemctl enable xrdp xrdp-sesman >/dev/null 2>&1 || true

sleep 2
echo "=========================================="
echo "远程桌面服务启动就绪"
echo "=========================================="
'''


def get_ssh_client(host, user, password, timeout=15):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=22, username=user, password=password, timeout=timeout)
    return client


def repair_node(host, user, password, name):
    logger.info("=" * 60)
    logger.info(f"正在对 {name} ({host}) 执行终极 RDP 闪退修复与加固...")
    logger.info("=" * 60)

    client = get_ssh_client(host, user, password)
    try:
        remote_sh = "/tmp/repair_rdp_final.sh"
        sftp = client.open_sftp()
        with sftp.file(remote_sh, "w") as f:
            f.write(REMOTE_REPAIR_SCRIPT.replace("\r\n", "\n"))
        sftp.close()

        stdin, stdout, stderr = client.exec_command(f"echo {password} | sudo -S chmod +x {remote_sh} && echo {password} | sudo -S {remote_sh}")
        out = stdout.read().decode("utf-8").strip()
        err = stderr.read().decode("utf-8").strip()
        code = stdout.channel.recv_exit_status()
        client.exec_command(f"echo {password} | sudo -S rm -f {remote_sh}")

        clean_err = "\n".join(l for l in err.splitlines() if "[sudo] password for" not in l).strip()
        if out:
            logger.info(f"[命令输出]\n{out}")
        if clean_err and code != 0:
            logger.error(f"[错误输出]\n{clean_err}")
            raise RuntimeError(f"修复失败: {clean_err}")

        # 核验
        chk_cmd = """bash -c '
echo "=== 服务状态 ==="
systemctl is-active xrdp xrdp-sesman
echo "=== 3389 监听 ==="
ss -tlpn | grep 3389
echo "=== X11 锁文件残留检查 ==="
ls -la /tmp/.X* 2>/dev/null || echo "无任何锁残留 (正常)"
'"""
        stdin, stdout, stderr = client.exec_command(f"echo {password} | sudo -S {chk_cmd}")
        chk_out = "\n".join(l for l in stdout.read().decode("utf-8").splitlines() if "[sudo] password for" not in l).strip()
        logger.info(f"[核验输出]\n{chk_out}")
        logger.info(f"[{name}] 远程桌面环境已成功恢复并加固完成！")

    finally:
        client.close()


def main():
    init_env()

    parser = argparse.ArgumentParser(
        description="双工作站远程桌面终极修复工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--node",
        choices=["all", "node0", "node1"],
        default="all",
        help="目标节点: all, node0, node1 (默认: all)",
    )
    args = parser.parse_args()

    host0 = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user0 = os.getenv("SSH_NODE0_USER", "winbot")
    pass0 = os.getenv("SSH_NODE0_PASS", "winbot")

    host1 = os.getenv("NODE1_MGMT_IP", "172.19.49.159")
    user1 = os.getenv("SSH_NODE1_USER", "dgxdeploy")
    pass1 = os.getenv("SSH_NODE1_PASS", "winning")

    nodes = []
    if args.node in ("all", "node0"):
        nodes.append((host0, user0, pass0, "Node0 工作站 (70)"))
    if args.node in ("all", "node1"):
        nodes.append((host1, user1, pass1, "Node1 工作站 (159)"))

    for host, u, p, name in nodes:
        repair_node(host, u, p, name)

    logger.info("=" * 60)
    logger.info("双工作站远程桌面终极修复已全部顺利完成！")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
