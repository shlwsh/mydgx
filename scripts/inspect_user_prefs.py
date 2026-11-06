#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

stdin, stdout, stderr = c.exec_command("cat ~/.local/share/com.follow.clash/shared_preferences.json")
raw = stdout.read().decode('utf-8', errors='replace')
print("JSON content:")
try:
    data = json.loads(raw)
    print(json.dumps(data, indent=2, ensure_ascii=False))
except Exception as e:
    print("Raw:", raw[:500])

c.close()
