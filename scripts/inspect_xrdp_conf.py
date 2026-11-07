#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """
echo "=== /etc/xrdp/xrdp.ini [Globals] ==="
sed -n '1,/^\[Xorg\]/p' /etc/xrdp/xrdp.ini
echo "=== /etc/xrdp/sesman.ini ==="
cat /etc/xrdp/sesman.ini | grep -v '^#' | grep -v '^;' | grep .
"""

stdin, stdout, stderr = c.exec_command(script)
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
