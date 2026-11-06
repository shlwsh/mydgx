#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新 local.conf 为单文件 OTF/TTF，冷重启 FlClash 并截屏
"""

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
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

# 更新 /etc/fonts/local.conf
sudo tee /etc/fonts/local.conf << 'EOF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
    <match target="pattern">
        <test qual="any" name="family">
            <string>sans-serif</string>
        </test>
        <edit name="family" mode="prepend" binding="strong">
            <string>DejaVu Sans</string>
            <string>Noto Sans CJK SC</string>
            <string>WenQuanYi Micro Hei</string>
        </edit>
    </match>
    <match target="pattern">
        <test qual="any" name="family">
            <string>monospace</string>
        </test>
        <edit name="family" mode="prepend" binding="strong">
            <string>DejaVu Sans Mono</string>
            <string>WenQuanYi Micro Hei Mono</string>
            <string>Noto Sans CJK SC</string>
        </edit>
    </match>
</fontconfig>
EOF

sudo fc-cache -f

# 杀死旧进程
killall -9 FlClash FlClashCore 2>/dev/null || true
rm -f /tmp/FlClashSocket* 2>/dev/null || true
sleep 1

# 以 zh_CN.utf8 启动
export LANG=zh_CN.utf8
export LANGUAGE=zh_CN:zh
export LC_ALL=zh_CN.utf8

/usr/local/bin/flclash-launcher > /tmp/fl_ttf.log 2>&1 &
PID=$!
echo "冷启动 PID: $PID"

sleep 4

rm -f /tmp/flclash_after_ttf.png
scrot -u -b /tmp/flclash_after_ttf.png 2>&1 || scrot /tmp/flclash_after_ttf.png 2>&1
ls -la /tmp/flclash_after_ttf.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/run_ttf_test.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/run_ttf_test.sh", get_pty=True)
print("OUT:\n", stdout.read().decode("utf-8", errors="replace"))

local_path = os.path.join(os.path.dirname(__file__), "flclash_after_ttf.png")
sftp = c.open_sftp()
sftp.get("/tmp/flclash_after_ttf.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
