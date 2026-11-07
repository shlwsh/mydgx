#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""经由 Node0 跳板识别 172.19.49.50 身份（是否为漂移后的 Node1）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env
import paramiko
logger = setup_logger("Probe-50")
def run(c, cmd, t=20):
    _, o, _ = c.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "ignore").strip()
def main():
    init_env()
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(os.getenv("NODE0_MGMT_IP","172.19.50.70"),22,os.getenv("SSH_NODE0_USER","winbot"),os.getenv("SSH_NODE0_PASS"),timeout=10)
    for cmd in [
        "ping -c2 -W2 172.19.49.50; ip neigh show 172.19.49.50",
        "timeout 5 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=4 dgxdeploy@172.19.49.50 'hostname; cat /etc/hostname; ip -br addr' 2>&1 || echo SSH50-FAIL",
        "getent hosts 172.19.49.50 || true; timeout 5 curl -sk https://172.19.49.50:3389/ -o /dev/null -w '%{http_code}\n' 2>&1 || true",
    ]:
        print("=== "+cmd[:70]); print(run(c, cmd)); print()
    c.close()
if __name__ == "__main__":
    main()
