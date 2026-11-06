#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek-V4-Flash 集群服务恢复与启动脚本

启动顺序与规则 (遵循 docs/LiteLLM网关与双机集群配置说明_20260910.md):
1. 确保 RoCE GID 正确配置;
2. 严格按顺序启动: 先启动 Node1 (rank1)，再启动工作站 (rank0);
3. 检查 vLLM 健康接口 (http://127.0.0.1:18090/health);
4. 启动 LiteLLM 网关服务 (litellm.service, litellm-https.service);
5. 恢复并启用 vllm-watchdog 守护服务;
6. 清理维护标记 (/home/winbot/.vllm_maintenance)。
"""

import os
import sys
import time

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env, ROOT_DIR

import paramiko

logger = setup_logger("ClusterStart")


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
    init_env()

    node0_ip = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    node0_user = os.getenv("SSH_NODE0_USER", "winbot")
    node0_pass = os.getenv("SSH_NODE0_PASS", "winbot")
    node1_fabric_ip = os.getenv("NODE1_FABRIC_IP", "192.168.100.11")
    node1_user = os.getenv("SSH_OPS_USER", "dgxdeploy")

    logger.info("=" * 70)
    logger.info("准备启动 DeepSeek 双机集群 (Node1 先启动 -> 工作站后启动 -> LiteLLM 网关)")
    logger.info("=" * 70)

    client0 = get_ssh_client(node0_ip, node0_user, node0_pass)

    try:
        # 步骤 1: 启动 Node1 (rank1)
        node1_start_cmd = (
            f"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new {node1_user}@{node1_fabric_ip} "
            "'cd /home/dgxdeploy/deepseek-v4-vllm/upstream && docker compose --env-file .env.canary128 up -d'"
        )
        exec_remote(client0, node1_start_cmd, desc="启动 Node1 (rank1) vLLM 容器")

        # 步骤 2: 启动工作站 (rank0)
        node0_start_cmd = "cd /home/winbot/deepseek-v4-vllm/upstream && docker compose --env-file .env.canary128 up -d"
        exec_remote(client0, node0_start_cmd, desc="启动工作站 Node0 (rank0) vLLM 容器")

        # 步骤 3: 轮询等待健康检查
        logger.info("等待 vLLM API 端口健康就绪 (http://127.0.0.1:18090/health)...")
        ready = False
        for attempt in range(1, 30):
            _, code, _ = exec_remote(
                client0,
                "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18090/health || echo 000",
                check=False,
            )
            if code == "200":
                ready = True
                logger.info(f"vLLM 健康检查通过 (第 {attempt} 次检测，HTTP 200)！")
                break
            time.sleep(5)

        if not ready:
            logger.warning("vLLM 尚未完全就绪，请稍后检查容器日志。")

        # 步骤 4: 启动 LiteLLM
        exec_remote(client0, "sudo systemctl start litellm.service litellm-https.service", desc="启动 LiteLLM 网关服务")
        exec_remote(client0, "sudo systemctl enable litellm.service litellm-https.service", desc="恢复 LiteLLM 自启")

        # 步骤 5: 移除维护标记并启动 watchdog
        exec_remote(client0, "rm -f /home/winbot/.vllm_maintenance", desc="清除维护标记")
        exec_remote(client0, "sudo systemctl enable --now vllm-watchdog.timer", desc="启动并恢复 watchdog 定时器")

        logger.info("集群启动流程完成！")

    except Exception as e:
        logger.error(f"启动失败: {e}", exc_info=True)
        return 1
    finally:
        client0.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
