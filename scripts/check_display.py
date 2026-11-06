#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")
stdin, stdout, stderr = c.exec_command("ls -la /tmp/.X11-unix/; who; systemctl status xrdp")
print(stdout.read().decode())
c.close()
