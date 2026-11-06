#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 strace 深度追踪 FlClash 进程打开的字体配置文件与字体文件
"""

import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """#!/bin/bash
export DISPLAY=:10
export XAUTHORITY=/home/winbot/.Xauthority
export GDK_BACKEND=x11
export LIBGL_ALWAYS_SOFTWARE=1

# 杀死旧进程
killall -9 FlClash FlClashCore 2>/dev/null || true
sleep 1

# 使用 strace 追踪 openat 调用并运行 3 秒
timeout 4 strace -f -e trace=openat /usr/share/FlClash/FlClash 2>&1 | grep -iE 'fonts|fontconfig|\\.ttf|\\.ttc|\\.otf' | head -n 80
"""

sftp = c.open_sftp()
with sftp.file("/tmp/strace_font.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/strace_font.sh")
print("STRACE OUT:\n", stdout.read().decode("utf-8", errors="replace"))
c.close()
