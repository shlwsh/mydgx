#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""经由 Node0 跳板探测 Node1(159) 是否存活：ping/ARP/Tailscale/3389扫描。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env
import paramiko

logger = setup_logger("Probe-159-via-70")

def main():
    init_env()
    host = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user = os.getenv("SSH_NODE0_USER", "winbot")
    pwd = os.getenv("SSH_NODE0_PASS", "")
    logger.info("正在连接 Node0 跳板 %s@%s ...", user, host)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, 22, user, pwd, timeout=10)
    cmds = [
        "ping -c3 -W2 172.19.49.159; echo RC=$?",
        "ip neigh show 172.19.49.159 || true",
        "tailscale status 2>/dev/null | grep -iE '0137|cube' || echo TS-NO-ENTRY",
        "for i in 21 46 50 129 159 160 238; do timeout 2 bash -c \"echo > /dev/tcp/172.19.49.$i/3389\" 2>/dev/null && echo 172.19.49.$i:3389-OPEN || echo 172.19.49.$i:3389-CLOSED; done",
        "arping -c2 -w3 -I $(ip route get 172.19.49.159 | grep -oP 'dev \\K\\S+' | head -1) 172.19.49.159 2>&1 | tail -5 || true",
    ]
    for cmd in cmds:
        logger.info("=== %s ===", cmd[:60])
        _, stdout, _ = c.exec_command(cmd, timeout=40)
        print(stdout.read().decode("utf-8", "ignore").strip())
        print()
    c.close()

if __name__ == "__main__":
    main()
