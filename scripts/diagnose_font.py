#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断双工作站中文字体、Locale 与 FlClash 字符渲染问题
"""

import os
import sys
import logging
import argparse
import paramiko
from env_helper import init_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("FontDiagnose")


def get_ssh_client(host, user, password, port=22):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=port, username=user, password=password, timeout=10)
    return client


def diagnose_node(host, user, password, node_name):
    logger.info(f"==================== 诊断 {node_name} ({host}) ====================")
    c = get_ssh_client(host, user, password)

    script = """
echo "=== 1. 检查当前系统 Locale ==="
locale
locale -a | grep -iE "zh|en" || true

echo "=== 2. 检查系统中已安装的中文字体 (fc-list :lang=zh) ==="
which fc-list || echo "未安装 fontconfig/fc-list"
fc-list :lang=zh 2>/dev/null | head -n 30 || echo "未检索到中文字体！"
echo "中文字体总数: $(fc-list :lang=zh 2>/dev/null | wc -l)"

echo "=== 3. 检查系统字体包安装状态 ==="
dpkg -l | grep -E "fonts-noto|fonts-wqy|fonts-droid|fontconfig" || true

echo "=== 4. 检查 sans-serif 默认匹配字体 ==="
fc-match sans-serif
fc-match sans-serif:lang=zh

echo "=== 5. 检查 FlClash 用户配置文件中的语言设置 ==="
for u in winbot his_test dgxdeploy; do
    cfg="/home/$u/.config/flclash/preferences.json"
    if [ -f "$cfg" ]; then
        echo "用户 $u preferences.json:"
        head -n 20 "$cfg"
    fi
done
"""
    stdin, stdout, stderr = c.exec_command(script)
    out = stdout.read().decode("utf-8", errors="replace")
    print(out)
    c.close()


def main():
    init_env()
    host0 = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user0 = os.getenv("SSH_NODE0_USER", "winbot")
    pass0 = os.getenv("SSH_NODE0_PASS", "winbot")

    host1 = os.getenv("NODE1_MGMT_IP", "172.19.49.159")
    user1 = os.getenv("SSH_NODE1_USER", "dgxdeploy")
    pass1 = os.getenv("SSH_NODE1_PASS", "winning")

    diagnose_node(host0, user0, pass0, "Node0 (70)")
    diagnose_node(host1, user1, pass1, "Node1 (159)")


if __name__ == "__main__":
    main()
