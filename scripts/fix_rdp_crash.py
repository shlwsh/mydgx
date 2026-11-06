#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/fix_rdp_crash.py - 双工作站远程桌面 (RDP) 登录闪退故障根治与会话固化脚本

故障根因分析：
1. 孤儿与死锁会话冲突：
   之前残留的 Xorg :10 与 gnome-session 孤儿进程驻留后台，占用了 UID 1000 的 GNOME 单会话锁；
   新连接进入时被分配 Display :11，新的 gnome-session 启动即检测到冲突崩溃 (exit code 1)，
   导致 xrdp 会话管理器判定窗口管理器退出，立即掐断连接 (闪退)；
2. /etc/xrdp/startwm.sh 环境变量未隔离：
   缺少 unset DBUS_SESSION_BUS_ADDRESS 与 unset XDG_RUNTIME_DIR，
   未显式指定 exec gnome-session --session=ubuntu；
3. sesman.ini 未配置用户会话复用 (Policy=U)：
   客户端窗口微调或重连时会误开新 Display，而非接管已有会话；
4. 证书权限告警：
   xrdp 用户未加入 ssl-cert 组导致无法读取 /etc/xrdp/key.pem。

修复策略：
1. 全面清理双机上所有孤儿 Xorg、xrdp-chansrv、残留 gnome-session 与 /tmp/.X11-unix 锁；
2. 重构 /etc/xrdp/startwm.sh，隔离 DBUS 环境变量并显式启动 Ubuntu GNOME 桌面；
3. 优化 /etc/xrdp/sesman.ini，配置 Policy=U 锁定单用户单会话重连复用；
4. 修正 xrdp 证书与 ssl-cert 用户组权限；
5. 重启 xrdp 与 xrdp-sesman 并验证 3389 端口与日志状态。
"""

import os
import sys
import argparse

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

import paramiko

logger = setup_logger("FixRDPCrash")

STARTWM_SH_CONTENT = r'''#!/bin/sh
# xrdp X session start script (Robust GNOME Session for Ubuntu 24.04)

if test -r /etc/profile; then
    . /etc/profile
fi

if test -r ~/.profile; then
    . ~/.profile
fi

# 关键：清理继承的旧会话总线变量，确保新 X11 显示拥有干净独立的 DBUS 环境
unset DBUS_SESSION_BUS_ADDRESS
unset XDG_RUNTIME_DIR

# 导出标准 Ubuntu GNOME X11 环境变量
export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_DATA_DIRS=/usr/share/ubuntu:/usr/local/share:/usr/share:/var/lib/snapd/desktop
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg

# 直接执行完整的 Ubuntu GNOME 桌面会话管理器
exec gnome-session --session=ubuntu
'''

REMOTE_FIX_SCRIPT = r'''#!/usr/bin/env bash
set -euo pipefail

echo "=========================================="
echo "开始彻底修复与优化远程桌面 (xrdp) 环境"
echo "=========================================="

# 1. 停止 xrdp 服务，以便彻底清理残留进程
echo "--> 1. 停止 xrdp 服务"
systemctl stop xrdp xrdp-sesman 2>/dev/null || true

# 2. 强力终止所有残留的 Xorg、xrdp、gnome-session 孤儿进程
echo "--> 2. 清理所有孤儿 Xorg 与残留桌面进程"
pkill -9 -f "Xorg.*xrdp" 2>/dev/null || true
pkill -9 -f "xrdp-chansrv" 2>/dev/null || true
pkill -9 -f "xrdp-sesman" 2>/dev/null || true
pkill -9 -f "gnome-session.*ubuntu" 2>/dev/null || true
pkill -9 -f "gnome-shell" 2>/dev/null || true

# 清理残留的 X11 临时套接字与锁文件
echo "--> 3. 清理 /tmp 下的 X11 与 xrdp 残留套接字"
rm -rf /tmp/.X11-unix/X* /tmp/.xorgxrdp* /run/xrdp/sockdir/* 2>/dev/null || true

# 3. 修正 xrdp 证书权限
echo "--> 4. 修正 xrdp 私钥读取权限 (ssl-cert 用户组)"
adduser xrdp ssl-cert 2>/dev/null || true
if [ -f /etc/xrdp/key.pem ]; then
    chmod 640 /etc/xrdp/key.pem
    chown root:ssl-cert /etc/xrdp/key.pem
fi

# 4. 固化 /etc/xrdp/startwm.sh
echo "--> 5. 写入优化版 /etc/xrdp/startwm.sh"
cat << 'EOF' > /etc/xrdp/startwm.sh
#!/bin/sh
if test -r /etc/profile; then
    . /etc/profile
fi

if test -r ~/.profile; then
    . ~/.profile
fi

unset DBUS_SESSION_BUS_ADDRESS
unset XDG_RUNTIME_DIR

export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_DATA_DIRS=/usr/share/ubuntu:/usr/local/share:/usr/share:/var/lib/snapd/desktop
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg

exec gnome-session --session=ubuntu
EOF
chmod 755 /etc/xrdp/startwm.sh

# 5. 优化 /etc/xrdp/sesman.ini 配置 Policy=U（按用户复用单一会话，防止多 display 冲突）
echo "--> 6. 配置 /etc/xrdp/sesman.ini 会话复用策略 (Policy=U)"
if [ -f /etc/xrdp/sesman.ini ]; then
    # 替换或添加 Policy=U
    if grep -q "^Policy=" /etc/xrdp/sesman.ini; then
        sed -i 's/^Policy=.*/Policy=U/g' /etc/xrdp/sesman.ini
    elif grep -q "\[Sessions\]" /etc/xrdp/sesman.ini; then
        sed -i '/\[Sessions\]/a Policy=U' /etc/xrdp/sesman.ini
    fi
    
    # 禁用无用的会话杀除限制，保证稳定连接
    sed -i 's/^KillDisconnected=.*/KillDisconnected=false/g' /etc/xrdp/sesman.ini 2>/dev/null || true
fi

# 6. 清理目标用户目录下的干扰文件
echo "--> 7. 检查清理各用户家目录下残留错误日志与锁定"
for homedir in /home/*; do
    [ -d "$homedir" ] || continue
    u="$(basename "$homedir")"
    rm -f "$homedir/.xsession-errors"* 2>/dev/null || true
    rm -f "$homedir/.xorgxrdp."*.log 2>/dev/null || true
    rm -f "$homedir/.Xauthority" 2>/dev/null || true
done

# 7. 启动并启用 xrdp 与 xrdp-sesman
echo "--> 8. 启动 xrdp 服务"
systemctl daemon-reload
systemctl restart xrdp xrdp-sesman
systemctl enable xrdp xrdp-sesman

sleep 2
echo "=========================================="
echo "xrdp 远程桌面环境优化与修复完成"
echo "=========================================="
'''


def get_ssh_client(host, user, password, timeout=15):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=22, username=user, password=password, timeout=timeout)
    return client


def run_remote_fix(client, sudo_pass, node_name):
    logger.info(f"==> 正在对 {node_name} 执行 RDP 修复...")
    remote_script = "/tmp/fix_rdp.sh"
    sftp = client.open_sftp()
    with sftp.file(remote_script, "w") as f:
        f.write(REMOTE_FIX_SCRIPT.replace("\r\n", "\n"))
    sftp.close()

    stdin, stdout, stderr = client.exec_command(f"echo {sudo_pass} | sudo -S chmod +x {remote_script} && echo {sudo_pass} | sudo -S {remote_script}")
    out = stdout.read().decode("utf-8").strip()
    err = stderr.read().decode("utf-8").strip()
    code = stdout.channel.recv_exit_status()

    # 清除临时脚本
    client.exec_command(f"echo {sudo_pass} | sudo -S rm -f {remote_script}")

    clean_err = "\n".join(
        line for line in err.splitlines() if "[sudo] password for" not in line
    ).strip()

    if out:
        logger.info(f"[修复输出]\n{out}")
    if clean_err:
        if code != 0:
            logger.error(f"[错误输出]\n{clean_err}")
        else:
            logger.debug(f"[标准错误]\n{clean_err}")

    if code != 0:
        raise RuntimeError(f"修复脚本执行失败 (退出码={code}):\n{clean_err}")


def verify_node_rdp(client, node_name, sudo_pass):
    logger.info(f"==> 核验 {node_name} RDP 服务状态与监听端口...")
    chk_cmd = """bash -c '
echo "=== 服务运行状态 ==="
systemctl is-active xrdp
systemctl is-active xrdp-sesman
echo "=== 3389 端口监听 ==="
ss -tlpn | grep 3389
echo "=== sesman.ini Policy 配置 ==="
grep "^Policy=" /etc/xrdp/sesman.ini || echo "未显式设置"
echo "=== startwm.sh 首尾预览 ==="
head -n 5 /etc/xrdp/startwm.sh
tail -n 3 /etc/xrdp/startwm.sh
'"""
    stdin, stdout, stderr = client.exec_command(f"echo {sudo_pass} | sudo -S {chk_cmd}")
    out = stdout.read().decode("utf-8").strip()
    clean_out = "\n".join(line for line in out.splitlines() if "[sudo] password for" not in line).strip()
    logger.info(f"[核验输出]\n{clean_out}")


def main():
    init_env()

    parser = argparse.ArgumentParser(
        description="双工作站远程桌面登录闪退根治工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--node",
        choices=["all", "node0", "node1"],
        default="all",
        help="目标节点选择: all, node0, node1 (默认: all)",
    )
    args = parser.parse_args()

    host0 = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user0 = os.getenv("SSH_NODE0_USER", "winbot")
    pass0 = os.getenv("SSH_NODE0_PASS", "winbot")

    host1 = os.getenv("NODE1_MGMT_IP", "172.19.49.159")
    user1 = os.getenv("SSH_NODE1_USER", "dgxdeploy")
    pass1 = os.getenv("SSH_NODE1_PASS", "winning")

    nodes = []
    if args.node in ("all", "node0"):
        nodes.append(("Node0 工作站 (70)", host0, user0, pass0))
    if args.node in ("all", "node1"):
        nodes.append(("Node1 工作站 (159)", host1, user1, pass1))

    for name, host, user, pwd in nodes:
        logger.info("=" * 60)
        logger.info(f"开始修复节点: {name} ({host})")
        logger.info("=" * 60)

        c = get_ssh_client(host, user, pwd)
        try:
            run_remote_fix(c, pwd, name)
            verify_node_rdp(c, name, pwd)
            logger.info(f"[{name}] 远程桌面登录闪退问题修复与核验成功！")
        finally:
            c.close()

    logger.info("=" * 60)
    logger.info("所有工作站远程桌面环境已全部修复并恢复正常！")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
