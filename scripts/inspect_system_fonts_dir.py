#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """
ls -la /usr/share/fonts/
"""

stdin, stdout, stderr = c.exec_command(script)
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
