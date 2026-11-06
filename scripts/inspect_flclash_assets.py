#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """
echo "=== flutter_assets ==="
ls -la /usr/share/FlClash/data/flutter_assets/
echo "=== FontManifest.json ==="
cat /usr/share/FlClash/data/flutter_assets/FontManifest.json
echo "=== fonts dir ==="
find /usr/share/FlClash/data/flutter_assets/ -name "*.ttf" -o -name "*.otf"
"""

stdin, stdout, stderr = c.exec_command(script)
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
