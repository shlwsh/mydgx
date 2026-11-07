#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深入诊断 172.19.50.70 (Node0) 和 172.19.49.159 (Node1) 的 RDP 缓慢、卡顿与连接失败原因
"""

import paramiko
from env_helper import init_env

init_env()

NODES = [
    {"host": "172.19.50.70", "user": "winbot", "pwd": "winbot"},
    {"host": "172.19.49.159", "user": "dgxdeploy", "pwd": "winning"}
]

for node in NODES:
    host = node["host"]
    user = node["user"]
    pwd = node["pwd"]
    print(f"\n==================== HOST: {host} ====================")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(host, username=user, password=pwd, timeout=8)
    except Exception as e:
        print(f"SSH 连接失败: {e}")
        continue
        
    script = """
    echo "=== 1. 最近 xrdp 错误与连接日志 (tail 40) ==="
    sudo tail -n 40 /var/log/xrdp.log 2>/dev/null || true
    
    echo "=== 2. 最近 xrdp-sesman 日志 (tail 40) ==="
    sudo tail -n 40 /var/log/xrdp-sesman.log 2>/dev/null || true
    
    echo "=== 3. 当前运行的 X11 / xrdp 会话进程 ==="
    ps aux | grep -iE "(xrdp|Xorg|Xvnc|gnome-session|dbus-daemon)" | grep -v grep | head -n 30
    
    echo "=== 4. 监听端口状态 ==="
    ss -tlnp | grep -E "(3389|3350|xrdp)"
    
    echo "=== 5. 系统负载与内存 ==="
    uptime
    free -h
    
    echo "=== 6. xrdp.ini 关键参数 ==="
    grep -iE "(crypt_level|tcp_send|tcp_recv|max_bpp|use_compression|ssl_protocols|channel_code)" /etc/xrdp/xrdp.ini 2>/dev/null || true
    
    echo "=== 7. sesman.ini 关键参数 ==="
    grep -iE "(KillDisconnected|DisconnectedTimeLimit|IdleTimeLimit|Policy)" /etc/xrdp/sesman.ini 2>/dev/null || true
    """
    
    stdin, stdout, stderr = c.exec_command(script)
    print(stdout.read().decode('utf-8', errors='replace'))
    c.close()
