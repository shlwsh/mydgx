#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟在 FlClash 中点击侧边栏菜单（配置/设置）并截屏检查字符
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

which xdotool >/dev/null 2>&1 || echo winbot | sudo -S apt-get install -y xdotool

WIN_ID=$(xwininfo -root -tree | grep -iE 'flclash' | head -n 1 | awk '{print $1}')
echo "FlClash WIN_ID: $WIN_ID"

# 激活窗口
xdotool windowactivate "$WIN_ID"
sleep 1

# 获取窗口左上角绝对坐标
eval $(xdotool getwindowgeometry --shell "$WIN_ID")
echo "WINDOW X=$X Y=$Y WIDTH=$WIDTH HEIGHT=$HEIGHT"

# 1. 点击左侧第 3 个图标（Profiles/配置）
PROFILES_X=$((X + 35))
PROFILES_Y=$((Y + 195))
echo "点击 Profiles: $PROFILES_X, $PROFILES_Y"
xdotool mousemove $PROFILES_X $PROFILES_Y click 1
sleep 1

# 截图 1: Profiles 界面
scrot -u -b /tmp/flclash_profiles.png 2>&1 || scrot /tmp/flclash_profiles.png 2>&1

# 2. 点击左侧第 6 个图标（Settings/设置）
SETTINGS_X=$((X + 35))
SETTINGS_Y=$((Y + 365))
echo "点击 Settings: $SETTINGS_X, $SETTINGS_Y"
xdotool mousemove $SETTINGS_X $SETTINGS_Y click 1
sleep 1

# 截图 2: Settings 界面
scrot -u -b /tmp/flclash_settings.png 2>&1 || scrot /tmp/flclash_settings.png 2>&1

ls -la /tmp/flclash_profiles.png /tmp/flclash_settings.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/click_and_capture.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/click_and_capture.sh")
out = stdout.read().decode("utf-8", errors="replace")
print("EXEC OUT:\n", out)

# 下载截图
sftp = c.open_sftp()
for name in ["flclash_profiles.png", "flclash_settings.png"]:
    local_path = os.path.join(os.path.dirname(__file__), name)
    try:
        sftp.get(f"/tmp/{name}", local_path)
        print(f"下载成功: {local_path} ({os.path.getsize(local_path)} 字节)")
    except Exception as e:
        print(f"下载 {name} 失败: {e}")
sftp.close()
c.close()
