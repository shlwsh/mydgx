#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/diagnose_network.py - 70工作站网络连接与激活状态诊断脚本
"""

import os
import sys

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

import paramiko

logger = setup_logger("DiagnoseNet")


def main():
    init_env()
    host = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user = os.getenv("SSH_NODE0_USER", "winbot")
    password = os.getenv("SSH_NODE0_PASS", "winbot")

    logger.info(f"正在连接 Node0 ({user}@{host})...")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, 22, user, password, timeout=10)

    commands = [
        ("列出 netplan 目录", "ls -la /etc/netplan/"),
        ("查看 netplan 文件内容", "cat /etc/netplan/*.yaml /etc/netplan/*.yml 2>/dev/null || true"),
        ("查看所有连接的 autoconnect 与类型", "nmcli -f NAME,UUID,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,DEVICE connection show"),
        ("查看有线连接 2 详细属性", "nmcli connection show '有线连接 2' | grep -E 'connection.autoconnect|ipv4.method|ipv6.method|connection.interface-name'"),
        ("检查 enP2p1s0f1np1 链路状态", "ethtool enP2p1s0f1np1 2>/dev/null | grep -E 'Speed|Duplex|Link detected' || true"),
        ("检查物理网卡列表与驱动", "lspci | grep -i ether"),
        ("检查 NetworkManager 配置与连通性检查 (connectivity)", "cat /etc/NetworkManager/NetworkManager.conf; cat /etc/NetworkManager/conf.d/* 2>/dev/null || true; cat /usr/lib/NetworkManager/conf.d/* 2>/dev/null || true"),
        ("检查最近 5 分钟 NetworkManager 告警日志", "journalctl -u NetworkManager --since '5 minutes ago' --no-pager | grep -iE 'fail|warn|error' | tail -n 25 || true"),
    ]

    try:
        for title, cmd in commands:
            logger.info(f"=== {title} ===")
            stdin, stdout, stderr = c.exec_command(cmd)
            out = stdout.read().decode("utf-8").strip()
            err = stderr.read().decode("utf-8").strip()
            if out:
                print(out)
            if err:
                print(f"[stderr] {err}")
            print()
    finally:
        c.close()


if __name__ == "__main__":
    main()
