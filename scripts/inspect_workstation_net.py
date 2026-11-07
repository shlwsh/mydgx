#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()

for host, user, pwd in [("172.19.50.70", "winbot", "winbot"), ("172.19.49.159", "dgxdeploy", "winning")]:
    print(f"\n==================== HOST: {host} ====================")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=pwd)
    
    script = """
    echo "=== 网络接口与 IP ==="
    ip -br addr
    
    echo "=== 路由表 ==="
    ip route
    
    echo "=== xrdp 服务进程详细资源消耗 ==="
    ps -eo pid,user,pcpu,pmem,args | grep -iE "(xrdp|Xorg)" | grep -v grep
    
    echo "=== /etc/X11/xrdp/xorg.conf 配置 ==="
    cat /etc/X11/xrdp/xorg.conf 2>/dev/null || cat /etc/xrdp/xorg.conf 2>/dev/null || true
    """
    stdin, stdout, stderr = c.exec_command(script)
    print(stdout.read().decode('utf-8', errors='replace'))
    c.close()
