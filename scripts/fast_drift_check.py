#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快速版：广播ping填充ARP，定位 cube-0137 MAC 是否还在线。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env
import paramiko
logger = setup_logger("Fast-Drift")
def run(c, cmd, t=60):
    _, o, _ = c.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "ignore").strip()
def main():
    init_env()
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(os.getenv("NODE0_MGMT_IP","172.19.50.70"),22,os.getenv("SSH_NODE0_USER","winbot"),os.getenv("SSH_NODE0_PASS"),timeout=10)
    # Node1 有线网卡 MAC（harden/基线时应已知，先抓历史ARP无则跳过）
    print(run(c, "ip neigh show | grep -iE '49\\.|21\\.' | head -n 40", t=30))
    print(run(c, "ping -b -c2 -W1 172.19.49.255 >/dev/null 2>&1; ping -b -c2 -W1 172.19.51.255 >/dev/null 2>&1; sleep 2; ip neigh show | grep -iv FAILED | head -n 60", t=60))
    c.close()
if __name__ == "__main__":
    main()
