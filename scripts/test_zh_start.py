#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
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

# 杀死旧进程
killall -9 FlClash FlClashCore 2>/dev/null || true
sleep 1

# 以真实的桌面会话语言 LANG=zh_CN.utf8 启动
export LANG=zh_CN.utf8
export LANGUAGE=zh_CN:zh
export LC_ALL=zh_CN.utf8

/usr/local/bin/flclash-launcher > /tmp/fl_zh.log 2>&1 &
PID=$!
echo "启动 PID: $PID"
sleep 4

# 截屏
rm -f /tmp/flclash_zh.png
WIN_ID=$(xwininfo -root -tree | grep -iE 'flclash' | head -n 1 | awk '{print $1}')
echo "WIN_ID: $WIN_ID"

scrot -u -b /tmp/flclash_zh.png 2>&1 || scrot /tmp/flclash_zh.png 2>&1
ls -la /tmp/flclash_zh.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/test_zh_start.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/test_zh_start.sh")
print(stdout.read().decode())

local_path = os.path.join(os.path.dirname(__file__), "flclash_zh.png")
sftp = c.open_sftp()
sftp.get("/tmp/flclash_zh.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
