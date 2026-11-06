#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心验证：将 wqy-microhei.ttf 注入到 FlClash 内置的 JetBrainsMono-Regular.ttf 资产中
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

FONT_TARGET="/usr/share/FlClash/data/flutter_assets/assets/fonts/JetBrainsMono-Regular.ttf"

# 备份原字体
if [ ! -f "${FONT_TARGET}.orig" ]; then
    sudo cp "$FONT_TARGET" "${FONT_TARGET}.orig"
fi

# 用 wqy-microhei.ttf 替换
sudo cp /usr/share/fonts/truetype/wqy/wqy-microhei.ttf "$FONT_TARGET"
sudo chmod 644 "$FONT_TARGET"
ls -lh "$FONT_TARGET"

# 杀死旧进程
killall -9 FlClash FlClashCore 2>/dev/null || true
rm -f /tmp/FlClashSocket* 2>/dev/null || true
sleep 1

# 以 zh_CN.utf8 启动
export LANG=zh_CN.utf8
export LANGUAGE=zh_CN:zh
export LC_ALL=zh_CN.utf8

/usr/local/bin/flclash-launcher > /tmp/fl_replace.log 2>&1 &
PID=$!
echo "冷启动 PID: $PID"

sleep 4

rm -f /tmp/flclash_font_replace.png
scrot -u -b /tmp/flclash_font_replace.png 2>&1 || scrot /tmp/flclash_font_replace.png 2>&1
ls -la /tmp/flclash_font_replace.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/run_font_replace_test.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/run_font_replace_test.sh", get_pty=True)
print("OUT:\n", stdout.read().decode("utf-8", errors="replace"))

local_path = os.path.join(os.path.dirname(__file__), "flclash_font_replace.png")
sftp = c.open_sftp()
sftp.get("/tmp/flclash_font_replace.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
