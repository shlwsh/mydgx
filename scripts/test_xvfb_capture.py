#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 xvfb-run 在独立虚拟 X 服务器中启动 FlClash 并截屏
"""

import os
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """#!/bin/bash
which xvfb-run >/dev/null 2>&1 || echo winbot | sudo -S apt-get install -y xvfb

echo "=== 使用 xvfb-run 启动 FlClash 并在 zh_CN.utf8 下截屏 ==="
rm -f /tmp/fl_xvfb.png /tmp/fl_xvfb.log

xvfb-run -s "-screen 0 1280x800x24" bash -c '
    export LANG=zh_CN.utf8
    export LC_ALL=zh_CN.utf8
    export LANGUAGE=zh_CN:zh
    
    /usr/local/bin/flclash-launcher > /tmp/fl_xvfb.log 2>&1 &
    PID=$!
    echo "后台 PID: $PID"
    sleep 4
    
    scrot /tmp/fl_xvfb.png
    kill -9 $PID 2>/dev/null || true
'

ls -la /tmp/fl_xvfb.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/run_xvfb.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/run_xvfb.sh", get_pty=True)
print("OUT:\n", stdout.read().decode("utf-8", errors="replace"))

local_path = os.path.join(os.path.dirname(__file__), "flclash_xvfb.png")
sftp = c.open_sftp()
sftp.get("/tmp/fl_xvfb.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
