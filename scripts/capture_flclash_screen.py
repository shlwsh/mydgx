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
echo "找到窗口 ID: $WIN_ID"

if [ -n "$WIN_ID" ]; then
    import -window "$WIN_ID" /tmp/flclash_win.png
else
    import -window root /tmp/flclash_win.png
fi

ls -la /tmp/flclash_win.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/capture2.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/capture2.sh")
out = stdout.read().decode("utf-8", errors="replace")
print("EXEC OUT:\n", out)

local_png = os.path.join(os.path.dirname(__file__), "flclash_screen.png")
try:
    sftp = c.open_sftp()
    sftp.get("/tmp/flclash_win.png", local_png)
    sftp.close()
    print(f"成功下载截图至本地: {local_png} (大小={os.path.getsize(local_png)} 字节)")
except Exception as e:
    print(f"下载截图失败: {e}")

c.close()
