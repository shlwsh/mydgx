#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新 shared_preferences.json 中的 themeProps，增加 "fontFamily": "CJKFont"
"""

import json
import os
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

# 读取当前的 json
stdin, stdout, stderr = c.exec_command("cat ~/.local/share/com.follow.clash/shared_preferences.json")
raw = stdout.read().decode('utf-8')
prefs = json.loads(raw)
config_str = prefs["flutter.config"]
config = json.loads(config_str)

# 在 themeProps 中增加 fontFamily
config["themeProps"]["fontFamily"] = "CJKFont"
print("更新后的 themeProps:", config["themeProps"])

prefs["flutter.config"] = json.dumps(config, separators=(',', ':'))
new_raw = json.dumps(prefs, separators=(',', ':'))

# 写入
sftp = c.open_sftp()
with sftp.file("/home/winbot/.local/share/com.follow.clash/shared_preferences.json", "w") as f:
    f.write(new_raw)
sftp.close()

print("已写入 shared_preferences.json")

# 启动并截屏
script = """#!/bin/bash
killall -9 FlClash FlClashCore 2>/dev/null || true
rm -f /tmp/FlClashSocket* 2>/dev/null || true
sleep 1
rm -f /tmp/fl_font_pref.png

xvfb-run -s "-screen 0 1024x768x24" dbus-run-session bash -c '
    export LANG=zh_CN.utf8
    export LC_ALL=zh_CN.utf8
    export LANGUAGE=zh_CN:zh
    export GDK_BACKEND=x11
    export LIBGL_ALWAYS_SOFTWARE=1
    
    cd /usr/share/FlClash || exit 1
    ./FlClash > /tmp/fl_font_pref.log 2>&1 &
    PID=$!
    sleep 5
    
    WIN_ID=$(xwininfo -root -tree | grep -iE "flclash" | head -n 1 | awk "{print \\$1}")
    if [ -n "$WIN_ID" ]; then
        scrot -u -b /tmp/fl_font_pref.png || scrot /tmp/fl_font_pref.png
    else
        scrot /tmp/fl_font_pref.png
    fi
    kill -9 $PID 2>/dev/null || true
'
ls -lh /tmp/fl_font_pref.png
"""

stdin, stdout, stderr = c.exec_command(script, get_pty=True)
print("OUT:\n", stdout.read().decode('utf-8', errors='replace'))

local_path = os.path.join(os.path.dirname(__file__), "flclash_font_pref.png")
sftp = c.open_sftp()
sftp.get("/tmp/fl_font_pref.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
