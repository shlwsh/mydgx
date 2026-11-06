#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同步安装 Node1 的 xvfb 和 scrot，并捕获 Node1 真实窗口截图验证
"""

import os
import paramiko
from env_helper import init_env

init_env()
host = "172.19.49.159"
user = "dgxdeploy"
pwd = "winning"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=pwd)

print(f"[{host}] 正在同步安装 xvfb, scrot, x11-utils...")
stdin, stdout, stderr = c.exec_command("sudo apt-get update && sudo apt-get install -y xvfb scrot x11-utils")
print(stdout.read().decode('utf-8', errors='replace')[-500:])

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

print(f"[{host}] 执行截图脚本...")
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
