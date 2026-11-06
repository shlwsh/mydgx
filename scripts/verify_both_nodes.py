#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在 Node0 和 Node1 上安装 xvfb 与 scrot，并测试启动 flclash-launcher 截图验证
"""

import os
import paramiko
from env_helper import init_env

init_env()

for host, user, pwd in [("172.19.50.70", "winbot", "winbot"), ("172.19.49.159", "dgxdeploy", "winning")]:
    print(f"\n==================== HOST: {host} ====================")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=pwd)
    
    # 安装必要工具
    print(f"[{host}] 安装 xvfb, scrot, x11-utils...")
    c.exec_command("sudo apt-get update -qq && sudo apt-get install -y -qq xvfb scrot x11-utils")
    
    # 模拟以普通用户执行 flclash-launcher 并截图
    script = f"""#!/bin/bash
    sudo rm -f /tmp/fl_verify_{user}.*
    
    xvfb-run -s "-screen 0 1024x768x24" dbus-run-session bash -c '
        /usr/local/bin/flclash-launcher > /tmp/fl_verify_{user}.log 2>&1 &
        PID=$!
        echo "FlClash PID: $PID"
        sleep 6
        
        WIN_ID=$(xwininfo -root -tree | grep -iE "flclash" | head -n 1 | awk "{{print \\$1}}")
        echo "Window ID: $WIN_ID"
        if [ -n "$WIN_ID" ]; then
            scrot -u -b /tmp/fl_verify_{user}.png || scrot /tmp/fl_verify_{user}.png
        else
            scrot /tmp/fl_verify_{user}.png
        fi
        kill -9 $PID 2>/dev/null || true
    '
    ls -lh /tmp/fl_verify_{user}.png
    cat /tmp/fl_verify_{user}.log | head -n 20
    """
    
    sftp = c.open_sftp()
    with sftp.file(f"/tmp/test_verify_{user}.sh", "w") as f:
        f.write(script)
    sftp.close()
    
    stdin, stdout, stderr = c.exec_command(f"bash /tmp/test_verify_{user}.sh", get_pty=True)
    out = stdout.read().decode('utf-8', errors='replace')
    print(f"[{host}] 运行结果:\n{out}")
    
    local_img = os.path.join(os.path.dirname(__file__), f"flclash_final_{host.replace('.', '_')}.png")
    try:
        sftp = c.open_sftp()
        sftp.get(f"/tmp/fl_verify_{user}.png", local_img)
        sftp.close()
        print(f"[{host}] 成功获取截图: {local_img}")
    except Exception as e:
        print(f"[{host}] 获取截图失败: {e}")
        
    c.close()
