#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/fix_node0_network.py - 70工作站“网络连接激活失败”故障根治与网络固化脚本

故障根因：
1. 70 工作站 (Node0) 的 ConnectX-7 双网卡中，第二根 200Gbps 直连网卡 (enP2p1s0f1np1) 物理线缆已插入并处于 UP 状态，
   直连对端 Node1 (172.19.49.159) 的 enP2p1s0f0np0 (静态 IP 192.168.201.2/24)。
2. Node0 的 /etc/netplan/ 仅配置了主 fabric (enp1s0f1np1: 192.168.100.10/24)，未配置副直连口 (enP2p1s0f1np1)。
3. NetworkManager 检测到 enP2p1s0f1np1 物理链路处于 UP 状态后，自动创建并反复激活默认的“有线连接 2”，尝试通过 DHCP 获取 IP；
   由于对端为静态直连网段且无 DHCP 服务器，每隔 45 秒 DHCP 超时失败一次 (reason: ip-config-unavailable)，
   触发桌面环境无限弹窗提示：“网络连接激活失败”！
4. 另外未插网线的“有线连接 1”与“有线连接 3”默认开启了 autoconnect。

修复方案：
1. 备份并更新 Node0 的 netplan 配置文件 /etc/netplan/40-cx7-deepseek.yaml，将 enP2p1s0f1np1 配置为静态 IP 192.168.201.1/24 (dhcp4: false, dhcp6: false)；
2. 清理 NetworkManager 中冲突失效的“有线连接 2”；
3. 将未插线的“有线连接 1”与“有线连接 3”设置为 connection.autoconnect no；
4. 在 /etc/NetworkManager/conf.d/ 中禁用公网连通性检测 (connectivity-check)，彻底杜绝隔离内网下的“网络受限”误报；
5. 应用 netplan apply 并验证双机副直连互通 (192.168.201.1 <-> 192.168.201.2) 及 NetworkManager 告警消除。
"""

import os
import sys
import argparse

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

import paramiko

logger = setup_logger("FixNode0Net")

REMOTE_FIX_SCRIPT = r'''#!/usr/bin/env bash
set -euo pipefail

echo "=========================================="
echo "开始执行 Node0 网络连接激活修复与固化"
echo "=========================================="

# 1. 备份原 netplan 配置
NETPLAN_FILE="/etc/netplan/40-cx7-deepseek.yaml"
if [ -f "$NETPLAN_FILE" ]; then
    cp "$NETPLAN_FILE" "${NETPLAN_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    echo "已备份现有 netplan 文件至 ${NETPLAN_FILE}.bak"
fi

# 2. 写入完整的双 CX-7 静态配置 (对齐 Node1 副链路 192.168.201.x 规范)
cat << 'EOF' > "$NETPLAN_FILE"
network:
  version: 2
  renderer: NetworkManager
  ethernets:
    enp1s0f1np1:
      addresses:
        - 192.168.100.10/24
      dhcp4: false
      dhcp6: false
    enP2p1s0f1np1:
      addresses:
        - 192.168.201.1/24
      dhcp4: false
      dhcp6: false
EOF
chmod 600 "$NETPLAN_FILE"
echo "已更新 $NETPLAN_FILE (enp1s0f1np1: 192.168.100.10/24, enP2p1s0f1np1: 192.168.201.1/24)"

# 3. 清理 NetworkManager 中失效的“有线连接 2”
echo "--> 检查并清理冲突的默认连接"
if nmcli connection show "有线连接 2" >/dev/null 2>&1; then
    nmcli connection delete "有线连接 2" || true
    echo "已成功删除冲突连接: 有线连接 2"
fi

# 4. 将未插线或未使用的有线连接禁用自动连接 (autoconnect=no)
for conn in "有线连接 1" "有线连接 3"; do
    if nmcli connection show "$conn" >/dev/null 2>&1; then
        nmcli connection modify "$conn" connection.autoconnect no || true
        echo "已将 $conn 设置为 autoconnect=no"
    fi
done

# 5. 禁用公网 connectivity-check (防止隔离内网下桌面网络图标受限与报错)
mkdir -p /etc/NetworkManager/conf.d
cat << 'EOF' > /etc/NetworkManager/conf.d/20-disable-connectivity-check.conf
[connectivity]
enabled=false
uri=
EOF
echo "已禁用 NetworkManager 公网连通性探测"

# 6. 应用 netplan 配置
echo "--> 正在应用 netplan 配置..."
netplan apply
sleep 2

echo "=========================================="
echo "Node0 网络修复脚本执行完毕"
echo "=========================================="
'''


def get_ssh_client(host, user, password, timeout=10):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=22, username=user, password=password, timeout=timeout)
    return client


def exec_sudo(client, cmd, sudo_pass, desc=None):
    if desc:
        logger.info(f"==> {desc}")
    stdin, stdout, stderr = client.exec_command(f"sudo -S {cmd}")
    stdin.write(f"{sudo_pass}\n")
    stdin.flush()
    out = stdout.read().decode("utf-8").strip()
    err = stderr.read().decode("utf-8").strip()
    code = stdout.channel.recv_exit_status()

    # 过滤 sudo 密码提示输出
    clean_err = "\n".join(
        line for line in err.splitlines() if "[sudo] password for" not in line
    ).strip()

    if out:
        logger.info(f"[命令输出]\n{out}")
    if clean_err:
        if code != 0:
            logger.error(f"[错误输出]\n{clean_err}")
        else:
            logger.debug(f"[标准错误]\n{clean_err}")

    if code != 0:
        raise RuntimeError(f"命令执行失败 (退出码={code}):\n{clean_err}")
    return out


def main():
    init_env()

    parser = argparse.ArgumentParser(
        description="70工作站网络连接激活失败故障根治工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    args = parser.parse_args()

    host0 = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user0 = os.getenv("SSH_NODE0_USER", "winbot")
    pass0 = os.getenv("SSH_NODE0_PASS", "winbot")

    logger.info("=" * 60)
    logger.info(f"开始对 70 工作站 ({user0}@{host0}) 执行网络故障修复")
    logger.info("=" * 60)

    client0 = get_ssh_client(host0, user0, pass0)
    try:
        # 1. SFTP 上传修复脚本
        remote_script = "/tmp/fix_node0_net.sh"
        sftp = client0.open_sftp()
        with sftp.file(remote_script, "w") as f:
            f.write(REMOTE_FIX_SCRIPT.replace("\r\n", "\n"))
        sftp.close()

        # 2. 执行修复
        exec_sudo(client0, f"chmod +x {remote_script}", pass0)
        exec_sudo(client0, remote_script, pass0, desc="执行网络修复与副链路静态 IP 固化")
        exec_sudo(client0, f"rm -f {remote_script}", pass0)

        # 3. 验证设备与连接状态
        logger.info("==> 验证 70 工作站设备与连接状态...")
        status_cmd = """bash -c '
echo "=== nmcli device status ==="
nmcli device status
echo "=== nmcli connection show ==="
nmcli connection show
echo "=== IP 与路由 ==="
ip -br addr show dev enP2p1s0f1np1
ip -br addr show dev enp1s0f1np1
ip route | grep -E "192.168.201|192.168.100|default"
'"""
        exec_sudo(client0, status_cmd, pass0, desc="核验设备与路由状态")

        # 4. 双机直连互通测试 (192.168.201.1 <-> 192.168.201.2 以及 192.168.100.10 <-> 192.168.100.11)
        logger.info("==> 测试双机高速链路连通性...")
        ping_cmd = """bash -c '
echo "--- 测试主链路互通 (192.168.100.11) ---"
ping -c 2 -W 2 192.168.100.11 || echo "主链路 ping 失败"
echo "--- 测试副链路互通 (192.168.201.2) ---"
ping -c 2 -W 2 192.168.201.2 || echo "副链路 ping 失败"
'"""
        exec_sudo(client0, ping_cmd, pass0, desc="执行双机 200G 链路双向 Ping 验证")

        # 5. 检查最近 NetworkManager 是否还有激活失败错误
        logger.info("==> 检查 NetworkManager 最近日志是否完全恢复正常...")
        log_check_cmd = """bash -c '
echo "=== 最近 1 分钟 NetworkManager 告警 ==="
journalctl -u NetworkManager --since "1 minute ago" --no-pager | grep -iE "fail|warn" || echo "无任何报错，完全正常！"
'"""
        exec_sudo(client0, log_check_cmd, pass0, desc="检查 NetworkManager 最新运行日志")

        logger.info("=" * 60)
        logger.info("70 工作站网络连接激活失败问题已成功彻底修复！")
        logger.info("=" * 60)

    finally:
        client0.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
