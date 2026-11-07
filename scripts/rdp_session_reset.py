#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/rdp_session_reset.py - 双工作站远程桌面会话重置工具

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
    },
    "node1": {
        "label": "工作站 Node1",
        "ip_env": "NODE1_MGMT_IP",
        "ip_default": "172.19.49.159",
        "ssh_user_env": "SSH_OPS_USER",
        "ssh_user_default": "dgxdeploy",
        "ssh_pass_env": "SSH_OPS_PASS",
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


def get_ssh(ip: str, user: str, pwd: str):
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, port=22, username=user, password=pwd or None, timeout=15)
    return client


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
    parser = argparse.ArgumentParser(description="双工作站远程桌面会话重置工具")
    parser.add_argument("--node", choices=["all", "node0", "node1", "node123"], default="all",
                        help="目标节点: node0 / node1 / node123 / all (默认 all)")
    parser.add_argument("--check", action="store_true", help="仅巡检会话状态，不做任何变更")
    args = parser.parse_args()

    init_env()
    targets = list(NODES.keys()) if args.node == "all" else [args.node]

    for key in targets:
        node = NODES[key]
        ip = os.getenv(node["ip_env"], node["ip_default"]).strip()
        ssh_user = os.getenv(node["ssh_user_env"], node["ssh_user_default"]).strip()
        ssh_pass = os.getenv(node["ssh_pass_env"], "").strip()

        logger.info("=" * 60)
        logger.info(f"{node['label']} ({ip}) 会话巡检")
        logger.info("=" * 60)

        try:
            client = get_ssh(ip, ssh_user, ssh_pass)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"SSH 连接失败: {exc}")
            continue

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
