#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
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

WIN_ID=$(xwininfo -root -tree | grep -iE 'flclash' | head -n 1 | awk '{print $1}')
echo "FlClash 窗口: $WIN_ID"
xdotool windowactivate "$WIN_ID"
sleep 1

# 1. 点击左侧 Profiles 图标
echo "点击 Profiles 图标..."
xdotool mousemove --window "$WIN_ID" 30 200 click 1
sleep 1.5

# 2. 点击 Add Profile 按钮
echo "点击 Add Profile 按钮..."
xdotool mousemove --window "$WIN_ID" 620 575 click 1
sleep 1.5

scrot -u -b /tmp/flclash_add_dialog.png 2>&1 || scrot /tmp/flclash_add_dialog.png 2>&1
ls -la /tmp/flclash_add_dialog.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/click_full_flow.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/click_full_flow.sh")
print(stdout.read().decode())

local_path = os.path.join(os.path.dirname(__file__), "flclash_add_dialog.png")
sftp = c.open_sftp()
sftp.get("/tmp/flclash_add_dialog.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
