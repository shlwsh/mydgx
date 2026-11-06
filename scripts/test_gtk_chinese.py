#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试原生 GTK3 窗口在当前 DISPLAY 中渲染中文字符是否正常
"""

import os
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """#!/bin/bash
# 检查当前是否有运行中的 DISPLAY
ACT_DISP=$(ls /tmp/.X11-unix/ 2>/dev/null | sed 's/X/:/' | head -n 1)
echo "当前 DISPLAY: $ACT_DISP"

if [ -z "$ACT_DISP" ]; then
    echo "当前无 DISPLAY 会话，启动 xvfb 测试..."
    xvfb-run -s "-screen 0 800x600x24" python3 -c "
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

win = Gtk.Window(title='中文字体测试窗口')
win.set_default_size(400, 200)
lbl = Gtk.Label(label='仪表盘 网络速度 系统代理 正常显示汉字')
win.add(lbl)
win.connect('destroy', Gtk.main_quit)
win.show_all()

import subprocess
import time
# 延迟后截屏并退出
def take_shot():
    subprocess.run(['scrot', '/tmp/gtk_chinese_test.png'])
    Gtk.main_quit()

from gi.repository import GLib
GLib.timeout_add(1000, take_shot)
Gtk.main()
"
else
    export DISPLAY=$ACT_DISP
    export XAUTHORITY=/home/winbot/.Xauthority
    python3 -c "
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

win = Gtk.Window(title='中文字体测试窗口')
win.set_default_size(400, 200)
lbl = Gtk.Label(label='仪表盘 网络速度 系统代理 正常显示汉字')
win.add(lbl)
win.connect('destroy', Gtk.main_quit)
win.show_all()

import subprocess
def take_shot():
    subprocess.run(['scrot', '-u', '-b', '/tmp/gtk_chinese_test.png'])
    Gtk.main_quit()

from gi.repository import GLib
GLib.timeout_add(1000, take_shot)
Gtk.main()
"
fi

ls -la /tmp/gtk_chinese_test.png
"""

sftp = c.open_sftp()
with sftp.file("/tmp/test_gtk_zh.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/test_gtk_zh.sh", get_pty=True)
print("OUT:\n", stdout.read().decode("utf-8", errors="replace"))

local_path = os.path.join(os.path.dirname(__file__), "gtk_chinese_test.png")
sftp = c.open_sftp()
sftp.get("/tmp/gtk_chinese_test.png", local_path)
sftp.close()
c.close()
print("下载完成:", local_path)
