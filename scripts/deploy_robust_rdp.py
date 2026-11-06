#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双工作站稳定版远程桌面 (xrdp + xorgxrdp + GNOME) 自动化部署脚本
彻底解决 GNOME Remote Desktop 在无屏幕/NVIDIA 环境下重定向崩溃、卡死在登录界面的问题。
"""

import os
import sys
import logging
from dotenv import load_dotenv
import paramiko

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("DeployRobustRDP")


def get_ssh_client(host, user, password=None, timeout=15):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=22, username=user, password=password, timeout=timeout)
    return client


def exec_remote(client, cmd, desc=None, check=True):
    if desc:
        logger.info(f"==> {desc}")
    logger.debug(f"执行远程命令: {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode("utf-8").strip()
    err = stderr.read().decode("utf-8").strip()
    code = stdout.channel.recv_exit_status()

    if out:
        logger.info(f"[命令输出]\n{out}")
    if err and code != 0:
        logger.warning(f"[错误输出]\n{err}")

    if check and code != 0:
        raise RuntimeError(f"命令执行失败 (退出码={code}): {cmd}\n{err}")

    return code, out, err


POLKIT_COLORD_RULE = """[Allow Colord all Users]
Identity=unix-user:*
Action=org.freedesktop.color-manager.create-device;org.freedesktop.color-manager.create-profile;org.freedesktop.color-manager.delete-device;org.freedesktop.color-manager.delete-profile;org.freedesktop.color-manager.modify-device;org.freedesktop.color-manager.modify-profile
ResultAny=no
ResultInactive=no
ResultActive=yes
"""

XSESSION_CONTENT = """export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_DATA_DIRS=/usr/share/ubuntu:/usr/local/share:/usr/share:/var/lib/snapd/desktop
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg
"""


def deploy_node(client, node_name, user_list):
    logger.info("=" * 70)
    logger.info(f"开始在 {node_name} 上安装并配置工业级稳定远程桌面 (xrdp + xorgxrdp)...")
    logger.info("=" * 70)

    # 1. 停用并禁用易卡死的 gnome-remote-desktop
    stop_grd = """
sudo systemctl stop gnome-remote-desktop 2>/dev/null || true
sudo systemctl disable gnome-remote-desktop 2>/dev/null || true
sudo systemctl stop gnome-remote-desktop-system 2>/dev/null || true
sudo systemctl disable gnome-remote-desktop-system 2>/dev/null || true
"""
    exec_remote(client, stop_grd, desc=f"[{node_name}] 停用冲突的 gnome-remote-desktop 服务")

    # 2. 安装 xrdp 与 xorgxrdp
    install_cmd = """
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq xrdp xorgxrdp
"""
    exec_remote(client, install_cmd, desc=f"[{node_name}] 通过 apt 安装 xrdp 与 xorgxrdp")

    # 3. 将用户加入 ssl-cert 组
    for u in user_list:
        exec_remote(
            client,
            f"sudo adduser {u} ssl-cert || true",
            desc=f"[{node_name}] 将用户 {u} 加入 ssl-cert 组以授权 TLS 访问",
        )

    # 4. 配置 polkit 规则避免登录弹窗
    polkit_cmd = f"""
sudo mkdir -p /etc/polkit-1/localauthority/50-local.d/
cat << 'EOF' | sudo tee /etc/polkit-1/localauthority/50-local.d/45-allow-colord.pkla > /dev/null
{POLKIT_COLORD_RULE}
EOF
"""
    exec_remote(client, polkit_cmd, desc=f"[{node_name}] 配置 polkit 规则避免色彩管理认证弹窗")

    # 5. 配置用户的 .xsessionrc 确保加载标准 Ubuntu GNOME 桌面
    for u in user_list:
        xsession_cmd = f"""
cat << 'EOF' | sudo -u {u} tee /home/{u}/.xsessionrc > /dev/null
{XSESSION_CONTENT}
EOF
sudo chown {u}:{u} /home/{u}/.xsessionrc
sudo chmod 644 /home/{u}/.xsessionrc
"""
        exec_remote(client, xsession_cmd, desc=f"[{node_name}] 为用户 {u} 配置 ~/.xsessionrc 桌面环境变量")

    # 6. 配置 GDM 禁用 Wayland，强制稳定 Xorg
    gdm_cmd = """
sudo sed -i 's/^#WaylandEnable=false/WaylandEnable=false/' /etc/gdm3/custom.conf 2>/dev/null || true
sudo sed -i 's/^#WaylandEnable=false/WaylandEnable=false/' /etc/gdm/custom.conf 2>/dev/null || true
"""
    exec_remote(client, gdm_cmd, desc=f"[{node_name}] 配置 GDM 禁用 Wayland 强制 Xorg")

    # 7. 启动并启用 xrdp 服务
    start_xrdp = """
sudo systemctl daemon-reload
sudo systemctl enable xrdp
sudo systemctl restart xrdp
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow 3389/tcp
fi
echo "=== 3389 监听状态 ==="
ss -tlnp | grep 3389
echo "=== xrdp 服务状态 ==="
systemctl is-active xrdp
"""
    exec_remote(client, start_xrdp, desc=f"[{node_name}] 启动并验证 xrdp 运行状态")
    logger.info(f"[{node_name}] 远程桌面 (xrdp) 部署成功！")


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(root, ".env"))

    node0_ip = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    node0_user = os.getenv("SSH_NODE0_USER", "winbot")
    node0_pass = os.getenv("SSH_NODE0_PASS", "winbot")

    node1_ip = "172.19.49.159"
    node1_fabric_ip = os.getenv("NODE1_FABRIC_IP", "192.168.100.11")
    node1_user = os.getenv("SSH_OPS_USER", "dgxdeploy")

    logger.info(f"正在连接 Node0 工作站 ({node0_user}@{node0_ip})...")
    c0 = get_ssh_client(node0_ip, node0_user, node0_pass)

    try:
        # 1. 部署 Node0
        deploy_node(c0, f"Node0 工作站 ({node0_ip})", ["winbot"])

        # 2. 部署 Node1
        logger.info(f"正在建立与 Node1 ({node1_user}@{node1_ip}) 的直接 SSH 连接...")
        # 此时 Node1 已切回 172.19.49.159，直接连 172.19.49.159 或经 fabric 直连
        c1 = get_ssh_client(node1_ip, node1_user, "winning")
        try:
            deploy_node(c1, f"Node1 工作站 ({node1_ip})", ["dgxdeploy", "his_test"])
        finally:
            c1.close()

    finally:
        c0.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
