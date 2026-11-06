#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断双工作站 FlClash 桌面应用启动失败原因
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
logger = logging.getLogger("FlClashDiagnose")


def get_ssh_client(host, user, password, port=22):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=port, username=user, password=password, timeout=10)
    return client


def run_command(client, cmd, password=None):
    if password and ("sudo " in cmd or cmd.strip().startswith("su ")):
        full_cmd = f"echo '{password}' | sudo -S bash -c {cmd!r}"
    else:
        full_cmd = cmd

    stdin, stdout, stderr = client.exec_command(full_cmd, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out


def diagnose_node(host, user, password, node_name):
    logger.info(f"==================== 开始诊断 {node_name} ({host}) ====================")
    try:
        client = get_ssh_client(host, user, password)
    except Exception as e:
        logger.error(f"连接 {node_name} 失败: {e}")
        return

    script = """
echo "=== 1. 查找 FlClash 二进制路径与权限 ==="
which FlClash || echo "which FlClash 未找到"
ls -la /usr/bin/FlClash* 2>/dev/null || echo "无 /usr/bin/FlClash*"
ls -la /usr/share/FlClash 2>/dev/null || echo "无 /usr/share/FlClash"

echo "=== 2. 查看 FlClash.desktop 内容 ==="
cat /usr/share/applications/FlClash.desktop 2>/dev/null || echo "无 /usr/share/applications/FlClash.desktop"

echo "=== 3. 检查用户桌面上的快捷方式与权限 ==="
for u in winbot dgxdeploy; do
    if [ -d "/home/$u/Desktop" ]; then
        echo "--- 用户 $u 桌面快捷方式 ---"
        ls -la /home/$u/Desktop/*.desktop 2>/dev/null
        if [ -f "/home/$u/Desktop/FlClash.desktop" ]; then
            echo "--- 用户 $u FlClash.desktop 权限与属性 ---"
            head -n 15 /home/$u/Desktop/FlClash.desktop
            gio info /home/$u/Desktop/FlClash.desktop 2>/dev/null | grep -i metadata || true
        fi
    fi
done

echo "=== 4. 动态链接库依赖检查 (ldd) ==="
FLCLASH_BIN=$(which FlClash 2>/dev/null || echo "/usr/bin/FlClash")
if [ -f "$FLCLASH_BIN" ]; then
    echo "检查二进制: $FLCLASH_BIN"
    file "$FLCLASH_BIN"
    ldd "$FLCLASH_BIN" | grep "not found" || echo "ldd 检查正常，无 not found 依赖"
    echo "依赖的主要图形与桌面库:"
    ldd "$FLCLASH_BIN" | grep -E "libgtk|libglib|libayatana|libappindicator|libsecret|libkeybinder|libGL|libEGL|libepoxy" || true
fi

echo "=== 5. 检查当前图形会话与 DISPLAY ==="
ps aux | grep -E "Xorg|xrdp|gnome-session" | grep -v grep || echo "未找到运行中的图形会话进程"
echo "检查活跃 DISPLAY socket:"
ls -la /tmp/.X11-unix/ 2>/dev/null || echo "无 /tmp/.X11-unix"

echo "=== 6. 查看用户目录下的 FlClash 配置与日志 ==="
for u in winbot dgxdeploy; do
    if [ -d "/home/$u/.config/flclash" ] || [ -d "/home/$u/.config/FlClash" ]; then
        echo "--- 用户 $u 的配置与日志目录 ---"
        ls -la /home/$u/.config/*clash* /home/$u/.config/*Clash* 2>/dev/null
        find /home/$u/.config/*clash* /home/$u/.config/*Clash* -type f -name "*.log" 2>/dev/null -exec tail -n 20 {} + || true
    fi
done

echo "=== 7. 测试命令行直接拉起 FlClash (抓取报错) ==="
# 尝试使用 xvfb 或者当前已有的 DISPLAY 测试
if ! which xvfb-run >/dev/null 2>&1; then
    echo "系统未安装 xvfb，正在尝试寻找当前活跃 DISPLAY..."
    ACT_DISP=$(ls /tmp/.X11-unix/ 2>/dev/null | sed 's/X/:/' | head -n 1)
    if [ -n "$ACT_DISP" ]; then
        echo "尝试在 DISPLAY=$ACT_DISP 下以当前用户启动 FlClash (超时 3 秒)..."
        DISPLAY=$ACT_DISP timeout 3 "$FLCLASH_BIN" 2>&1 || echo "执行结束 (退出码=$?)"
    else
        echo "无可用 DISPLAY，尝试直接无头执行..."
        timeout 3 "$FLCLASH_BIN" 2>&1 || echo "执行结束 (退出码=$?)"
    fi
else
    echo "使用 xvfb-run 模拟运行 FlClash (超时 5 秒)..."
    xvfb-run -a timeout 5 "$FLCLASH_BIN" 2>&1 || echo "执行结束 (退出码=$?)"
fi
"""
    code, out = run_command(client, script, password)
    print(out)
    client.close()


def main():
    init_env()
    parser = argparse.ArgumentParser(description="诊断双工作站 FlClash 启动问题")
    parser.add_argument("--node", choices=["all", "node0", "node1"], default="all")
    args = parser.parse_args()

    host0 = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user0 = os.getenv("SSH_NODE0_USER", "winbot")
    pass0 = os.getenv("SSH_NODE0_PASS", "winbot")

    host1 = os.getenv("NODE1_MGMT_IP", "172.19.49.159")
    user1 = os.getenv("SSH_NODE1_USER", "dgxdeploy")
    pass1 = os.getenv("SSH_NODE1_PASS", "winning")

    if args.node in ("all", "node0"):
        diagnose_node(host0, user0, pass0, "Node0 (70)")
    if args.node in ("all", "node1"):
        diagnose_node(host1, user1, pass1, "Node1 (159)")


if __name__ == "__main__":
    main()
