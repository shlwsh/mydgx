#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()

for host, user, pwd in [("172.19.50.70", "winbot", "winbot"), ("172.19.49.159", "dgxdeploy", "winning")]:
    print(f"\n==================== HOST: {host} ====================")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=pwd)
    
    script = """
    echo "--- /usr/local/bin/flclash-launcher ---"
    cat /usr/local/bin/flclash-launcher 2>/dev/null || echo "NOT FOUND"
    
    echo "--- /usr/share/applications/FlClash.desktop ---"
    cat /usr/share/applications/FlClash.desktop 2>/dev/null || echo "NOT FOUND"
    
    echo "--- User Desktop shortcuts ---"
    ls -la /home/*/Desktop/*.desktop 2>/dev/null || true
    for f in /home/*/Desktop/*.desktop; do
        if [ -f "$f" ]; then
            echo "File: $f"
            cat "$f"
        fi
    done
    
    echo "--- shared_preferences.json locale ---"
    for f in /home/*/.local/share/com.follow.clash/shared_preferences.json; do
        if [ -f "$f" ]; then
            echo "User Pref: $f"
            grep -o '"locale":[^,}]*' "$f" || echo "no locale"
        fi
    done
    """
    
    stdin, stdout, stderr = c.exec_command(script)
    print(stdout.read().decode('utf-8', errors='replace'))
    c.close()
