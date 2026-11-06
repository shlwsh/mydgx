#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """#!/bin/bash
export LANG=zh_CN.utf8
export LC_ALL=zh_CN.utf8
export LANGUAGE=zh_CN:zh

xvfb-run -s "-screen 0 1024x768x24" bash -c '
    timeout 5 strace -f -e trace=openat /usr/share/FlClash/FlClash > /tmp/trace_raw.log 2>&1
'
echo "Strace 完成，日志大小: $(wc -c < /tmp/trace_raw.log)"
grep -iE '\.(ttf|ttc|otf)' /tmp/trace_raw.log | grep -v '\.conf' | head -n 35 || true
"""

sftp = c.open_sftp()
with sftp.file("/tmp/strace_zh.sh", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = c.exec_command("bash /tmp/strace_zh.sh")
print("OUT:\n", stdout.read().decode("utf-8", errors="replace"))
c.close()
