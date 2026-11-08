#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/optimize_tailscale_proxy.py - 本地代理分流与 Tailscale 直连优化脚本

功能：
1. 自动分析并优化本地 macOS 系统代理的 Bypass Domains（绕过代理列表），将 Tailscale 专用网段（100.64.0.0/10）、工作站公网 IP、Tailscale 域名彻底排除在代理之外；
2. 深度注入本地 0dcloud 代理客户端的偏好设置与 Clash Meta 内核配置，增加 DIRECT（直连）规则与 Fake-IP 过滤，彻底避免 UDP 打洞报文被代理接管；
3. 将直连规则同步写入 0dcloud SQLite 规则映射库，确保配置持久生效；
4. 诊断并测试 Tailscale 到工作站（100.67.107.19）的直接 P2P 连通性、延迟与丢包率。
"""

import os
import sys
import json
import sqlite3
import argparse
import subprocess

# 引入项目通用环境引导模块（确保使用 .venv 并加载 .env）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

logger = setup_logger("TailscaleProxyOptimizer")

# 需要直连与绕过代理的关键条目定义
TAILSCALE_BYPASS_DOMAINS = [
    "100.*",
    "59.49.101.*",
    "*.ts.net",
    "*.tailscale.com",
]

TAILSCALE_FAKE_IP_FILTERS = [
    "*.ts.net",
    "+.ts.net",
    "*.tailscale.com",
    "+.tailscale.com",
]

TAILSCALE_CLASH_RULES = [
    "IP-CIDR,100.64.0.0/10,DIRECT,no-resolve",
    "IP-CIDR,59.49.101.0/24,DIRECT,no-resolve",
    "DOMAIN-SUFFIX,ts.net,DIRECT",
    "DOMAIN-SUFFIX,tailscale.com,DIRECT",
    "DOMAIN-KEYWORD,tailscale,DIRECT",
    "PROCESS-NAME,tailscaled,DIRECT",
]


def optimize_macos_system_proxy(service_name="Wi-Fi"):
    """优化 macOS 系统代理绕过域名列表"""
    logger.info(f"正在检查网络接口 [{service_name}] 的系统代理绕过配置...")
    try:
        cmd_get = ["networksetup", "-getproxybypassdomains", service_name]
        res = subprocess.run(cmd_get, capture_output=True, text=True, check=True)
        current_domains = [d.strip() for d in res.stdout.splitlines() if d.strip()]
    except Exception as e:
        logger.warning(f"获取接口 [{service_name}] 代理列表失败: {e}，尝试检测可用接口...")
        res = subprocess.run(["networksetup", "-listallnetworkservices"], capture_output=True, text=True)
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip() and not line.startswith("An asterisk")]
        if not lines:
            logger.error("未找到有效的网络接口！")
            return False
        service_name = lines[0]
        cmd_get = ["networksetup", "-getproxybypassdomains", service_name]
        res = subprocess.run(cmd_get, capture_output=True, text=True)
        current_domains = [d.strip() for d in res.stdout.splitlines() if d.strip()]

    logger.info(f"当前 [{service_name}] 代理绕过名单包含 {len(current_domains)} 个条目")
    updated_domains = list(current_domains)
    added = []
    for item in TAILSCALE_BYPASS_DOMAINS:
        if item not in updated_domains:
            updated_domains.append(item)
            added.append(item)

    if added:
        logger.info(f"正在向 [{service_name}] 添加 Tailscale 绕过条目: {added}")
        cmd_set = ["networksetup", "-setproxybypassdomains", service_name] + updated_domains
        subprocess.run(cmd_set, check=True)
        logger.info(f"[{service_name}] 系统代理绕过列表已成功更新！")
    else:
        logger.info(f"[{service_name}] 系统代理已包含所有必要绕过条目，无需重复添加。")

    return True


def optimize_0dcloud_config():
    """优化 0dcloud 的偏好设置与 Clash Meta 配置"""
    logger.info("正在读取 0dcloud (com.odcloud.app) 偏好配置...")
    try:
        out = subprocess.check_output(["defaults", "read", "com.odcloud.app", "flutter.config"]).decode("utf-8")
        cfg = json.loads(out)
    except Exception as e:
        logger.warning(f"未读取到 0dcloud 偏好配置 (或未安装/未运行): {e}")
        return False

    changed = False

    # 1. 优化 networkProps.bypassDomain
    net_props = cfg.setdefault("networkProps", {})
    bypass_list = net_props.setdefault("bypassDomain", [])
    for item in TAILSCALE_BYPASS_DOMAINS:
        if item not in bypass_list:
            bypass_list.append(item)
            changed = True
            logger.info(f"0dcloud networkProps.bypassDomain 添加: {item}")

    # 2. 优化 patchClashConfig
    patch = cfg.setdefault("patchClashConfig", {})

    # 2.1 增加 fake-ip-filter
    dns_cfg = patch.setdefault("dns", {})
    fake_ip_filters = dns_cfg.setdefault("fake-ip-filter", [])
    for item in TAILSCALE_FAKE_IP_FILTERS:
        if item not in fake_ip_filters:
            fake_ip_filters.append(item)
            changed = True
            logger.info(f"0dcloud fake-ip-filter 添加: {item}")

    # 2.2 增加 rule 直连规则 (插在最顶层)
    rules = patch.setdefault("rule", [])
    # 逆序插入到最前面以保持优先级顺序
    for r in reversed(TAILSCALE_CLASH_RULES):
        if r not in rules:
            rules.insert(0, r)
            changed = True
            logger.info(f"0dcloud Clash 规则注入最高优先直连: {r}")

    if changed:
        new_json_str = json.dumps(cfg, ensure_ascii=False)
        subprocess.run(["defaults", "write", "com.odcloud.app", "flutter.config", "-string", new_json_str], check=True)
        logger.info("0dcloud 偏好配置已成功持久化更新！")
    else:
        logger.info("0dcloud 偏好配置中已包含完整规则，无需修改。")

    return True


import uuid

def optimize_0dcloud_sqlite():
    """将直连规则同步写入 0dcloud SQLite 数据库"""
    db_path = os.path.expanduser("~/Library/Application Support/com.odcloud.app/database.sqlite")
    if not os.path.isfile(db_path):
        logger.info(f"未找到 0dcloud 数据库文件 ({db_path})，跳过数据库直写。")
        return False

    logger.info(f"正在更新 0dcloud 本地数据库 ({db_path})...")
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # 获取当前 profile_id
        cur.execute("SELECT id FROM profiles ORDER BY `order` ASC LIMIT 1;")
        row = cur.fetchone()
        profile_id = row[0] if row else 376954164158140416

        # 检查并插入规则
        for idx, rule in enumerate(TAILSCALE_CLASH_RULES):
            cur.execute("SELECT id FROM rules WHERE value = ?;", (rule,))
            res = cur.fetchone()
            if res:
                rule_id = res[0]
            else:
                cur.execute("INSERT INTO rules (value) VALUES (?);", (rule,))
                rule_id = cur.lastrowid
                logger.info(f"数据库 rules 表新增规则 [{rule_id}]: {rule}")

            # 检查是否已关联
            cur.execute(
                "SELECT id FROM profile_rule_mapping WHERE profile_id = ? AND rule_id = ?;",
                (profile_id, rule_id),
            )
            mapping = cur.fetchone()
            if not mapping:
                map_id = uuid.uuid4().hex
                cur.execute(
                    "INSERT INTO profile_rule_mapping (id, profile_id, rule_id, scene, `order`) VALUES (?, ?, ?, ?, ?);",
                    (map_id, profile_id, rule_id, "direct", str(idx)),
                )
                logger.info(f"关联规则 [{rule_id}] 至 Profile [{profile_id}]，映射ID: {map_id}，优先级: {idx}")

        conn.commit()
        conn.close()
        logger.info("0dcloud SQLite 数据库更新完成！")
        return True
    except Exception as e:
        logger.error(f"更新 0dcloud 数据库时出错: {e}")
        return False


def test_tailscale_connection(target_ip="100.67.107.19"):
    """测试当前 Tailscale 连接状态与延迟性能"""
    logger.info(f"=== 开始测试工作站 Tailscale 节点 ({target_ip}) 连通状态 ===")

    # 1. Tailscale status 查询对端连接模式
    try:
        out = subprocess.check_output(["tailscale", "status"]).decode("utf-8")
        target_line = ""
        for line in out.splitlines():
            if target_ip in line:
                target_line = line.strip()
                break
        if target_line:
            logger.info(f"Tailscale 节点拓扑状态: {target_line}")
            if "direct" in target_line:
                logger.info("✓ 成功检测到 Direct（P2P 直连通道建立）！")
            elif "relay" in target_line:
                logger.warning("⚠ 当前处于 Relay（中继代理模式），可能由于 NAT 打洞尚未完成或被拦截！")
        else:
            logger.warning(f"未能从 tailscale status 找到目标 IP {target_ip}")
    except Exception as e:
        logger.error(f"执行 tailscale status 失败: {e}")

    # 2. 系统 ICMP Ping 测试
    logger.info(f"正在进行 ICMP ping 连续 5 次探测...")
    try:
        res = subprocess.run(["ping", "-c", "5", "-W", "2000", target_ip], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if "round-trip" in line or "statistics" in line or "packets transmitted" in line:
                logger.info(f"[Ping] {line}")
    except Exception as e:
        logger.error(f"ICMP ping 执行出错: {e}")

    # 3. Tailscale Ping 握手测试
    logger.info(f"正在进行 Tailscale Peer UDP 握手测试...")
    try:
        res = subprocess.run(["tailscale", "ping", "--c", "2", "--timeout", "3s", target_ip], capture_output=True, text=True)
        if res.stdout:
            for line in res.stdout.splitlines():
                if line.strip():
                    logger.info(f"[TS-Ping] {line.strip()}")
        if res.stderr:
            for line in res.stderr.splitlines():
                if line.strip():
                    logger.warning(f"[TS-Ping-Err] {line.strip()}")
    except Exception as e:
        logger.error(f"Tailscale ping 执行出错: {e}")


def main():
    parser = argparse.ArgumentParser(description="Tailscale 本地代理分流优化与直连加速工具")
    parser.add_argument("--target-ip", default="100.67.107.19", help="工作站 Tailscale IP（默认: 100.67.107.19）")
    parser.add_argument("--interface", default="Wi-Fi", help="macOS 主要网络接口名称（默认: Wi-Fi）")
    parser.add_argument("--skip-test", action="store_true", help="跳过连接与延迟测试")
    args = parser.parse_args()

    init_env()
    logger.info("==========================================")
    logger.info("   Tailscale 本地代理分流与直连优化任务启动   ")
    logger.info("==========================================")

    # 1. 优化 macOS 系统代理绕过名单
    optimize_macos_system_proxy(args.interface)

    # 2. 优化 0dcloud 客户端偏好与规则注入
    optimize_0dcloud_config()

    # 3. 同步写入 0dcloud SQLite 数据库
    optimize_0dcloud_sqlite()

    # 4. 执行连通性与链路测试
    if not args.skip_test:
        test_tailscale_connection(args.target_ip)

    logger.info("优化任务执行完毕！")


if __name__ == "__main__":
    main()
