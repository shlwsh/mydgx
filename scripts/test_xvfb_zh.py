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
rm -f /tmp/fl_xvfb_zh.png /tmp/fl_xvfb_zh.log

xvfb-run -s "-screen 0 1024x768x24" dbus-run-session bash -c '
    export LANG=zh_CN.utf8
    export LC_ALL=zh_CN.utf8
    export LANGUAGE=zh_CN:zh
    export GDK_BACKEND=x11
    export LIBGL_ALWAYS_SOFTWARE=1
    
    cd /usr/share/FlClash || exit 1
    ./FlClash > /tmp/fl_xvfb_zh.log 2>&1 &
    PID=$!
    echo "后台 PID: $PID"
    sleep 5
    
    WIN_ID=$(xwininfo -root -tree | grep -iE "flclash" | head -n 1 | awk "{print \\$1}")
    echo "窗口 ID: $WIN_ID"
    
    if [ -n "$WIN_ID" ]; then
        scrot -u -b /tmp/fl_xvfb_zh.png || scrot /tmp/fl_xvfb_zh.png
    else
        scrot /tmp/fl_xvfb_zh.png
    fi
    kill -9 $PID 2>/dev/null || true
'

ls -la /tmp/fl_xvfb_zh.png
cat /tmp/fl_xvfb_zh.log | head -n 20
"""

sftp = c.open_sftp()
with sftp.file("/tmp/run_xvfb_zh.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/run_xvfb_zh.sh", get_pty=True)
print("OUT:\n", stdout.read().decode("utf-8", errors="replace"))

local_path = os.path.join(os.path.dirname(__file__), "flclash_xvfb_zh.png")
sftp = c.open_sftp()
sftp.get("/tmp/fl_xvfb_zh.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
