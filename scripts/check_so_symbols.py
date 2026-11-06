#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """
echo "=== LDD libflutter_linux_gtk.so ==="
ldd /usr/share/FlClash/lib/libflutter_linux_gtk.so | grep -i font || true
echo "=== Strings libfontconfig ==="
strings /usr/share/FlClash/lib/libflutter_linux_gtk.so | grep -i "libfontconfig" || true
echo "=== Strings FcInit or FcFont ==="
strings /usr/share/FlClash/lib/libflutter_linux_gtk.so | grep -iE "(FcInit|FcFontMatch|FcPattern)" | head -n 20 || true
echo "=== ldd /usr/share/FlClash/FlClash ==="
ldd /usr/share/FlClash/FlClash
"""

stdin, stdout, stderr = c.exec_command(script)
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
