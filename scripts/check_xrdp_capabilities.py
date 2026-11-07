#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """
echo "=== xrdp 版本与特性 ==="
xrdp -v
echo "=== xorgxrdp 模块 ==="
ls -l /usr/lib/xorg/modules/libxorgxrdp.* /usr/lib/xorg/modules/drivers/xrdp* 2>/dev/null || true
echo "=== /dev/dri 状态 ==="
ls -l /dev/dri/
"""

stdin, stdout, stderr = c.exec_command(script)
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
