#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

stdin, stdout, stderr = c.exec_command("cat /tmp/trace_raw.log")
lines = stdout.read().decode('utf-8', errors='replace').splitlines()
print(f"Total lines: {len(lines)}")
for line in lines[:50]:
    print(line)

c.close()
