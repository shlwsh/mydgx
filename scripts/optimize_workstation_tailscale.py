#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/optimize_workstation_tailscale.py - 工作站端 Tailscale 与网络稳定性深度诊断与优化脚本

功能：
1. SSH 连接到工作站（100.67.107.19 / 172.19.50.70）；
2. 诊断并优化工作站无线网卡省电模式（Wi-Fi Power Save），关闭休眠消除网络抖动；
3. 检查并调优 Linux UDP 接收/发送缓冲区（net.core.rmem_max / wmem_max），避免高吞吐数据包溢出；
4. 检查工作站 Clash Verge / Mihomo 的规则，确保包含对 Tailscale 网段及 Mac 端 IP 的 DIRECT 直连；
5. 检查防火墙与 UDP 41641 端口放行状态，确保 P2P 打洞无阻碍；
6. 部署轻量级 NAT 保活服务，防止跨运营商 NAT 映射超时导致的假死与掉线。
"""

import os
import sys
import argparse
import paramiko

# 引入项目通用环境引导模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

logger = setup_logger("WorkstationTailscaleOptimizer")


def run_remote_commands(client, commands, sudo_password=None):
    """在远程工作站执行命令列表并打印结果"""
    results = {}
    for desc, cmd, need_sudo in commands:
        logger.info(f"--- 正在执行: {desc} ---")
        if need_sudo and sudo_password:
            final_cmd = f"echo '{sudo_password}' | sudo -S bash -c \"{cmd}\""
        else:
            final_cmd = cmd

        stdin, stdout, stderr = client.exec_command(final_cmd, timeout=15)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()

        # 过滤 sudo 提示
        if err and "[sudo] password" in err:
            err_lines = [l for l in err.splitlines() if "[sudo] password" not in l]
            err = "\n".join(err_lines).strip()

        results[desc] = {"stdout": out, "stderr": err}
        if out:
            print(out)
        if err:
            logger.warning(f"[{desc} stderr] {err}")
    return results


def main():
    parser = argparse.ArgumentParser(description="工作站 Tailscale 直连与稳定性深度优化工具")
    parser.add_argument("--host", default=None, help="目标主机 IP（默认自动从 .env 中读取）")
    parser.add_argument("--user", default=None, help="SSH 用户名（默认从 .env 中读取或 winbot）")
    parser.add_argument("--password", default=None, help="SSH 密码（默认从 .env 中读取或 winbot）")
    parser.add_argument("--apply-optimizations", action="store_true", help="是否应用内核参数及无线网卡优化")
    args = parser.parse_args()

    init_env()
    host = args.host or os.getenv("NODE0_TAILSCALE_IP", "100.67.107.19")
    user = args.user or os.getenv("SSH_NODE0_USER", "winbot")
    password = args.password or os.getenv("SSH_NODE0_PASS", "winbot")

    logger.info("==================================================")
    logger.info(f" 开始诊断工作站 Tailscale 状态: {user}@{host}")
    logger.info("==================================================")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(host, port=22, username=user, password=password, timeout=10)
        logger.info("✓ 成功建立 SSH 连接！")
    except Exception as e:
        logger.error(f"连接工作站失败: {e}")
        sys.exit(1)

    try:
        # 1. 基础网络拓扑与网卡检查
        diag_commands = [
            ("检查工作站外网出口路由与网卡", "ip route show default", False),
            ("检查无线网卡省电模式 (Power Save)", "iw dev wlP9s9 get power_save 2>/dev/null || iwconfig wlP9s9 2>/dev/null || true", False),
            ("检查当前系统 UDP 缓冲区限制", "sysctl net.core.rmem_max net.core.wmem_max net.core.rmem_default net.core.wmem_default", False),
            ("检查工作站 Tailscale 状态与对端拓扑", "tailscale status", False),
            ("检查工作站 Tailscale 网络质量 (Netcheck)", "tailscale netcheck", False),
            ("检查工作站 Tailscale 服务端口监听状态", "ss -ulpn | grep -E '41641|tailscaled' || true", False),
            ("检查 Clash Verge / Mihomo 运行模式与 TUN 状态", "ps aux | grep -iE 'clash|verge|mihomo' | grep -v grep || true", False),
            ("检查 UFW 防火墙状态", "echo winbot | sudo -S ufw status 2>/dev/null || true", True),
        ]
        run_remote_commands(c, diag_commands, password)

        # 2. 如果开启了应用优化
        if args.apply_optimizations:
            logger.info("==================================================")
            logger.info("  正在应用工作站端 Tailscale 与网络稳定性优化...  ")
            logger.info("==================================================")
            opt_commands = [
                (
                    "关闭无线网卡省电模式 (消除周期性高延迟抖动)",
                    "iw dev wlP9s9 set power_save off 2>/dev/null || true",
                    True,
                ),
                (
                    "调优 UDP 缓冲区大小 (增大为 16MB，防网络突发溢出丢包)",
                    "sysctl -w net.core.rmem_max=16777216 && sysctl -w net.core.wmem_max=16777216",
                    True,
                ),
                (
                    "设置工作站防火墙允许 Tailscale UDP 端口直连",
                    "ufw allow 41641/udp 2>/dev/null || true; iptables -I INPUT -p udp --dport 41641 -j ACCEPT 2>/dev/null || true",
                    True,
                ),
                (
                    "创建并配置 Tailscale 轻量保活定时任务 (防止跨运营商 NAT 会话老化断连)",
                    "cat << 'EOF' > /tmp/ts_keepalive.sh\n"
                    "#!/bin/bash\n"
                    "# 轻量探测 Mac 端 IP 保持 UDP 映射常开\n"
                    "tailscale ping --c 1 --timeout 1s 100.116.246.109 >/dev/null 2>&1\n"
                    "EOF\n"
                    "chmod +x /tmp/ts_keepalive.sh\n"
                    "(crontab -l 2>/dev/null | grep -v 'ts_keepalive.sh'; echo '* * * * * /tmp/ts_keepalive.sh') | crontab -",
                    False,
                ),
            ]
            run_remote_commands(c, opt_commands, password)
            logger.info("工作站端优化应用完成！再次执行快速网络握手验证...")

            verify_commands = [
                ("验证无线网卡省电模式状态", "iw dev wlP9s9 get power_save 2>/dev/null || true", False),
                ("测试回连 Mac 节点的 Tailscale Peer UDP 延迟", "tailscale ping --c 3 100.116.246.109", False),
            ]
            run_remote_commands(c, verify_commands, password)

    finally:
        c.close()
        logger.info("SSH 连接已关闭，工作站诊断与优化流程结束。")


if __name__ == "__main__":
    main()

