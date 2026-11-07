#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""经由 Node0 跳板扫描 Node1 是否漂移到其他网段（含访客网）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env
import paramiko
logger = setup_logger("Scan-Drift")
def run(c, cmd, t=90):
    _, o, _ = c.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "ignore").strip()
def main():
    init_env()
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(os.getenv("NODE0_MGMT_IP","172.19.50.70"),22,os.getenv("SSH_NODE0_USER","winbot"),os.getenv("SSH_NODE0_PASS"),timeout=10)
    ops_user = os.getenv("SSH_OPS_USER","dgxdeploy"); ops_pass = os.getenv("SSH_OPS_PASS","")
    # 候选网段：管理网各子段 + 历史访客网
    cmd = (
        "for net in '172.19.49' '172.19.50' '172.19.51' '172.19.21'; do "
        "echo \"== SWEEP $net.0/24 ==\"; "
        "for i in $(seq 1 254); do (timeout 1 bash -c \"echo > /dev/tcp/$net.$i/22\" 2>/dev/null && echo \"$net.$i:22-OPEN\") & done; wait; "
        "done; echo DONE-SWEEP"
    )
    print(run(c, cmd, t=120))
    print(run(c, "ip neigh show | head -n 60", t=20))
    # 对扫到的 22-OPEN 主机尝试识别是否为 cube-0137（hostname 探测，需口令时快速失败）
    print(run(c, f"for ip in $(ip neigh show | grep -oP '172\\.19\\.\\d+\\.\\d+'); do timeout 4 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=3 {ops_user}@$ip 'hostname; ip -br addr | head -5' 2>&1 | head -8 | sed \"s/^/[$ip] /\"; done; echo DONE-IDENT", t=150))
    c.close()
if __name__ == "__main__":
    main()
