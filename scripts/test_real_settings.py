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
echo "WIN_ID: $WIN_ID"

# 尝试点击第 5 个图标 (扳手/工具/设置图标)
# 扳手图标在 y = 540 左右？看左侧图标分布:
# 图标 1: Logo (y=40)
# 图标 2: Dashboard (y=130)
# 图标 3: Profiles (y=190)
# 图标 4: Proxies (y=250)
# 图标 5: Logs (y=310)
# 图标 6: Tools (y=370)
# 图标 7: 设置 (扳手/齿轮，y=535)

xdotool windowactivate "$WIN_ID"
sleep 0.5
xdotool mousemove --window "$WIN_ID" 30 535 click 1
sleep 1.5

scrot -u -b /tmp/flclash_real_settings.png 2>&1
ls -la /tmp/flclash_real_settings.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/click_settings.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/click_settings.sh")
print(stdout.read().decode())

local_path = os.path.join(os.path.dirname(__file__), "flclash_real_settings.png")
sftp = c.open_sftp()
sftp.get("/tmp/flclash_real_settings.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
