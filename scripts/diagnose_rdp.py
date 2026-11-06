#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/diagnose_rdp.py - 远程桌面 (RDP/xrdp) 闪退与异常诊断脚本
"""

import os
import sys

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

import paramiko

logger = setup_logger("DiagnoseRDP")


def diagnose_node(host, user, password, name, target_users):
    logger.info("=" * 60)
    logger.info(f"正在诊断节点: {name} ({host})")
    logger.info("=" * 60)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, 22, user, password, timeout=10)

    try:
        commands = [
            ("查看 /etc/xrdp/startwm.sh", "cat /etc/xrdp/startwm.sh"),
            ("查看 /etc/xrdp/sesman.ini [Sessions] 段", "cat /etc/xrdp/sesman.ini | grep -A 10 '\\[Sessions\\]' || true"),
            ("查看 /etc/xrdp/xrdp.ini 关键配置", "cat /etc/xrdp/xrdp.ini | grep -E 'security_layer|crypt_level|certificate|key_file' || true"),
        ]

        for u in target_users:
            commands.extend([
                (f"用户 {u} ~/.xsession-errors", f"sudo -S cat /home/{u}/.xsession-errors 2>/dev/null | tail -n 30 || echo '无日志'"),
                (f"用户 {u} ~/.xorgxrdp.*.log 最新日志", f"sudo -S tail -n 30 $(ls -t /home/{u}/.xorgxrdp.*.log 2>/dev/null | head -n 1) 2>/dev/null || echo '无日志'"),
                (f"用户 {u} ~/.xsessionrc 内容", f"sudo -S cat /home/{u}/.xsessionrc 2>/dev/null || echo '无文件'"),
            ])

        for title, cmd in commands:
            logger.info(f"--> {title}")
            stdin, stdout, stderr = c.exec_command(cmd)
            if "sudo -S" in cmd:
                stdin.write(f"{password}\n")
                stdin.flush()
            out = stdout.read().decode("utf-8").strip()
            # 过滤 sudo 提示
            clean_out = "\n".join(l for l in out.splitlines() if "[sudo] password" not in l).strip()
            if clean_out:
                print(clean_out)
            print()

    finally:
        c.close()


def main():
    init_env()
    host0 = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user0 = os.getenv("SSH_NODE0_USER", "winbot")
    pass0 = os.getenv("SSH_NODE0_PASS", "winbot")

    host1 = os.getenv("NODE1_MGMT_IP", "172.19.49.159")
    user1 = os.getenv("SSH_NODE1_USER", "dgxdeploy")
    pass1 = os.getenv("SSH_NODE1_PASS", "winning")

    diagnose_node(host0, user0, pass0, "Node0 工作站", ["winbot"])
    diagnose_node(host1, user1, pass1, "Node1 工作站", ["dgxdeploy", "his_test"])
    return 0


if __name__ == "__main__":
    main()
