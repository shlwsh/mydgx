#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
完全杀死旧的 FlClash 进程，使用最新字体配置冷启动，并截屏验证
"""

import os
import sys
import time
import paramiko
from env_helper import init_env

init_env()

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """#!/bin/bash
export DISPLAY=:10
export XAUTHORITY=/home/winbot/.Xauthority
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

echo "=== 1. 彻底杀死旧进程 ==="
killall -9 FlClash FlClashCore 2>/dev/null || true
rm -f /tmp/FlClashSocket* 2>/dev/null || true
sleep 1

echo "=== 2. 使用新包装器冷启动 FlClash ==="
/usr/local/bin/flclash-launcher > /tmp/fl_cold.log 2>&1 &
PID=$!
echo "冷启动 PID: $PID"

sleep 4

echo "=== 3. 截取最新窗口 ==="
rm -f /tmp/flclash_cold.png
WIN_ID=$(xwininfo -root -tree | grep -iE 'flclash' | head -n 1 | awk '{print $1}')
echo "窗口 ID: $WIN_ID"

if [ -n "$WIN_ID" ]; then
    scrot -u -b /tmp/flclash_cold.png 2>&1 || scrot /tmp/flclash_cold.png 2>&1
else
    scrot /tmp/flclash_cold.png 2>&1
fi

ls -la /tmp/flclash_cold.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/cold_start_capture.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/cold_start_capture.sh")
out = stdout.read().decode("utf-8", errors="replace")
print("EXEC OUT:\n", out)

local_png = os.path.join(os.path.dirname(__file__), "flclash_cold.png")
try:
    sftp = c.open_sftp()
    sftp.get("/tmp/flclash_cold.png", local_png)
    sftp.close()
    print(f"成功下载新截图: {local_png} (大小={os.path.getsize(local_png)} 字节)")
except Exception as e:
    print(f"下载失败: {e}")

c.close()
