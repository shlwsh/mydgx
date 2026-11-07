#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/rdp_session_reset.py - 三工作站远程桌面会话重置工具

背景：
    xrdp 会话在客户端异常断开（SIGTERM / 网络中断 / 窗口直接关闭）后，
    服务端的 GNOME 会话可能残留为「僵死」状态。此时重新连接会命中
    `reconnected session`（复用旧显示号），表现为登录后黑屏。

    本脚本用于在出现黑屏 / 卡死时，远程清理残留会话，强制下次连接重建全新桌面。

功能：
    1. 巡检远端 xrdp 会话状态（显示号、窗口管理器、gnome-shell 是否存活）；
    2. 检测到僵死会话时，安全终止该会话的全部进程与 X11 socket；
    3. 支持仅巡检（--check）与强制重置（默认行为）。

用法:
    python scripts/rdp_session_reset.py --node all --check   # 仅巡检
    python scripts/rdp_session_reset.py --node node0          # 重置 Node0 残留会话
    python scripts/rdp_session_reset.py --node all            # 重置双机
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from env_helper import ROOT_DIR, setup_logger, init_env
except ImportError:
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def setup_logger(name="RDP-Session-Reset", level=20):
        import logging

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        return logging.getLogger(name)

    def init_env():
        return os.path.join(ROOT_DIR, ".env")


logger = setup_logger("RDP-Session-Reset")

NODES = {
    "node0": {
        "label": "工作站 Node0",
        "ip_env": "NODE0_MGMT_IP",
        "ip_default": "172.19.50.70",
        "ssh_user_env": "SSH_NODE0_USER",
        "ssh_user_default": "winbot",
        "ssh_pass_env": "SSH_NODE0_PASS",
        "fabric_ip_env": "NODE0_FABRIC_IP",
        "fabric_ip_default": "192.168.100.10",
    },
    "node1": {
        "label": "工作站 Node1",
        "ip_env": "NODE1_MGMT_IP",
        "ip_default": "172.19.49.159",
        "ssh_user_env": "SSH_OPS_USER",
        "ssh_user_default": "dgxdeploy",
        "ssh_pass_env": "SSH_OPS_PASS",
        "fabric_ip_env": "NODE1_FABRIC_IP",
        "fabric_ip_default": "192.168.100.11",
    },
    "node123": {
        "label": "工作站 Node123",
        "ip_env": "NODE123_MGMT_IP",
        "ip_default": "172.19.51.123",
        "ssh_user_env": "SSH_NODE123_USER",
        "ssh_user_default": "bot",
        "ssh_pass_env": "SSH_NODE123_PASS",
    },
}

# 巡检命令：列出当前 xrdp 会话与关键进程
INSPECT_CMD = (
    "echo 'SOCKETS:'; ls /tmp/.X11-unix/ 2>/dev/null | tr '\\n' ' '; echo; "
    "echo 'PROCS:'; ps -eo pid,user,etimes,comm 2>/dev/null "
    "| grep -E '(^| )(Xorg|gnome-shell|xrdp-sesman|xrdp)' | grep -v grep; "
    "echo 'SUDO:'; id -u"
)


def node_fabric_ip(node: dict) -> str:
    """读取节点的高速互联口地址（可能为空表示未配置）。"""
    env_key = node.get("fabric_ip_env")
    if not env_key:
        return ""
    return os.getenv(env_key, node.get("fabric_ip_default", "")).strip()


def test_ssh_port(ip: str, timeout: int = 5) -> bool:
    """检测目标 IP 的 22 端口是否为真实 SSH 服务。

    单纯的 TCP 连接检测在 Clash TUN 模式下不可靠（所有被代理接管的连接
    都会假性建立成功），因此读取 SSH 协议 banner 做校验。
    """
    import socket

    try:
        with socket.create_connection((ip, 22), timeout=timeout) as sock:
            sock.settimeout(timeout)
            banner = sock.recv(64)
            return banner.startswith(b"SSH-")
    except OSError:
        return False


def resolve_node_addr(node: dict) -> tuple:
    """解析节点可用地址：管理网优先，不可达时回退高速互联口。

    高速互联口（192.168.100.0/24）仅在 Node0↔Node1 之间物理直连，
    普通管理机并无该网卡，因此直连高速口通常不可达——此时需要借道 Node0
    跳板建立 SSH 隧道才能触达。此处仅做地址选择，隧道由调用方按需建立。

    返回 (地址, 来源描述)。
    """
    mgmt_ip = os.getenv(node["ip_env"], node["ip_default"]).strip()
    if mgmt_ip and test_ssh_port(mgmt_ip):
        return mgmt_ip, "管理网"

    fabric_ip = node_fabric_ip(node)
    if fabric_ip:
        logger.warning(f"管理网地址 {mgmt_ip} 不可达，回退到高速互联口 {fabric_ip}")
        return fabric_ip, "高速互联口(需跳板)"

    return mgmt_ip, "管理网(不可达)"


def get_ssh(ip: str, user: str, pwd: str, jump=None):
    """建立 SSH 连接。

    传入 jump（一个已连接的 paramiko SSHClient）时，通过该跳板建立 direct-tcpip
    隧道连接目标地址，用于触达仅在节点间物理直连的高速互联口。
    """
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if jump is not None:
        channel = jump.get_transport().open_channel(
            "direct-tcpip", (ip, 22), ("127.0.0.1", 0)
        )
        client.connect(hostname=ip, port=22, username=user, password=pwd or None,
                       sock=channel, timeout=15)
    else:
        client.connect(hostname=ip, port=22, username=user, password=pwd or None, timeout=15)
    return client


def open_jump_tunnel(node: dict):
    """尝试经 Node0 跳板建立到目标高速口的隧道。

    返回 (jump_client, target_ssh_client) 或 (None, None)。
    """
    import paramiko

    jump_ip = os.getenv("NODE0_MGMT_IP", "172.19.50.70").strip()
    jump_user = os.getenv("SSH_NODE0_USER", "winbot").strip()
    jump_pass = os.getenv("SSH_NODE0_PASS", "").strip()
    target_ip = node_fabric_ip(node)
    target_user = os.getenv(node["ssh_user_env"], node["ssh_user_default"]).strip()
    target_pass = os.getenv(node["ssh_pass_env"], "").strip()

    if not target_ip:
        return None, None

    jump = paramiko.SSHClient()
    jump.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        jump.connect(hostname=jump_ip, port=22, username=jump_user,
                     password=jump_pass or None, timeout=15)
        target = get_ssh(target_ip, target_user, target_pass, jump=jump)
        return jump, target
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"经 Node0 跳板连接高速口 {target_ip} 失败: {exc}")
        try:
            jump.close()
        except Exception:  # noqa: BLE001
            pass
        return None, None


def exec_sudo(client, cmd: str, sudo_pass: str, timeout: int = 60) -> tuple:
    """以 sudo 权限执行命令（sudo 口令与登录口令一致时复用）。"""
    if sudo_pass:
        wrapped = f"echo {sudo_pass} | sudo -S bash -c {_shq(cmd)} 2>&1"
    else:
        wrapped = cmd
    _, stdout, _ = client.exec_command(wrapped, timeout=timeout)
    out = stdout.read().decode("utf-8", "ignore")
    code = stdout.channel.recv_exit_status()
    return code, out


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def inspect(client) -> str:
    _, out = exec_sudo(client, INSPECT_CMD, "")
    return out


def is_stale(out: str) -> bool:
    """判断是否存在僵死会话：有 X11 socket 但缺少 gnome-shell 进程。"""
    sockets = ""
    has_shell = False
    for line in out.splitlines():
        if line.startswith("SOCKETS:"):
            sockets = line.split(":", 1)[1].strip()
        if "gnome-shell" in line:
            has_shell = True
    return bool(sockets) and not has_shell


def reset(client, sudo_pass: str) -> None:
    """终止残留会话进程并清理 X11 socket。"""
    cmd = (
        "pkill -u $USER -f gnome-shell 2>/dev/null; "
        "pkill -u $USER Xorg 2>/dev/null; "
        "pkill -u $USER -f xrdp-sesman 2>/dev/null; "
        "sleep 2; "
        "rm -f /tmp/.X11-unix/X* 2>/dev/null; "
        "echo 'RESET_DONE'"
    )
    code, out = exec_sudo(client, cmd, sudo_pass)
    if "RESET_DONE" in out:
        logger.info("残留会话已清理，X11 socket 已释放")
    else:
        logger.warning(f"清理命令返回码={code}，输出: {out.strip()[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="三工作站远程桌面会话重置工具")
    parser.add_argument("--node", choices=["all", "node0", "node1", "node123"], default="all",
                        help="目标节点: node0 / node1 / node123 / all (默认 all)")
    parser.add_argument("--check", action="store_true", help="仅巡检会话状态，不做任何变更")
    args = parser.parse_args()

    init_env()
    targets = list(NODES.keys()) if args.node == "all" else [args.node]

    for key in targets:
        node = NODES[key]
        ssh_user = os.getenv(node["ssh_user_env"], node["ssh_user_default"]).strip()
        ssh_pass = os.getenv(node["ssh_pass_env"], "").strip()
        ip, addr_src = resolve_node_addr(node)

        logger.info("=" * 60)
        logger.info(f"{node['label']} ({ip}) 会话巡检 [{addr_src}]")
        logger.info("=" * 60)

        try:
            client = get_ssh(ip, ssh_user, ssh_pass)
            jump = None
        except Exception as exc:  # noqa: BLE001
            # 管理网直连失败：尝试经 Node0 跳板走高速互联口
            jump, client = open_jump_tunnel(node)
            if client is None:
                logger.error(f"SSH 连接失败: {exc}")
                continue
            logger.info(f"已通过 Node0 跳板建立到 {ip} 的隧道")
            ssh_pass = os.getenv(node["ssh_pass_env"], "").strip()

        try:
            out = inspect(client)
            for line in out.splitlines():
                if line.strip():
                    logger.info("  " + line.strip())

            stale = is_stale(out)
            if not stale:
                logger.info("[结论] 未发现僵死会话，桌面状态正常")
                continue

            logger.warning("[结论] 检测到僵死会话（存在 X11 socket 但 gnome-shell 已退出）")
            if args.check:
                logger.info("如需强制重置，去掉 --check 参数重新执行")
                continue

            reset(client, ssh_pass)
            logger.info("[完成] 下次连接将重建全新桌面会话")
        finally:
            client.close()
            if jump is not None:
                try:
                    jump.close()
                except Exception:  # noqa: BLE001
                    pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
