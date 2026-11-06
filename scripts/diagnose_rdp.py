#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import paramiko

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

logger = setup_logger("DiagnoseRealtime")

def main():
    init_env()
    for name, host, u, p in [("Node0", "172.19.50.70", "winbot", "winbot")]:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(host, 22, u, p, timeout=10)
        print(f"=== {name} /etc/X11/xrdp/xorg.conf ===")
        _, o, _ = c.exec_command("cat /etc/X11/xrdp/xorg.conf")
        print(o.read().decode().strip())
        c.close()

if __name__ == "__main__":
    main()
