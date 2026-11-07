#!/usr/bin/env python3
"""按《Cursor安装说明.md》在双工作站上解包安装 Cursor。

流程（与说明文档一致）：
    1. 校验 AppImage 架构与可执行权限；
    2. 在临时目录执行 ``--appimage-extract`` 解包；
    3. 将产物移动到 ``~/squashfs-root``；
    4. ``chrome-sandbox`` 设为 root 属主 + 4755（setuid）；
    5. 生成 ``~/.local/share/applications/cursor.desktop`` 并刷新菜单缓存。

示例：
    .venv/bin/python scripts/install_cursor.py --node all
    .venv/bin/python scripts/install_cursor.py --node node0 --check
    .venv/bin/python scripts/install_cursor.py --node all --force   # 覆盖旧安装（自动备份）
"""

from __future__ import annotations

import argparse
import logging
import os
import posixpath
import shlex
import sys
import time
from dataclasses import dataclass

import paramiko
from dotenv import load_dotenv

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("install_cursor")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_APPIMAGE = "Downloads/install/Cursor-3.24.18-aarch64.AppImage"
DEFAULT_PREFIX = "squashfs-root"

DESKTOP_TEMPLATE = """[Desktop Entry]
Name=Cursor
GenericName=Text Editor
Comment=AI-powered code editor
Exec={apprun} %F
Icon={icon}
Type=Application
Categories=Development;IDE;
MimeType=text/plain;inode/directory;
Terminal=false
StartupWMClass=Cursor
"""


@dataclass
class Node:
    key: str
    label: str
    host: str
    user: str
    password: str
    client: paramiko.SSHClient | None = None
    sftp: paramiko.SFTPClient | None = None
    home: str = ""

    def connect(self, timeout: int = 15) -> None:
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
        logger.info("[%s] 已连接 %s@%s（家目录 %s）", self.label, self.user, self.host, self.home)

    def close(self) -> None:
        if self.sftp is not None:
            self.sftp.close()
        if self.client is not None:
            self.client.close()

    def run(self, command: str, timeout: int = 300, sudo: bool = False) -> str:
        assert self.client is not None
        if sudo:
            command = f"sudo -S -p '' bash -lc {shlex.quote(command)}"
        else:
            command = f"bash -lc {shlex.quote(command)}"
        logger.debug("[%s] $ %s", self.label, command)
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        if sudo:
            stdin.write(self.password + "\n")
            stdin.flush()
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        if exit_code != 0:
            raise RuntimeError(f"命令失败（{exit_code}）：{command}\n{out}\n{err}")
        return out


def build_nodes(selected: str, node1_account: str = "ops") -> list[Node]:
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
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
    return [definitions[key] for key in keys]


def check_node(node: Node, prefix: str) -> bool:
    apprun = posixpath.join(node.home, prefix, "AppRun")
    sandbox = posixpath.join(node.home, prefix, "usr/share/cursor/chrome-sandbox")
    desktop = posixpath.join(node.home, ".local/share/applications/cursor.desktop")
    command = (
        f"test -x {shlex.quote(apprun)} && echo 'AppRun=OK' || echo 'AppRun=MISSING';"
        f"test -e {shlex.quote(desktop)} && echo 'desktop=OK' || echo 'desktop=MISSING';"
        f"stat -c '%U:%G:%a' {shlex.quote(sandbox)} 2>/dev/null || echo 'sandbox=MISSING'"
    )
    result = node.run(command)
    logger.info("[%s] 安装状态：\n%s", node.label, "\n".join("  " + line for line in result.splitlines()))
    installed = "AppRun=OK" in result and "desktop=OK" in result and "root:root:4755" in result
    logger.info("[%s] %s", node.label, "✅ 已正确安装" if installed else "⚠️ 未安装或配置不完整")
    return installed


def install_node(node: Node, appimage: str, prefix: str, force: bool) -> None:
    remote_appimage = appimage if appimage.startswith("/") else posixpath.join(node.home, appimage)
    remote_prefix = prefix if prefix.startswith("/") else posixpath.join(node.home, prefix)
    apprun = posixpath.join(remote_prefix, "AppRun")
    sandbox = posixpath.join(remote_prefix, "usr/share/cursor/chrome-sandbox")

    # 1. 环境校验
    arch = node.run("uname -m")
    if arch != "aarch64":
        raise RuntimeError(f"本机架构为 {arch}，与 aarch64 AppImage 不匹配")
    if not node.run(f"test -f {shlex.quote(remote_appimage)} && echo yes || echo no") == "yes":
        raise RuntimeError(f"AppImage 不存在：{remote_appimage}")
    node.run(f"chmod +x {shlex.quote(remote_appimage)}")

    # 2. 处理已存在的安装
    exists = node.run(f"test -d {shlex.quote(remote_prefix)} && echo yes || echo no") == "yes"
    if exists:
        if not force:
            raise RuntimeError(f"{remote_prefix} 已存在；如需重装请追加 --force（会自动备份）")
        backup = f"{remote_prefix}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        node.run(f"mv {shlex.quote(remote_prefix)} {shlex.quote(backup)}")
        logger.warning("[%s] 已备份旧安装到 %s", node.label, backup)

    # 3. 解包（在独立临时目录内执行，产物固定为 squashfs-root）
    temp_dir = posixpath.join(node.home, f".cache/cursor-install-{int(time.time())}")
    node.run(f"mkdir -p {shlex.quote(temp_dir)}")
    logger.info("[%s] 开始解包 %s（约 290MB，请稍候）", node.label, remote_appimage)
    started = time.time()
    node.run(f"cd {shlex.quote(temp_dir)} && {shlex.quote(remote_appimage)} --appimage-extract > extract.log 2>&1", timeout=900)
    node.run(f"test -d {shlex.quote(posixpath.join(temp_dir, 'squashfs-root'))}")
    node.run(f"mv {shlex.quote(posixpath.join(temp_dir, 'squashfs-root'))} {shlex.quote(remote_prefix)}")
    node.run(f"rm -rf {shlex.quote(temp_dir)}")
    logger.info("[%s] 解包完成，耗时 %.1fs", node.label, time.time() - started)

    # 4. chrome-sandbox 必须 root 属主 + setuid
    node.run(f"chown root:root {shlex.quote(sandbox)} && chmod 4755 {shlex.quote(sandbox)}", sudo=True)
    sandbox_stat = node.run(f"stat -c '%U:%G:%a' {shlex.quote(sandbox)}")
    if sandbox_stat != "root:root:4755":
        raise RuntimeError(f"chrome-sandbox 权限异常：{sandbox_stat}（期望 root:root:4755）")
    logger.info("[%s] chrome-sandbox 权限已设置：%s", node.label, sandbox_stat)

    # 5. 确认可执行文件与图标
    node.run(f"chmod +x {shlex.quote(apprun)}")
    icon_candidates = [
        posixpath.join(remote_prefix, "usr/share/pixmaps/co.anysphere.cursor.png"),
        posixpath.join(remote_prefix, "usr/share/pixmaps/cursor.png"),
    ]
    icon = icon_candidates[0]
    for candidate in icon_candidates:
        if node.run(f"test -f {shlex.quote(candidate)} && echo yes || echo no") == "yes":
            icon = candidate
            break
    else:
        raise RuntimeError("未找到 Cursor 图标文件")

    # 6. 桌面快捷方式
    desktop = posixpath.join(node.home, ".local/share/applications/cursor.desktop")
    node.run(f"mkdir -p {shlex.quote(posixpath.dirname(desktop))}")
    content = DESKTOP_TEMPLATE.format(apprun=apprun, icon=icon)
    assert node.sftp is not None
    with node.sftp.open(desktop, "w") as handle:
        handle.write(content.encode("utf-8"))
    node.sftp.chmod(desktop, 0o644)
    node.run("update-desktop-database ~/.local/share/applications/ >/dev/null 2>&1 || true")
    logger.info("[%s] 桌面快捷方式已写入 %s", node.label, desktop)

    # 7. 版本自检
    version = node.run(f"timeout 60 {shlex.quote(apprun)} --version 2>/dev/null || true", timeout=90)
    logger.info("[%s] 版本自检：%s", node.label, version.splitlines()[0] if version else "（无输出，可能需要在图形会话中运行）")
    check_node(node, prefix)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="按 Cursor安装说明.md 在双工作站解包安装 Cursor（aarch64）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--node", choices=["all", "node0", "node1"], default="all", help="目标节点")
    parser.add_argument(
        "--node1-account",
        choices=["ops", "his_test"],
        default="ops",
        help="Node1 使用的 SSH 账号（ops=dgxdeploy，his_test=his_test；对 Node0 无影响）",
    )
    parser.add_argument("--appimage", default=DEFAULT_APPIMAGE, help="相对家目录或绝对路径的 AppImage")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="解包安装目录（相对家目录或绝对路径）")
    parser.add_argument("--force", action="store_true", help="覆盖已有安装（旧目录自动备份）")
    parser.add_argument("--check", action="store_true", help="仅检查安装状态")
    parser.add_argument("--keep-going", action="store_true", help="单节点失败后继续处理其他节点")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    failures: list[str] = []
    for node in build_nodes(args.node, args.node1_account):
        try:
            node.connect()
            try:
                if args.check:
                    if not check_node(node, args.prefix):
                        failures.append(node.label)
                else:
                    install_node(node, args.appimage, args.prefix, args.force)
            finally:
                node.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] 处理失败：%s", node.label, exc)
            failures.append(node.label)
            if not args.keep_going:
                break

    if failures:
        logger.error("未成功节点：%s", "、".join(failures))
        return 1
    logger.info("全部节点处理完成 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
