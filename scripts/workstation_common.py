#!/usr/bin/env python3
"""双工作站远程操作公共模块：连接、命令执行、批量节点构建。

所有敏感配置统一从项目根目录 .env 读取，禁止在代码中硬编码。
"""

from __future__ import annotations

import logging
import os
import posixpath
import shlex
from dataclasses import dataclass
from pathlib import Path

import paramiko
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"


def init_logging(name: str, verbose: bool = False) -> logging.Logger:
    """初始化统一日志格式。"""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=LOG_FORMAT)
    return logging.getLogger(name)


@dataclass
class Node:
    """工作站节点连接信息。"""

    key: str
    label: str
    host: str
    user: str
    password: str
    client: paramiko.SSHClient | None = None
    sftp: paramiko.SFTPClient | None = None
    home: str = ""

    def connect(self, timeout: int = 15, retries: int = 2) -> None:
        import time

        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=self.host,
                    port=22,
                    username=self.user,
                    password=self.password,
                    timeout=timeout,
                    banner_timeout=timeout,
                    auth_timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                transport = client.get_transport()
                if transport is not None:
                    transport.set_keepalive(30)
                self.client = client
                self.sftp = client.open_sftp()
                self.home = self.sftp.normalize(".")
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < retries:
                    time.sleep(2)
        raise ConnectionError(f"{self.label} ({self.host}) 无法连接：{last_error}")

    def close(self) -> None:
        if self.sftp is not None:
            self.sftp.close()
            self.sftp = None
        if self.client is not None:
            self.client.close()
            self.client = None

    def run(self, command: str, timeout: int = 120, sudo: bool = False, check: bool = True) -> str:
        """执行远端命令；sudo 时从 .env 口令自动应答。"""
        assert self.client is not None
        wrapped = f"bash -lc {shlex.quote(command)}"
        if sudo:
            wrapped = f"sudo -S -p '' {wrapped}"
        stdin, stdout, stderr = self.client.exec_command(wrapped, timeout=timeout)
        if sudo:
            stdin.write(self.password + "\n")
            stdin.flush()
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        if check and exit_code != 0:
            raise RuntimeError(f"命令失败（{exit_code}）：{command}\n{out}\n{err}")
        return out

    def write_text(self, remote_path: str, content: str, mode: int = 0o600) -> None:
        assert self.sftp is not None
        self.run(f"mkdir -p {shlex.quote(posixpath.dirname(remote_path))}")
        with self.sftp.open(remote_path, "w") as handle:
            handle.write(content.encode("utf-8"))
        self.sftp.chmod(remote_path, mode)


def build_nodes(selected: str, node1_account: str = "ops") -> list[Node]:
    """构建节点列表；Node1 可选择 ops(dgxdeploy) 或 his_test 账号。"""
    load_dotenv(PROJECT_ROOT / ".env")
    if node1_account == "his_test":
        node1_user = os.getenv("SSH_NODE1_HIS_USER", "his_test")
        node1_pass = os.getenv("SSH_NODE1_HIS_PASS", "")
        node1_label = "Node1/159(his_test)"
    else:
        node1_user = os.getenv("SSH_OPS_USER", "")
        node1_pass = os.getenv("SSH_OPS_PASS", "")
        node1_label = "Node1/159"

    definitions = {
        "node0": Node(
            key="node0",
            label="Node0/70",
            host=os.getenv("NODE0_MGMT_IP", ""),
            user=os.getenv("SSH_NODE0_USER", ""),
            password=os.getenv("SSH_NODE0_PASS", ""),
        ),
        "node1": Node(
            key="node1",
            label=node1_label,
            host=os.getenv("NODE1_MGMT_IP", ""),
            user=node1_user,
            password=node1_pass,
        ),
    }
    keys = ["node0", "node1"] if selected == "all" else [selected]
    nodes = []
    for key in keys:
        node = definitions[key]
        if not node.host or not node.user:
            raise SystemExit(f"缺少 {key} 的连接配置，请检查 .env")
        nodes.append(node)
    return nodes
