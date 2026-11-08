#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/verify_all_ts_nodes.py - 三节点 Tailscale 直连与远程桌面延迟综合验收脚本
"""

import os
import sys
import time
import socket
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

logger = setup_logger("VerifyAllNodes")

NODES = [
    {"name": "Node0 (cube-f22b)", "ip": "100.67.107.19", "port": 41641},
    {"name": "Node1 (cube-0137)", "ip": "100.103.250.108", "port": 41642},
    {"name": "Node123 (cube-fe5e)", "ip": "100.79.120.65", "port": 41643},
]


def test_rdp_tcp(ip, port=3389):
    t0 = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect((ip, port))
        dt = (time.time() - t0) * 1000
        s.close()
        return dt, True
    except Exception as e:
        return 0, False


def test_tailscale_direct(ip):
    try:
        out = subprocess.check_output(["tailscale", "status"]).decode()
        for line in out.splitlines():
            if ip in line:
                return line.strip()
    except Exception:
        pass
    return "未知"


def test_icmp_ping(ip, count=4):
    try:
        res = subprocess.run(["ping", "-c", str(count), "-W", "1500", ip], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if "round-trip" in line:
                # round-trip min/avg/max/stddev = 11.154/13.013/14.417/1.065 ms
                parts = line.split("=")[-1].strip().split("/")
                return f"avg {parts[1]}ms (min {parts[0]}/max {parts[2]}ms)"
    except Exception:
        pass
    return "超时"


def test_ts_ping(ip):
    try:
        res = subprocess.run(["tailscale", "ping", "--c", "2", "--timeout", "2s", ip], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if "pong from" in line:
                return line.strip()
    except Exception:
        pass
    return "握手失败"


def main():
    init_env()
    logger.info("=========================================================================================")
    logger.info("                       工作站三节点 Tailscale 直连综合验收测试                             ")
    logger.info("=========================================================================================")

    print(f"{'节点名称':<20} | {'Tailscale IP':<15} | {'连接拓扑':<32} | {'ICMP延迟':<24} | {'RDP(3389)握手':<12}")
    print("-" * 115)

    for n in NODES:
        name = n["name"]
        ip = n["ip"]
        topo = test_tailscale_direct(ip)
        mode = "Direct 59.49.101.30:" + str(n["port"]) if "direct" in topo else ("Relay" if "relay" in topo else "Idle")
        icmp = test_icmp_ping(ip, 4)
        rdp_lat, rdp_ok = test_rdp_tcp(ip, 3389)
        rdp_res = f"{rdp_lat:.2f} ms" if rdp_ok else "不可达"

        print(f"{name:<20} | {ip:<15} | {mode:<32} | {icmp:<24} | {rdp_res:<12}")

    print("=" * 115)


if __name__ == "__main__":
    main()

