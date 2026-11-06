#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek 双机集群服务状态检查脚本
检查工作站 (rank0) 和 Node1 (rank1) 的内存、GPU、vLLM 容器、LiteLLM 服务及守护进程状态。
"""

import os
import sys

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env, ROOT_DIR

import paramiko

logger = setup_logger("ClusterStatus")


def get_ssh_client(host, user, password, timeout=10):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=22, username=user, password=password, timeout=timeout)
    return client


def run_command(client, cmd):
    logger.debug(f"执行命令: {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode("utf-8").strip()
    err = stderr.read().decode("utf-8").strip()
    return out, err


def main():
    init_env()

    host = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user = os.getenv("SSH_NODE0_USER", "winbot")
    password = os.getenv("SSH_NODE0_PASS", "winbot")

    logger.info("=" * 60)
    logger.info(f"正在连接工作站 Node0 ({user}@{host})...")
    client = get_ssh_client(host, user, password)

    try:
        # 1. 工作站内存与负载
        mem_out, _ = run_command(client, "free -h")
        logger.info(f"[工作站 Node0] 内存状态:\n{mem_out}")

        # 2. 工作站 GPU 显存
        gpu_out, _ = run_command(client, "nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free --format=csv,noheader")
        logger.info(f"[工作站 Node0] GPU 显存:\n{gpu_out}")

        # 3. 工作站 Docker 容器
        docker_out, _ = run_command(client, "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'")
        logger.info(f"[工作站 Node0] 运行中容器:\n{docker_out if docker_out else '无运行中容器'}")

        # 4. 工作站 systemd 服务状态
        services = ["vllm-watchdog.timer", "vllm-watchdog.service", "litellm.service", "litellm-https.service"]
        for svc in services:
            svc_status, _ = run_command(client, f"systemctl is-active {svc} 2>/dev/null || echo inactive")
            svc_enabled, _ = run_command(client, f"systemctl is-enabled {svc} 2>/dev/null || echo disabled")
            logger.info(f"[工作站 Node0] 服务 {svc}: active={svc_status}, enabled={svc_enabled}")

        # 5. 检查维护开关
        maint_out, _ = run_command(client, "test -f /home/winbot/.vllm_maintenance && echo '存在' || echo '不存在'")
        logger.info(f"[工作站 Node0] 维护标记 (/home/winbot/.vllm_maintenance): {maint_out}")

        # 6. 通过工作站检查 Node1 (rank1)
        logger.info("-" * 60)
        logger.info("正在通过工作站连接 Node1 (192.168.100.11 / 172.19.49.159)...")
        node1_cmds = (
            "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new dgxdeploy@192.168.100.11 "
            "\"free -h && echo '---' && "
            "nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free --format=csv,noheader && echo '---' && "
            "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'\""
        )
        node1_out, node1_err = run_command(client, node1_cmds)
        if node1_out:
            logger.info(f"[Node1 rank1] 状态输出:\n{node1_out}")
        if node1_err:
            logger.warning(f"[Node1 rank1] stderr:\n{node1_err}")

    finally:
        client.close()
        logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
