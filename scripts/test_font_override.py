#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心实验：覆盖 Cantarell 与 Ubuntu 系统字体为思源黑体/文泉驿，测试 Flutter 是否直接命中中文字形
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

# 准备源中文字体 (包含完整 ASCII + 简体中文)
ZH_FONT="/usr/share/fonts/opentype/noto/NotoSansCJK-SC-Regular.otf"
[ -f "$ZH_FONT" ] || ZH_FONT="/usr/share/fonts/truetype/wqy/wqy-microhei.ttf"

echo "使用中文字体源: $ZH_FONT ($(ls -lh $ZH_FONT | awk '{print $5}'))"

# 1. 备份并替换 Cantarell 字体
CANTARELL_DIR="/usr/share/fonts/opentype/cantarell"
if [ -d "$CANTARELL_DIR" ]; then
    sudo mkdir -p "${CANTARELL_DIR}.bak"
    sudo cp -n "$CANTARELL_DIR"/* "${CANTARELL_DIR}.bak/" 2>/dev/null || true
    for f in "$CANTARELL_DIR"/*.otf; do
        sudo cp "$ZH_FONT" "$f"
    done
    echo "已覆盖 Cantarell 字体为中文字体"
fi

# 2. 备份并替换 Ubuntu 字体
UBUNTU_DIR="/usr/share/fonts/truetype/ubuntu"
if [ -d "$UBUNTU_DIR" ]; then
    sudo mkdir -p "${UBUNTU_DIR}.bak"
    sudo cp -n "$UBUNTU_DIR"/* "${UBUNTU_DIR}.bak/" 2>/dev/null || true
    for f in "$UBUNTU_DIR"/Ubuntu*.ttf; do
        sudo cp "$ZH_FONT" "$f" 2>/dev/null || true
    done
    echo "已覆盖 Ubuntu 字体为中文字体"
fi

sudo fc-cache -f

# 3. 彻底杀死旧进程并以 zh_CN.utf8 启动 FlClash
killall -9 FlClash FlClashCore 2>/dev/null || true
rm -f /tmp/FlClashSocket* 2>/dev/null || true
sleep 1

export LANG=zh_CN.utf8
export LANGUAGE=zh_CN:zh
export LC_ALL=zh_CN.utf8

/usr/local/bin/flclash-launcher > /tmp/fl_override.log 2>&1 &
PID=$!
echo "启动 PID: $PID"
sleep 4

rm -f /tmp/flclash_override_res.png
scrot -u -b /tmp/flclash_override_res.png 2>&1 || scrot /tmp/flclash_override_res.png 2>&1
ls -lh /tmp/flclash_override_res.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/run_font_override.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/run_font_override.sh", get_pty=True)
print("OUT:\n", stdout.read().decode("utf-8", errors="replace"))

local_path = os.path.join(os.path.dirname(__file__), "flclash_override_res.png")
sftp = c.open_sftp()
sftp.get("/tmp/flclash_override_res.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
