#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """
for p in $(pgrep -u winbot gnome-session 2>/dev/null); do
    echo "=== PID: $p ==="
    tr '\\0' '\\n' < /proc/$p/environ 2>/dev/null | grep -E '^(LANG|LC_|LANGUAGE)='
    break
done
"""
stdin, stdout, stderr = c.exec_command(script)
print(stdout.read().decode())
c.close()
