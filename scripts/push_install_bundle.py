#!/usr/bin/env python3
"""将本地目录内容批量推送至双工作站的「下载/install」目录。

通过 SSH/SFTP（paramiko）把源目录下的所有文件与子目录递归同步到目标节点，
支持断点续传（同名同大小文件自动跳过）、失败重试与结果校验。

示例：
    # 推送 /home/bot/work/cursor 到双机 ~/Downloads/install
    .venv/bin/python scripts/push_install_bundle.py --src /home/bot/work/cursor

    # 只推送 Node0，并指定远端子目录
    .venv/bin/python scripts/push_install_bundle.py --src /home/bot/work/cursor --node node0

    # 仅比对远端与本地差异，不上传
    .venv/bin/python scripts/push_install_bundle.py --src /home/bot/work/cursor --check
"""

from __future__ import annotations

import argparse
import logging
import os
import posixpath
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import paramiko
from dotenv import load_dotenv

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("push_install_bundle")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = "install"
DOWNLOAD_DIR_CANDIDATES = ("Downloads", "下载")
CHUNK_SIZE = 256 * 1024


@dataclass
class Node:
    """工作站节点连接信息。"""

    key: str
    label: str
    host: str
    user: str
    password: str
    home: str = ""
    client: paramiko.SSHClient | None = field(default=None, repr=False)
    sftp: paramiko.SFTPClient | None = field(default=None, repr=False)

    def connect(self, timeout: int = 15, retries: int = 2) -> None:
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
                sftp = client.open_sftp()
                self.client, self.sftp = client, sftp
                self.home = sftp.normalize(".")
                logger.info("[%s] SSH 连接成功 %s@%s，家目录 %s", self.label, self.user, self.host, self.home)
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("[%s] 第 %d/%d 次连接失败：%s", self.label, attempt, retries, exc)
                time.sleep(2)
        raise ConnectionError(f"{self.label} ({self.host}) 无法连接：{last_error}")

    def close(self) -> None:
        if self.sftp is not None:
            self.sftp.close()
        if self.client is not None:
            self.client.close()

    def resolve_download_dir(self) -> str:
        """优先使用 ~/Downloads，其次中文「下载」目录。"""
        assert self.sftp is not None
        for name in DOWNLOAD_DIR_CANDIDATES:
            path = posixpath.join(self.home, name)
            try:
                if stat.S_ISDIR(self.sftp.stat(path).st_mode):
                    return path
            except FileNotFoundError:
                continue
        fallback = posixpath.join(self.home, "Downloads")
        logger.warning("[%s] 未找到下载目录，使用 %s", self.label, fallback)
        return fallback

    def mkdirs(self, remote_dir: str) -> None:
        assert self.sftp is not None
        parts = remote_dir.strip("/").split("/")
        current = "/" if remote_dir.startswith("/") else ""
        for part in parts:
            current = posixpath.join(current, part)
            try:
                if stat.S_ISDIR(self.sftp.stat(current).st_mode):
                    continue
            except FileNotFoundError:
                pass
            try:
                self.sftp.mkdir(current)
                logger.debug("[%s] 创建目录 %s", self.label, current)
            except OSError:
                # 并发或已存在时忽略
                if not stat.S_ISDIR(self.sftp.stat(current).st_mode):
                    raise

    def remote_size(self, path: str) -> int | None:
        assert self.sftp is not None
        try:
            return self.sftp.stat(path).st_size
        except FileNotFoundError:
            return None


def collect_files(src: Path) -> list[tuple[Path, str]]:
    """返回 (本地绝对路径, 相对路径) 列表，保持稳定顺序。"""
    entries: list[tuple[Path, str]] = []
    for root, dirnames, filenames in os.walk(src):
        dirnames.sort()
        for name in sorted(filenames):
            local = Path(root) / name
            entries.append((local, str(local.relative_to(src)).replace(os.sep, "/")))
    return entries


def ensure_remote_dirs(node: Node, remote_root: str, relative_dirs: Iterable[str]) -> None:
    for rel in sorted(relative_dirs):
        node.mkdirs(posixpath.join(remote_root, rel) if rel not in ("", ".") else remote_root)


def upload_file(node: Node, local: Path, remote: str) -> None:
    assert node.sftp is not None
    total = local.stat().st_size
    start = time.time()
    with local.open("rb") as source, node.sftp.open(remote, "wb") as target:
        target.set_pipelined(True)
        sent = 0
        last_report = 0.0
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            target.write(chunk)
            sent += len(chunk)
            now = time.time()
            if now - last_report >= 5 or sent == total:
                speed = sent / max(now - start, 1e-6) / 1024 / 1024
                logger.info(
                    "[%s] %s  %s / %s (%.1f%%)  %.2f MB/s",
                    node.label,
                    local.name,
                    human_size(sent),
                    human_size(total),
                    sent / total * 100 if total else 100.0,
                    speed,
                )
                last_report = now


def ensure_mode(node: Node, local: Path, remote: str) -> bool:
    """保持远端文件权限与本地一致（AppImage 需可执行，chrome-sandbox 需 setuid）。"""
    assert node.sftp is not None
    expected = stat.S_IMODE(local.stat().st_mode)
    try:
        actual = stat.S_IMODE(node.sftp.stat(remote).st_mode)
    except FileNotFoundError:
        return False
    if actual == expected:
        return False
    node.sftp.chmod(remote, expected)
    logger.info("[%s] 修正权限 %s：%s -> %s", node.label, posixpath.basename(remote), oct(actual), oct(expected))
    return True


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f}{unit}" if unit != "B" else f"{int(num)}B"
        num /= 1024
    return f"{num:.1f}TB"


def sync_node(node: Node, src: Path, dest_subdir: str, check_only: bool) -> dict[str, int]:
    """同步单个节点，返回统计信息。"""
    files = collect_files(src)
    dirs = {posixpath.dirname(rel) for _, rel in files}
    dirs |= {d for d in dirs if d}

    download_dir = node.resolve_download_dir()
    remote_root = posixpath.join(download_dir, *dest_subdir.strip("/").split("/"))
    logger.info("[%s] 目标目录：%s（共 %d 个文件，%s）", node.label, remote_root, len(files), human_size(sum(p.stat().st_size for p, _ in files)))
    node.mkdirs(remote_root)
    ensure_remote_dirs(node, remote_root, dirs)

    stats = {"uploaded": 0, "skipped": 0, "failed": 0, "bytes": 0, "chmod": 0}
    started = time.time()
    for index, (local, rel) in enumerate(files, 1):
        remote = posixpath.join(remote_root, rel)
        local_size = local.stat().st_size
        node.mkdirs(posixpath.dirname(remote))
        remote_size = node.remote_size(remote)
        if remote_size == local_size:
            expected_mode = stat.S_IMODE(local.stat().st_mode)
            actual_mode = stat.S_IMODE(node.sftp.stat(remote).st_mode)
            if check_only and actual_mode != expected_mode:
                logger.warning(
                    "[%s] (%d/%d) 权限不一致 %s（本地 %s，远端 %s）",
                    node.label, index, len(files), rel, oct(expected_mode), oct(actual_mode),
                )
                stats["failed"] += 1
                continue
            logger.info("[%s] (%d/%d) 跳过已同步 %s (%s)", node.label, index, len(files), rel, human_size(local_size))
            stats["skipped"] += 1
            if not check_only and ensure_mode(node, local, remote):
                stats["chmod"] += 1
            continue
        if check_only:
            logger.warning("[%s] (%d/%d) 缺失/大小不一致 %s（本地 %s，远端 %s）", node.label, index, len(files), rel, human_size(local_size), remote_size)
            stats["failed"] += 1
            continue
        logger.info("[%s] (%d/%d) 上传 %s (%s)", node.label, index, len(files), rel, human_size(local_size))
        try:
            upload_file(node, local, remote)
            uploaded_size = node.remote_size(remote)
            if uploaded_size != local_size:
                raise IOError(f"校验失败：远端 {uploaded_size} != 本地 {local_size}")
            stats["uploaded"] += 1
            stats["bytes"] += local_size
            if ensure_mode(node, local, remote):
                stats["chmod"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] 上传失败 %s：%s", node.label, rel, exc)
            stats["failed"] += 1

    elapsed = time.time() - started
    logger.info(
        "[%s] 同步完成：上传 %d，跳过 %d，修正权限 %d，失败 %d，传输 %s，耗时 %.1fs",
        node.label,
        stats["uploaded"],
        stats["skipped"],
        stats["chmod"],
        stats["failed"],
        human_size(stats["bytes"]),
        elapsed,
    )
    return stats


def build_nodes(selected: str, node1_account: str = "ops") -> list[Node]:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="将本地目录内容递归推送至工作站的下载/install 目录（支持断点续传与校验）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--src", required=True, type=Path, help="本地源目录")
    parser.add_argument("--dest-subdir", default=DEFAULT_DEST, help="相对下载目录的远端子目录")
    parser.add_argument("--node", choices=["all", "node0", "node1"], default="all", help="目标节点")
    parser.add_argument(
        "--node1-account",
        choices=["ops", "his_test"],
        default="ops",
        help="Node1 使用的 SSH 账号（ops=dgxdeploy，his_test=his_test；对 Node0 无影响）",
    )
    parser.add_argument("--check", action="store_true", help="仅比对差异，不上传")
    parser.add_argument("--keep-going", action="store_true", help="单节点失败时继续处理其他节点")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    if not args.src.is_dir():
        raise SystemExit(f"源目录不存在或不是目录：{args.src}")

    src = args.src.resolve()
    logger.info("源目录：%s（%s）", src, human_size(sum(p.stat().st_size for p, _ in collect_files(src))))

    results: dict[str, dict[str, int]] = {}
    failures: list[str] = []
    for node in build_nodes(args.node, args.node1_account):
        try:
            node.connect()
            try:
                results[node.label] = sync_node(node, src, args.dest_subdir, args.check)
            finally:
                node.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] 节点处理失败：%s", node.label, exc)
            failures.append(node.label)
            if not args.keep_going:
                break

    logger.info("==== 结果汇总 ====")
    for label, stats in results.items():
        state = "✅ 一致" if stats["failed"] == 0 and stats["uploaded"] == 0 and stats["skipped"] > 0 else "📦 已同步"
        if args.check:
            state = "✅ 一致" if stats["failed"] == 0 else "⚠️ 存在差异"
        logger.info("%s：%s（上传 %d / 跳过 %d / 修正权限 %d / 失败 %d）", label, state, stats["uploaded"], stats["skipped"], stats["chmod"], stats["failed"])
    if failures:
        logger.error("未完成节点：%s", "、".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
