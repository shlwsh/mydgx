#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心假说验证：Skia DirectoryFontLoader 只扫描 /usr/share/fonts/ 根层，不递归！
我们在 /usr/share/fonts/ 根目录下建立软链接或直接复制字体文件，验证 Flutter 能否直接加载！
"""

import os
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """#!/bin/bash
set -e

# 1. 在 /usr/share/fonts/ 根目录下创建字体链接或复制
sudo cp /usr/share/fonts/truetype/wqy/wqy-microhei.ttf /usr/share/fonts/wqy-microhei.ttf
sudo cp /usr/share/fonts/opentype/noto/NotoSansCJK-SC-Regular.otf /usr/share/fonts/NotoSansCJK-SC-Regular.otf
sudo chmod 644 /usr/share/fonts/wqy-microhei.ttf /usr/share/fonts/NotoSansCJK-SC-Regular.otf

# 同时也软链接一份 Ubuntu.ttf 和 DejaVuSans.ttf
if [ -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf ]; then
    sudo cp /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf /usr/share/fonts/DejaVuSans.ttf
fi

ls -la /usr/share/fonts/*.ttf /usr/share/fonts/*.otf || true

# 2. 杀掉旧 FlClash
killall -9 FlClash FlClashCore 2>/dev/null || true
rm -f /tmp/FlClashSocket* 2>/dev/null || true
sleep 1

# 3. 运行并截屏
rm -f /tmp/fl_root_font.png

xvfb-run -s "-screen 0 1024x768x24" dbus-run-session bash -c '
    export LANG=zh_CN.utf8
    export LC_ALL=zh_CN.utf8
    export LANGUAGE=zh_CN:zh
    export GDK_BACKEND=x11
    export LIBGL_ALWAYS_SOFTWARE=1
    
    cd /usr/share/FlClash || exit 1
    ./FlClash > /tmp/fl_root_font.log 2>&1 &
    PID=$!
    sleep 5
    
    WIN_ID=$(xwininfo -root -tree | grep -iE "flclash" | head -n 1 | awk "{print \\$1}")
    if [ -n "$WIN_ID" ]; then
        scrot -u -b /tmp/fl_root_font.png || scrot /tmp/fl_root_font.png
    else
        scrot /tmp/fl_root_font.png
    fi
    kill -9 $PID 2>/dev/null || true
'

ls -lh /tmp/fl_root_font.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/run_root_font_test.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/run_root_font_test.sh", get_pty=True)
print("OUT:\n", stdout.read().decode('utf-8', errors='replace'))

local_path = os.path.join(os.path.dirname(__file__), "flclash_root_font.png")
sftp = c.open_sftp()
sftp.get("/tmp/fl_root_font.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
