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

WIN_ID=$(xwininfo -root -tree | grep -iE 'flclash' | head -n 1 | awk '{print $1}')
eval $(xdotool getwindowgeometry --shell "$WIN_ID")

# 点击右下角 + Add Profile
BTN_X=$((X + WIDTH - 100))
BTN_Y=$((Y + HEIGHT - 40))
echo "点击 Add Profile 按钮: $BTN_X, $BTN_Y"
xdotool mousemove $BTN_X $BTN_Y click 1
sleep 1

# 截图弹出的对话框
scrot -u -b /tmp/flclash_add_profile.png 2>&1 || scrot /tmp/flclash_add_profile.png 2>&1
ls -la /tmp/flclash_add_profile.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/click_add_profile.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/click_add_profile.sh")
print(stdout.read().decode())

local_path = os.path.join(os.path.dirname(__file__), "flclash_add_profile.png")
sftp = c.open_sftp()
sftp.get("/tmp/flclash_add_profile.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
