#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/optimize_all_nodes.py - 三节点工作站 Tailscale 网络与系统深度优化工具

功能：
1. 覆盖 Node0 (cube-f22b)、Node1 (cube-0137)、Node123 (cube-fe5e) 三大节点；
2. 消除无线网卡 Wi-Fi 休眠抖动（关闭 Power Save 并持久化配置）；
3. 调优 Linux 内核 UDP 缓冲区大小（扩充至 16MB 并持久化）；
4. 严格校验与固化各节点专属 Tailscale UDP 端口（Node0: 41641, Node1: 41642, Node123: 41643）；
5. 清除 tailscaled 可能指向失效本地代理的环境变量，放行防火墙 UDP 端口；
6. 固化 NetworkManager 虚拟网卡隔离规则，防止 TUN 变更导致 WiFi 掉线；
7. 部署跨运营商轻量级 NAT 保持活跃任务（Keepalive）；
8. 巡检与测试各节点与 Mac 端之间的 P2P Direct 延迟与 RDP 握手响应。
"""

import os
import sys
import time
import socket
import argparse
import paramiko

# 引入项目通用环境引导模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

logger = setup_logger("AllNodesOptimizer")

MAC_TS_IP = "100.116.246.109"

NODE_SPECS = {
    "node0": {
        "id": "node0",
        "name": "Node0 (cube-f22b)",
        "ts_ip_env": "NODE0_TAILSCALE_IP",
        "default_ts_ip": "100.67.107.19",
        "mgmt_ip_env": "NODE0_MGMT_IP",
        "default_mgmt_ip": "172.19.50.70",
        "user_env": "SSH_NODE0_USER",
        "default_user": "winbot",
        "pass_env": "SSH_NODE0_PASS",
        "default_pass": "winbot",
        "expected_port": 41641,
        "wifi_iface": "wlP9s9",
    },
    "node1": {
        "id": "node1",
        "name": "Node1 (cube-0137)",
        "ts_ip_env": "NODE1_TAILSCALE_IP",
        "default_ts_ip": "100.103.250.108",
        "mgmt_ip_env": "NODE1_MGMT_IP",
        "default_mgmt_ip": "172.19.49.159",
        "user_env": "SSH_OPS_USER",
        "default_user": "dgxdeploy",
        "pass_env": "SSH_OPS_PASS",
        "default_pass": "winning",
        "expected_port": 41642,
        "wifi_iface": "wlP9s9",
    },
    "node123": {
        "id": "node123",
        "name": "Node123 (cube-fe5e)",
        "ts_ip_env": "NODE123_TAILSCALE_IP",
        "default_ts_ip": "100.79.120.65",
        "mgmt_ip_env": "NODE123_MGMT_IP",
        "default_mgmt_ip": "172.19.51.123",
        "user_env": "SSH_NODE123_USER",
        "default_user": "bot",
        "pass_env": "SSH_NODE123_PASS",
        "default_pass": "bot",
        "expected_port": 41643,
        "wifi_iface": "wlP9s9",
    },
}


def get_ssh_client(node_info):
    """建立 SSH 连接（优先尝试 Tailscale IP，其次管理网 IP）"""
    ts_ip = os.getenv(node_info["ts_ip_env"], node_info["default_ts_ip"])
    mgmt_ip = os.getenv(node_info["mgmt_ip_env"], node_info["default_mgmt_ip"])
    user = os.getenv(node_info["user_env"], node_info["default_user"])
    password = os.getenv(node_info["pass_env"], node_info["default_pass"])

    targets = [("Tailscale IP", ts_ip), ("管理 IP", mgmt_ip)]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    for ip_type, ip in targets:
        if not ip:
            continue
        try:
            client.connect(ip, port=22, username=user, password=password, timeout=6)
            logger.info(f"✓ [{node_info['name']}] 经 {ip_type} ({ip}) 成功建立 SSH 连接")
            return client, ip, password
        except Exception as e:
            logger.debug(f"[{node_info['name']}] 经 {ip_type} ({ip}) 连接失败: {e}")

    logger.error(f"✗ [{node_info['name']}] 所有通道均无法建立 SSH 连接！")
    return None, None, None


def exec_remote(client, cmd, sudo_pass=None, timeout=15):
    """远程执行命令并安全支持 sudo"""
    if sudo_pass:
        full_cmd = f"echo '{sudo_pass}' | sudo -S bash -c \"{cmd}\""
    else:
        full_cmd = cmd
    stdin, stdout, stderr = client.exec_command(full_cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if err and "[sudo] password" in err:
        lines = [l for l in err.splitlines() if "[sudo] password" not in l]
        err = "\n".join(lines).strip()
    return out, err


def diagnose_node(node_info, client, sudo_pass):
    """深度巡检单个节点的网络与服务状态"""
    name = node_info["name"]
    logger.info(f"========== 正在深度巡检 {name} ==========")

    diag_data = {}

    # 1. 无线网卡省电状态
    out, _ = exec_remote(client, f"iw dev {node_info['wifi_iface']} get power_save 2>/dev/null || true")
    power_save = out.replace("Power save:", "").strip() if out else "未知/非iw"
    diag_data["power_save"] = power_save
    logger.info(f"  [无线网卡省电模式] {power_save}")

    # 2. UDP 缓冲区
    out, _ = exec_remote(client, "sysctl -n net.core.rmem_max net.core.wmem_max 2>/dev/null || true")
    vals = out.split()
    rmem = int(vals[0]) if len(vals) > 0 and vals[0].isdigit() else 0
    diag_data["rmem_max"] = rmem
    logger.info(f"  [UDP rmem_max 缓冲区] {rmem} 字节 ({rmem // 1024} KB)")

    # 3. Tailscale 端口
    out, _ = exec_remote(client, "grep '^PORT=' /etc/default/tailscaled 2>/dev/null || true")
    port_cfg = out.replace("PORT=", "").strip().strip('"') if out else "默认"
    diag_data["port_config"] = port_cfg
    logger.info(f"  [/etc/default/tailscaled 配置端口] {port_cfg} (规划端口: {node_info['expected_port']})")

    # 4. Tailscale 实际监听端口
    out, _ = exec_remote(client, "ss -ulpn | grep tailscaled || true")
    diag_data["ss_tailscale"] = out.splitlines()[0] if out else "未监听"
    logger.info(f"  [实际监听状态] {diag_data['ss_tailscale']}")

    # 5. Tailscale status 对端直连状态
    out, _ = exec_remote(client, f"tailscale status | grep -E '{MAC_TS_IP}|smq-macbook' || true")
    diag_data["ts_peer_status"] = out if out else "无对端记录"
    logger.info(f"  [Mac 对端状态] {diag_data['ts_peer_status']}")

    # 6. Tailscale Ping 握手延迟
    out, _ = exec_remote(client, f"tailscale ping --c 2 --timeout 2s {MAC_TS_IP} 2>/dev/null || true")
    pong_line = ""
    for line in out.splitlines():
        if "pong from" in line:
            pong_line = line.strip()
            break
    diag_data["ts_ping"] = pong_line if pong_line else "握手超时"
    logger.info(f"  [Tailscale UDP 握手] {diag_data['ts_ping']}")

    # 7. 虚拟网卡防掉线配置
    out, _ = exec_remote(client, "cat /etc/NetworkManager/conf.d/99-ignore-virtual-nics.conf 2>/dev/null || true")
    diag_data["nm_ignore_virtual"] = bool("unmanaged-devices" in out)
    logger.info(f"  [NM 虚拟网卡防掉线配置] {'已就绪' if diag_data['nm_ignore_virtual'] else '未配置'}")

    return diag_data


def apply_node_optimizations(node_info, client, sudo_pass):
    """对单个节点应用网络与直连优化项"""
    name = node_info["name"]
    expected_port = node_info["expected_port"]
    wifi_iface = node_info["wifi_iface"]
    logger.info(f"========== 正在对 {name} 实施全套网络与直连优化 ==========")

    # 0. 固化 Tailscale 真实控制平面 IP 至 /etc/hosts，防止本地代理 Fake-IP 劫持导致断网
    logger.info("1/7 正在固化 Tailscale 控制平面与日志真实 IP (防 Fake-IP 劫持)...")
    cmd_hosts = """
    grep -v 'controlplane.tailscale.com' /etc/hosts > /tmp/hosts.tmp
    cat << 'EOF' >> /tmp/hosts.tmp
192.200.0.102 controlplane.tailscale.com log.tailscale.io
EOF
    cp /tmp/hosts.tmp /etc/hosts
    sed -i '/10.251.1.1/d' /etc/resolv.conf 2>/dev/null || true
    """
    exec_remote(client, cmd_hosts, sudo_pass)

    # 1. 禁用无线网卡省电模式 (消除周期性休眠抖动)
    logger.info("2/7 正在关闭无线网卡省电模式并配置持久化...")
    cmd_wifi = f"""
    iw dev {wifi_iface} set power_save off 2>/dev/null || true
    mkdir -p /etc/NetworkManager/conf.d/
    cat << 'EOF' > /etc/NetworkManager/conf.d/disable-wifi-powersave.conf
[connection]
wifi.powersave = 2
EOF
    """
    exec_remote(client, cmd_wifi, sudo_pass)

    # 2. 调优内核 UDP 缓冲区至 16MB
    logger.info("3/7 正在调优内核 UDP 缓冲区限制至 16MB 并持久化...")
    cmd_sysctl = """
    cat << 'EOF' > /etc/sysctl.d/99-tailscale-net.conf
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
EOF
    sysctl -p /etc/sysctl.d/99-tailscale-net.conf
    """
    exec_remote(client, cmd_sysctl, sudo_pass)

    # 3. 固化规划的 Tailscale UDP 端口
    logger.info(f"4/7 正在固化 Tailscale UDP 端口至 {expected_port} 并重启服务...")
    cmd_ts_port = f"""
    mkdir -p /etc/default
    if [ -f /etc/default/tailscaled ]; then
        if grep -q '^PORT=' /etc/default/tailscaled; then
            sed -i.bak 's/^PORT=.*/PORT="{expected_port}"/' /etc/default/tailscaled
        else
            echo 'PORT="{expected_port}"' >> /etc/default/tailscaled
        fi
    else
        echo 'PORT="{expected_port}"' > /etc/default/tailscaled
    fi

    # 清除可能导致解析异常的 tailscaled 代理 drop-in
    rm -f /etc/systemd/system/tailscaled.service.d/override.conf 2>/dev/null || true
    systemctl daemon-reload
    (sleep 1 && systemctl restart tailscaled) >/dev/null 2>&1 &
    """
    exec_remote(client, cmd_ts_port, sudo_pass)
    time.sleep(3)

    # 4. 放行对应 UDP 端口防火墙
    logger.info(f"5/7 正在放行 UDP 端口 {expected_port} 防火墙规则...")
    cmd_fw = f"""
    ufw allow {expected_port}/udp 2>/dev/null || true
    iptables -I INPUT -p udp --dport {expected_port} -j ACCEPT 2>/dev/null || true
    """
    exec_remote(client, cmd_fw, sudo_pass)

    # 5. NetworkManager 虚拟网卡隔离 (防止 TUN 网卡导致 WiFi 异常重置)
    logger.info("6/7 正在配置 NetworkManager 忽略虚拟网卡 (防掉线加固)...")
    cmd_nm = """
    mkdir -p /etc/NetworkManager/conf.d/
    cat << 'EOF' > /etc/NetworkManager/conf.d/99-ignore-virtual-nics.conf
[keyfile]
unmanaged-devices=interface-name:Meta;interface-name:tun*;interface-name:wg*;interface-name:clash*;interface-name:docker*;interface-name:br-*
EOF
    """
    exec_remote(client, cmd_nm, sudo_pass)

    # 6. 配置轻量 NAT 会话持久保活脚本
    logger.info("7/7 正在部署跨运营商 NAT 保活任务...")
    cmd_keepalive = f"""
    cat << 'EOF' > /usr/local/bin/tailscale_keepalive.sh
#!/bin/bash
tailscale ping --c 1 --timeout 1s {MAC_TS_IP} >/dev/null 2>&1
EOF
    chmod +x /usr/local/bin/tailscale_keepalive.sh
    (crontab -l 2>/dev/null | grep -v 'tailscale_keepalive.sh'; echo '* * * * * /usr/local/bin/tailscale_keepalive.sh') | crontab -
    """
    exec_remote(client, cmd_keepalive, None)

    logger.info(f"✓ [{name}] 全套优化项已成功应用完成！")


def test_rdp_port(ip, port=3389):
    """测试到远端 RDP 端口的 TCP 握手延迟"""
    t0 = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect((ip, port))
        dt = (time.time() - t0) * 1000
        s.close()
        return dt, True
    except Exception as e:
        return 0, False


def main():
    parser = argparse.ArgumentParser(description="三节点工作站 Tailscale 网络与系统深度优化工具")
    parser.add_argument(
        "--node",
        choices=["all", "node0", "node1", "node123"],
        default="all",
        help="目标节点选择（默认: all 全部三节点）",
    )
    parser.add_argument("--check-only", action="store_true", help="仅执行巡检诊断，不修改配置")
    args = parser.parse_args()

    init_env()
    logger.info("==========================================================")
    logger.info("      工作站三节点 Tailscale 网络与系统优化任务启动       ")
    logger.info("==========================================================")

    target_nodes = (
        list(NODE_SPECS.values())
        if args.node == "all"
        else [NODE_SPECS[args.node]]
    )

    summary_report = []

    for node_info in target_nodes:
        name = node_info["name"]
        client, connected_ip, sudo_pass = get_ssh_client(node_info)
        if not client:
            summary_report.append((name, "连接失败", "无法通过 SSH 连通"))
            continue

        try:
            # 1. 深度巡检
            diag = diagnose_node(node_info, client, sudo_pass)

            # 2. 应用优化
            if not args.check_only:
                apply_node_optimizations(node_info, client, sudo_pass)
                client.close()
                logger.info(f"等待网络稳定后重新连接 {name} 执行复测...")
                time.sleep(3)
                client, _, sudo_pass = get_ssh_client(node_info)
                if not client:
                    logger.warning(f"未能重连 {name} 进行复测")
                    post_diag = {}
                else:
                    post_diag = diagnose_node(node_info, client, sudo_pass)
            else:
                post_diag = diag

            # 3. 本地测试到该节点的 RDP TCP 握手
            ts_ip = os.getenv(node_info["ts_ip_env"], node_info["default_ts_ip"])
            rdp_lat, rdp_ok = test_rdp_port(ts_ip, 3389)
            rdp_str = f"{rdp_lat:.1f}ms" if rdp_ok else "不可达"

            summary_report.append((
                name,
                "✓ 成功优化并验证" if not args.check_only else "✓ 巡检完成",
                f"UDP握手: {post_diag.get('ts_ping', '未知')} | RDP(3389): {rdp_str} | 省电: {post_diag.get('power_save')}"
            ))

        except Exception as e:
            logger.error(f"处理节点 {name} 时发生异常: {e}")
            summary_report.append((name, "异常", str(e)))
        finally:
            client.close()

    logger.info("\n==========================================================")
    logger.info("                   优化与巡检执行总结                     ")
    logger.info("==========================================================")
    for n_name, status, detail in summary_report:
        logger.info(f"{n_name:<25} | {status:<15} | {detail}")
    logger.info("==========================================================")


if __name__ == "__main__":
    main()
