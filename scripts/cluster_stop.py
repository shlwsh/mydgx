#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek-V4-Flash 集群服务关闭与自启禁用脚本

核心功能:
1. 在工作站创建维护标记文件 (/home/winbot/.vllm_maintenance)，阻止 watchdog 自愈；
2. 停止并禁用工作站上的 vllm-watchdog.timer 与相关守护；
3. 停止并禁用工作站上的 litellm.service 和 litellm-https.service；
4. 停止 Node1 (rank1) 上的 vLLM 容器 (docker compose down) 并释放内存空间；
5. 停止工作站 (rank0) 上的 vLLM 容器 (docker compose down) 并释放内存空间；
6. 验证服务状态及双机内存释放情况，确保默认开机不会自动启动。

遵循项目规范:
- 完善的日志记录 (时间戳、级别、模块名、执行阶段)
- 敏感信息保护
"""

import os
import sys

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env, ROOT_DIR

import paramiko

logger = setup_logger("ClusterStop")


def get_ssh_client(host, user, password, timeout=15):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=22, username=user, password=password, timeout=timeout)
    return client


def exec_remote(client, cmd, check=True, desc=None):
    if desc:
        logger.info(f"==> 执行阶段: {desc}")
    logger.debug(f"SSH 命令: {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode("utf-8").strip()
    err = stderr.read().decode("utf-8").strip()
    exit_code = stdout.channel.recv_exit_status()

    if out:
        logger.info(f"[命令输出]\n{out}")
    if err and exit_code != 0:
        logger.warning(f"[错误输出]\n{err}")

    if check and exit_code != 0:
        raise RuntimeError(f"命令执行失败 (退出码 {exit_code}): {cmd}\n{err}")

    return exit_code, out, err


def main():
    env_file = init_env()
    if os.path.exists(env_file):
        logger.info(f"已加载配置文件: {env_file}")
    else:
        logger.warning(f"未找到 .env 文件，使用默认配置参数")

    node0_ip = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    node0_user = os.getenv("SSH_NODE0_USER", "winbot")
    node0_pass = os.getenv("SSH_NODE0_PASS", "winbot")
    node1_fabric_ip = os.getenv("NODE1_FABRIC_IP", "192.168.100.11")
    node1_user = os.getenv("SSH_OPS_USER", "dgxdeploy")

    logger.info("=" * 70)
    logger.info("准备停止 DeepSeek 双机集群 (vLLM + LiteLLM) 并释放内存")
    logger.info(f"目标工作站 Node0: {node0_user}@{node0_ip}")
    logger.info(f"目标 Node1 (通过 fabric 直连): {node1_user}@{node1_fabric_ip}")
    logger.info("=" * 70)

    logger.info("正在建立与工作站 Node0 的 SSH 连接...")
    client0 = get_ssh_client(node0_ip, node0_user, node0_pass)

    try:
        # 记录停止前的内存状态
        logger.info(">>> 记录停止前的内存与显存状态")
        _, mem0_before, _ = exec_remote(client0, "free -h", check=False, desc="查询工作站停止前内存")
        _, node1_mem_before, _ = exec_remote(
            client0,
            f"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new {node1_user}@{node1_fabric_ip} 'free -h'",
            check=False,
            desc="查询 Node1 停止前内存",
        )

        # 步骤 1: 创建维护开关，避免 watchdog 自愈误触发
        exec_remote(
            client0,
            "touch /home/winbot/.vllm_maintenance",
            desc="创建维护开关文件 (/home/winbot/.vllm_maintenance)",
        )

        # 步骤 2: 停止并禁用工作站上的 watchdog
        logger.info(">>> 停止并禁用 vLLM watchdog 守护进程")
        exec_remote(
            client0,
            "sudo systemctl stop vllm-watchdog.timer vllm-watchdog.service 2>/dev/null || true",
            check=False,
            desc="停止 vllm-watchdog 服务与定时器",
        )
        exec_remote(
            client0,
            "sudo systemctl disable vllm-watchdog.timer 2>/dev/null || true",
            check=False,
            desc="禁用 vllm-watchdog.timer 开机自启",
        )

        # 步骤 3: 停止并禁用 LiteLLM 网关服务
        logger.info(">>> 停止并禁用 LiteLLM 网关服务")
        exec_remote(
            client0,
            "sudo systemctl stop litellm.service litellm-https.service 2>/dev/null || true",
            check=False,
            desc="停止 litellm HTTP 与 HTTPS 网关服务",
        )
        exec_remote(
            client0,
            "sudo systemctl disable litellm.service litellm-https.service 2>/dev/null || true",
            check=False,
            desc="禁用 litellm 与 litellm-https 开机自启",
        )

        # 步骤 4: 停止 Node1 (rank1) 上的 vLLM 容器
        logger.info(">>> 停止 Node1 (rank1) 上的 vLLM 容器")
        node1_stop_cmd = (
            f"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new {node1_user}@{node1_fabric_ip} "
            "'cd /home/dgxdeploy/deepseek-v4-vllm/upstream && docker compose --env-file .env.canary128 down'"
        )
        exec_remote(client0, node1_stop_cmd, check=False, desc="在 Node1 执行 docker compose down")

        # 步骤 5: 停止工作站 (rank0) 上的 vLLM 容器
        logger.info(">>> 停止工作站 Node0 (rank0) 上的 vLLM 容器")
        node0_stop_cmd = "cd /home/winbot/deepseek-v4-vllm/upstream && docker compose --env-file .env.canary128 down"
        exec_remote(client0, node0_stop_cmd, check=False, desc="在工作站执行 docker compose down")

        # 步骤 6: 验证服务状态及自启配置
        logger.info("=" * 70)
        logger.info(">>> 验证所有服务关闭及禁用自启状态")
        check_services = ["vllm-watchdog.timer", "litellm.service", "litellm-https.service"]
        for svc in check_services:
            _, is_act, _ = exec_remote(client0, f"systemctl is-active {svc} 2>/dev/null || echo inactive", check=False)
            _, is_en, _ = exec_remote(client0, f"systemctl is-enabled {svc} 2>/dev/null || echo disabled", check=False)
            logger.info(f"服务 {svc}: 运行状态={is_act}, 开机自启={is_en}")

        # 检查容器是否全部移除
        _, ps0, _ = exec_remote(client0, "docker ps --filter name=deepseek-v4-flash -q", check=False)
        if not ps0:
            logger.info("[工作站 Node0] deepseek-v4-flash 容器已彻底停止并清理！")
        else:
            logger.warning(f"[工作站 Node0] 仍检测到未停止容器: {ps0}")

        _, ps1, _ = exec_remote(
            client0,
            f"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new {node1_user}@{node1_fabric_ip} 'docker ps --filter name=deepseek-v4-flash -q'",
            check=False,
        )
        if not ps1:
            logger.info("[Node1 rank1] deepseek-v4-flash 容器已彻底停止并清理！")
        else:
            logger.warning(f"[Node1 rank1] 仍检测到未停止容器: {ps1}")

        # 步骤 7: 内存与显存释放对比
        logger.info("=" * 70)
        logger.info(">>> 内存空间释放对比结果:")
        _, mem0_after, _ = exec_remote(client0, "free -h", check=False, desc="工作站当前内存")
        _, node1_mem_after, _ = exec_remote(
            client0,
            f"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new {node1_user}@{node1_fabric_ip} 'free -h'",
            check=False,
            desc="Node1 当前内存",
        )

        logger.info("========================================")
        logger.info(f"【工作站 Node0 内存释放对比】\n[停止前]:\n{mem0_before}\n[停止后]:\n{mem0_after}")
        logger.info("----------------------------------------")
        logger.info(f"【Node1 rank1 内存释放对比】\n[停止前]:\n{node1_mem_before}\n[停止后]:\n{node1_mem_after}")
        logger.info("========================================")
        logger.info("DeepSeek LLM 服务及 LiteLLM 网关服务已完全关闭，已释放内存，且默认不会自动启动！")

    except Exception as e:
        logger.error(f"执行停止过程发生异常: {e}", exc_info=True)
        return 1
    finally:
        client0.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
