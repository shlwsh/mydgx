#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双工作站 GNOME 远程桌面 (RDP) 一键配置、诊断与快速连接生成工具
基于 Ubuntu 24.04 LTS 原生 gnome-remote-desktop 系统级远程登录服务 (Remote Login)。

功能特性:
1. 双工作站 (Node0: 172.19.50.70 / Node1: 172.19.49.159) RDP 服务自动化部署;
2. 自签名 TLS 证书自动生成与权限配置;
3. 系统级 gnome-remote-desktop 守护进程配置与开机自启;
4. 用户密码与 PAM 认证安全同步;
5. 防火墙与 3389 端口放行检测;
6. 本机网络端口连通性验证;
7. 自动生成 Windows 原生一键连接文件 (.rdp)。

用法:
    python scripts/setup_rdp.py              # 配置所有工作站并生成 .rdp 快捷方式
    python scripts/setup_rdp.py --status     # 仅巡检各节点远程桌面服务与端口状态
    python scripts/setup_rdp.py --target node0  # 仅配置工作站 Node0
    python scripts/setup_rdp.py --target node1  # 仅配置工作站 Node1
"""

import argparse
import os
import socket
import sys
import logging
from dotenv import load_dotenv
import paramiko

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("SetupRDP")


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


def test_tcp_port(ip, port=3389, timeout=3):
    """测试本地到目标主机的 TCP 端口连通性。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


def generate_rdp_file(filepath, host_ip, username):
    """生成 Windows 原生 RDP 配置文件。"""
    content = (
        f"full address:s:{host_ip}:3389\r\n"
        f"username:s:{username}\r\n"
        "prompt for credentials:i:1\r\n"
        "administrative session:i:1\r\n"
        "screen mode id:i:2\r\n"
        "use multimon:i:0\r\n"
        "desktopwidth:i:1920\r\n"
        "desktopheight:i:1080\r\n"
        "session bpp:i:32\r\n"
        "compression:i:1\r\n"
        "keyboardhook:i:2\r\n"
        "audiocapturemode:i:0\r\n"
        "videoplaybackmode:i:1\r\n"
        "connection type:i:7\r\n"
        "networkautodetect:i:1\r\n"
        "bandwidthautodetect:i:1\r\n"
        "displayconnectionbar:i:1\r\n"
        "enableworkspacereconnect:i:0\r\n"
        "disable wallpaper:i:0\r\n"
        "allow font smoothing:i:1\r\n"
        "allow desktop composition:i:1\r\n"
        "disable full window drag:i:0\r\n"
        "disable menu anims:i:0\r\n"
        "disable themes:i:0\r\n"
        "disable cursor setting:i:0\r\n"
        "bitmapcachepersistenable:i:1\r\n"
        "authentication level:i:2\r\n"
        "negotiate security layer:i:1\r\n"
    )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"已生成 Windows 远程桌面连接文件: {filepath}")


def setup_node0(c0, node_ip):
    logger.info("=" * 65)
    logger.info(f"开始配置 Node0 工作站 ({node_ip}) 远程桌面服务...")
    logger.info("=" * 65)

    cert_dir = "/etc/gnome-remote-desktop"
    cert_file = f"{cert_dir}/rdp-tls.crt"
    key_file = f"{cert_dir}/rdp-tls.key"

    script = f"""
set -e
sudo mkdir -p {cert_dir}
if [ ! -f {cert_file} ] || [ ! -f {key_file} ]; then
    echo "生成自签名 TLS 证书..."
    sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 -keyout {key_file} -out {cert_file} -subj "/CN=cube-f22b"
    sudo chown -R gnome-remote-desktop:gnome-remote-desktop {cert_dir}
    sudo chmod 600 {key_file}
    sudo chmod 644 {cert_file}
fi
sudo grdctl --system rdp set-tls-cert {cert_file}
sudo grdctl --system rdp set-tls-key {key_file}
sudo grdctl --system rdp set-credentials winbot winbot
sudo grdctl --system rdp enable
sudo systemctl daemon-reload
sudo systemctl enable gnome-remote-desktop.service
sudo systemctl restart gnome-remote-desktop.service
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow 3389/tcp
fi
echo "=== 3389 监听状态 ==="
ss -tlnp | grep 3389 || echo "警告: 未监听到 3389 端口"
echo "=== 服务配置状态 ==="
sudo grdctl --system status
"""
    exec_remote(c0, script, desc="在 Node0 配置 GNOME 系统级远程桌面")


def setup_node1(c0, node1_user, node1_fabric_ip):
    logger.info("=" * 65)
    logger.info(f"开始通过 fabric 跳板配置 Node1 工作站 ({node1_fabric_ip}) 远程桌面服务...")
    logger.info("=" * 65)

    cert_dir = "/etc/gnome-remote-desktop"
    cert_file = f"{cert_dir}/rdp-tls.crt"
    key_file = f"{cert_dir}/rdp-tls.key"

    node1_script = f"""
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new {node1_user}@{node1_fabric_ip} 'bash -s' << 'EOF'
set -e
echo "确保 dgxdeploy 密码同步..."
echo 'dgxdeploy:winning' | sudo chpasswd
sudo mkdir -p {cert_dir}
if [ ! -f {cert_file} ] || [ ! -f {key_file} ]; then
    echo "生成自签名 TLS 证书..."
    sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 -keyout {key_file} -out {cert_file} -subj "/CN=cube-0137"
    sudo chown -R gnome-remote-desktop:gnome-remote-desktop {cert_dir}
    sudo chmod 600 {key_file}
    sudo chmod 644 {cert_file}
fi
sudo grdctl --system rdp set-tls-cert {cert_file}
sudo grdctl --system rdp set-tls-key {key_file}
sudo grdctl --system rdp set-credentials dgxdeploy winning
sudo grdctl --system rdp enable
sudo systemctl daemon-reload
sudo systemctl enable gnome-remote-desktop.service
sudo systemctl restart gnome-remote-desktop.service
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow 3389/tcp
fi
echo "=== 3389 监听状态 ==="
ss -tlnp | grep 3389 || echo "警告: 未监听到 3389 端口"
echo "=== 服务配置状态 ==="
sudo grdctl --system status
EOF
"""
    exec_remote(c0, node1_script, desc="在 Node1 配置 GNOME 系统级远程桌面与密码认证")


def check_status(c0, node0_ip, node1_ip, node1_fabric_ip, node1_user):
    logger.info("=" * 65)
    logger.info("双工作站远程桌面 (xrdp) 状态巡检")
    logger.info("=" * 65)

    # 1. Node0 本地服务与端口检查
    exec_remote(
        c0,
        "echo '[Node0 远端状态]'; ss -tlnp | grep 3389 || echo '未监听 3389'; "
        "echo 'xrdp 服务: ' $(systemctl is-active xrdp) '(自启: ' $(systemctl is-enabled xrdp) ')'; "
        "uptime",
        desc="检查 Node0 状态",
        check=False,
    )

    # 2. Node1 远端服务与端口检查
    node1_check = (
        f"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new {node1_user}@{node1_ip} "
        "'echo \"[Node1 远端状态]\"; ss -tlnp | grep 3389 || echo \"未监听 3389\"; "
        "echo \"xrdp 服务: \" $(systemctl is-active xrdp) \"(自启: \" $(systemctl is-enabled xrdp) \")\"; "
        "uptime'"
    )
    exec_remote(c0, node1_check, desc="检查 Node1 状态", check=False)

    # 3. 本机 Windows TCP 端口实测
    logger.info("-" * 65)
    logger.info(">>> 本机网络直连 3389 端口测试:")
    ok0 = test_tcp_port(node0_ip, 3389)
    logger.info(f"本机 -> Node0 ({node0_ip}:3389) RDP 端口连通: {'[成功 PASS]' if ok0 else '[失败 FAIL]'}")
    ok1 = test_tcp_port(node1_ip, 3389)
    logger.info(f"本机 -> Node1 ({node1_ip}:3389) RDP 端口连通: {'[成功 PASS]' if ok1 else '[失败 FAIL]'}")
    logger.info("-" * 65)


def main():
    parser = argparse.ArgumentParser(description="双工作站 GNOME 远程桌面配置与快速连接工具")
    parser.add_argument(
        "--target",
        choices=["all", "node0", "node1"],
        default="all",
        help="目标节点: node0, node1, all (默认全部)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="仅检查远程桌面服务与端口状态",
    )
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(root, ".env"))

    node0_ip = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    node0_user = os.getenv("SSH_NODE0_USER", "winbot")
    node0_pass = os.getenv("SSH_NODE0_PASS", "winbot")

    node1_ip = os.getenv("NODE1_MGMT_IP", "172.19.49.159")
    node1_fabric_ip = os.getenv("NODE1_FABRIC_IP", "192.168.100.11")
    node1_user = os.getenv("SSH_OPS_USER", "dgxdeploy")

    logger.info(f"正在建立与 Node0 工作站 ({node0_user}@{node0_ip}) 的连接...")
    c0 = get_ssh_client(node0_ip, node0_user, node0_pass)

    try:
        if args.status:
            check_status(c0, node0_ip, node1_ip, node1_fabric_ip, node1_user)
            return 0

        # 执行配置
        if args.target in ["all", "node0"]:
            setup_node0(c0, node0_ip)
        if args.target in ["all", "node1"]:
            setup_node1(c0, node1_user, node1_fabric_ip)

        # 检查配置后状态
        check_status(c0, node0_ip, node1_ip, node1_fabric_ip, node1_user)

        # 生成 Windows .rdp 快捷连接文件
        scripts_dir = os.path.join(root, "scripts")
        rdp0_path = os.path.join(scripts_dir, "工作站_Node0_172.19.50.70.rdp")
        rdp1_path = os.path.join(scripts_dir, "工作站_Node1_172.19.49.159.rdp")
        generate_rdp_file(rdp0_path, node0_ip, "winbot")
        generate_rdp_file(rdp1_path, node1_ip, "dgxdeploy")

        logger.info("=" * 65)
        logger.info("双工作站远程桌面配置全部完成！")
        logger.info(f"Node0 远程桌面: {node0_ip}:3389 (用户: winbot / 密码: {node0_pass})")
        logger.info(f"Node1 远程桌面: {node1_ip}:3389 (用户: dgxdeploy 或 his_test / 密码: winning)")
        logger.info(f"可在 Windows 资源管理器中双击以下文件快速连接:")
        logger.info(f"  1. {rdp0_path}")
        logger.info(f"  2. {rdp1_path}")
        logger.info("=" * 65)

    finally:
        c0.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
