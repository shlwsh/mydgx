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

# 窗口相对坐标点击 (620, 575)
xdotool mousemove --window "$WIN_ID" 620 575 click 1
sleep 2

scrot -u -b /tmp/flclash_dialog.png 2>&1 || scrot /tmp/flclash_dialog.png 2>&1
ls -la /tmp/flclash_dialog.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/click_dialog.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/click_dialog.sh")
print(stdout.read().decode())

local_path = os.path.join(os.path.dirname(__file__), "flclash_dialog.png")
sftp = c.open_sftp()
sftp.get("/tmp/flclash_dialog.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
