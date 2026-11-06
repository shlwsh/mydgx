#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证方案：通过 Flutter 内置 Asset 注入中文字体，并在 themeProps 中设置 fontFamily
"""

import json
import os
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """#!/bin/bash
set -e

# 1. 复制文泉驿微米黑到 FlClash 的 assets/fonts/ 目录
CJK_SRC="/usr/share/fonts/truetype/wqy/wqy-microhei.ttf"
CJK_DST="/usr/share/FlClash/data/flutter_assets/assets/fonts/CJKFont.ttf"

if [ ! -f "$CJK_DST" ]; then
    sudo cp "$CJK_SRC" "$CJK_DST"
    sudo chmod 644 "$CJK_DST"
fi
ls -lh "$CJK_DST"

# 2. 备份并更新 FontManifest.json
MANIFEST="/usr/share/FlClash/data/flutter_assets/FontManifest.json"
if [ ! -f "${MANIFEST}.bak" ]; then
    sudo cp "$MANIFEST" "${MANIFEST}.bak"
fi

cat << 'EOF' | sudo tee "$MANIFEST"
[
  {"family":"MaterialIcons","fonts":[{"asset":"fonts/MaterialIcons-Regular.otf"}]},
  {"family":"JetBrainsMono","fonts":[{"asset":"assets/fonts/CJKFont.ttf"}]},
  {"family":"CJKFont","fonts":[{"asset":"assets/fonts/CJKFont.ttf"}]},
  {"family":"Twemoji","fonts":[{"asset":"assets/fonts/Twemoji.Mozilla.ttf"}]},
  {"family":"Icons","fonts":[{"asset":"assets/fonts/Icons.ttf"}]}
]
EOF
cat "$MANIFEST"

# 3. 杀掉旧进程
killall -9 FlClash FlClashCore 2>/dev/null || true
rm -f /tmp/FlClashSocket* 2>/dev/null || true
sleep 1

# 4. 在 xvfb 下以 zh_CN.utf8 启动 FlClash 并截屏
rm -f /tmp/fl_cjk_test.png /tmp/fl_cjk_test.log

xvfb-run -s "-screen 0 1024x768x24" dbus-run-session bash -c '
    export LANG=zh_CN.utf8
    export LC_ALL=zh_CN.utf8
    export LANGUAGE=zh_CN:zh
    export GDK_BACKEND=x11
    export LIBGL_ALWAYS_SOFTWARE=1
    
    cd /usr/share/FlClash || exit 1
    ./FlClash > /tmp/fl_cjk_test.log 2>&1 &
    PID=$!
    echo "后台 PID: $PID"
    sleep 5
    
    WIN_ID=$(xwininfo -root -tree | grep -iE "flclash" | head -n 1 | awk "{print \\$1}")
    echo "窗口 ID: $WIN_ID"
    
    if [ -n "$WIN_ID" ]; then
        scrot -u -b /tmp/fl_cjk_test.png || scrot /tmp/fl_cjk_test.png
    else
        scrot /tmp/fl_cjk_test.png
    fi
    kill -9 $PID 2>/dev/null || true
'

ls -la /tmp/fl_cjk_test.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/run_cjk_test.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/run_cjk_test.sh", get_pty=True)
print("OUT:\n", stdout.read().decode("utf-8", errors="replace"))

local_path = os.path.join(os.path.dirname(__file__), "flclash_cjk_test1.png")
sftp = c.open_sftp()
sftp.get("/tmp/fl_cjk_test.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
