#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双工作站 FlClash 桌面应用启动与桌面快捷方式彻底修复脚本
针对远程桌面 (xrdp) 环境下的 OpenGL/DRI3 渲染优化、Desktop 信任属性与自愈机制
"""

import os
import sys
import logging
import argparse
import paramiko
from env_helper import init_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("FixFlClash")

# 标准优化包装器内容
LAUNCHER_SCRIPT_CONTENT = """#!/bin/bash
# FlClash 优化启动包装器
# 解决 xrdp 虚拟显卡无硬件 DRI3 导致的 OpenGL 帧超时与 Flutter 渲染卡死问题

export GDK_BACKEND=x11
export LIBGL_ALWAYS_SOFTWARE=1
export LD_LIBRARY_PATH="/usr/share/FlClash/lib:${LD_LIBRARY_PATH:-}"

# 进入程序基准目录
cd /usr/share/FlClash || exit 1

exec /usr/share/FlClash/FlClash "$@"
"""

# 标准系统级 FlClash.desktop
FLCLASH_DESKTOP_CONTENT = """[Desktop Entry]
Version=1.0
Type=Application
Name=FlClash
GenericName=Clash GUI Proxy
Comment=FlClash Proxy Client for Linux
Icon=FlClash
Exec=/usr/local/bin/flclash-launcher %U
Path=/usr/share/FlClash
Terminal=false
Categories=Network;WebBrowser;Utility;
Keywords=FlClash;Clash;ClashMeta;Proxy;VPN;
StartupNotify=true
StartupWMClass=com.follow.clash
"""

# 标准系统级 OpenCode.desktop
OPENCODE_DESKTOP_CONTENT = """[Desktop Entry]
Version=1.0
Type=Application
Name=OpenCode
GenericName=AI Assistant
Comment=OpenCode AI Assistant Terminal
Exec=gnome-terminal --title="OpenCode AI" -- opencode
Icon=utilities-terminal
Terminal=false
Categories=Development;Utility;
StartupNotify=true
"""


def get_ssh_client(host, user, password, port=22):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=port, username=user, password=password, timeout=10)
    return client


def run_remote_script(client, script_content, sudo_password=None, desc=""):
    sftp = client.open_sftp()
    remote_script_path = "/tmp/fix_flclash_tmp.sh"
    with sftp.file(remote_script_path, "w") as f:
        f.write(script_content)
    sftp.close()

    if sudo_password:
        cmd = f"echo '{sudo_password}' | sudo -S bash {remote_script_path}"
    else:
        cmd = f"bash {remote_script_path}"

    logger.info(f"==> 执行任务: {desc} ...")
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()

    # 清理临时脚本
    client.exec_command(f"rm -f {remote_script_path}")

    # 过滤掉 sudo 密码提示行
    clean_lines = [l for l in out.splitlines() if not l.startswith("[sudo] password for ")]
    clean_out = "\n".join(clean_lines).strip()
    if clean_out:
        logger.info(f"[执行输出]\n{clean_out}")

    if code != 0:
        raise RuntimeError(f"任务执行失败 (退出码={code}):\n{clean_out}")
    return clean_out


def fix_node(client, host, user, password, target_users):
    logger.info(f"==================== 开始修复节点 {host} ====================")

    # 1. 部署 /usr/local/bin/flclash-launcher
    # 2. 替换软链接 /usr/bin/FlClash、/usr/local/bin/flclash 等
    # 3. 部署并更新 /usr/share/applications/FlClash.desktop 与 opencode.desktop
    # 4. 同步更新所有目标用户的 ~/Desktop/ 快捷方式
    # 5. 固化用户级别与登录脚本中的 metadata::trusted 自愈机制

    users_str = " ".join(target_users)

    setup_script = f"""#!/bin/bash
set -e

echo "=== 1. 部署优化包装脚本 /usr/local/bin/flclash-launcher ==="
cat << 'EOF' > /usr/local/bin/flclash-launcher
{LAUNCHER_SCRIPT_CONTENT}
EOF
chmod 755 /usr/local/bin/flclash-launcher

# 确保全局命令均指向优化包装器
ln -sf /usr/local/bin/flclash-launcher /usr/bin/FlClash
ln -sf /usr/local/bin/flclash-launcher /usr/local/bin/FlClash
ln -sf /usr/local/bin/flclash-launcher /usr/local/bin/flclash
echo "全局命令软链接已建立: /usr/bin/FlClash -> /usr/local/bin/flclash-launcher"

echo "=== 2. 部署系统应用程序菜单项 ==="
cat << 'EOF' > /usr/share/applications/FlClash.desktop
{FLCLASH_DESKTOP_CONTENT}
EOF
chmod 644 /usr/share/applications/FlClash.desktop

cat << 'EOF' > /usr/share/applications/opencode.desktop
{OPENCODE_DESKTOP_CONTENT}
EOF
chmod 644 /usr/share/applications/opencode.desktop

# 更新系统桌面数据库
update-desktop-database /usr/share/applications 2>/dev/null || true

echo "=== 3. 部署用户桌面快捷方式并标记为受信任 ==="
for u in {users_str}; do
    if id "$u" >/dev/null 2>&1; then
        u_home=$(getent passwd "$u" | cut -d: -f6)
        u_desktop="$u_home/Desktop"
        
        # 确保 Desktop 目录存在
        mkdir -p "$u_desktop"
        chown "$u:$u" "$u_desktop"
        chmod 755 "$u_desktop"

        # 拷贝快捷方式
        cp /usr/share/applications/FlClash.desktop "$u_desktop/FlClash.desktop"
        cp /usr/share/applications/opencode.desktop "$u_desktop/opencode.desktop"
        chown -R "$u:$u" "$u_desktop"
        chmod 755 "$u_desktop/FlClash.desktop" "$u_desktop/opencode.desktop"

        # 写入用户 ~/.xsessionrc 确保登录时自愈信任状态
        xsessionrc="$u_home/.xsessionrc"
        touch "$xsessionrc"
        chown "$u:$u" "$xsessionrc"
        
        # 增加信任自愈与优化环境变量
        if ! grep -q "FLCLASH_OPTIMIZE_INCLUDED" "$xsessionrc"; then
            cat << 'EOF_X' >> "$xsessionrc"

# --- FLCLASH_OPTIMIZE_INCLUDED: 自动优化与桌面图标信任自愈 ---
export GDK_BACKEND=x11
export LIBGL_ALWAYS_SOFTWARE=1
(
    sleep 3
    for f in "$HOME/Desktop/"*.desktop; do
        [ -f "$f" ] && gio set "$f" metadata::trusted true 2>/dev/null || true
    done
) &
# -------------------------------------------------------------
EOF_X
            echo "已为用户 $u 配置 ~/.xsessionrc 桌面自愈钩子"
        fi

        # 立即尝试通过用户 dbus 设置 metadata::trusted
        u_uid=$(id -u "$u")
        bus_sock="/run/user/$u_uid/bus"
        if [ -S "$bus_sock" ]; then
            sudo -u "$u" DBUS_SESSION_BUS_ADDRESS="unix:path=$bus_sock" gio set "$u_desktop/FlClash.desktop" metadata::trusted true 2>/dev/null || true
            sudo -u "$u" DBUS_SESSION_BUS_ADDRESS="unix:path=$bus_sock" gio set "$u_desktop/opencode.desktop" metadata::trusted true 2>/dev/null || true
            echo "已为当前在线会话用户 $u 设置 gio metadata::trusted"
        else
            echo "用户 $u 当前无活跃 D-Bus socket ($bus_sock)，将在登录时由 .xsessionrc 自动设置"
        fi
    fi
done

echo "=== 4. 清理残余僵尸 socket 并完成修复 ==="
rm -f /tmp/FlClashSocket* /tmp/.flclash* 2>/dev/null || true
echo "节点配置完成！"
"""
    run_remote_script(client, setup_script, password, desc=f"在 {host} 部署 FlClash 优化与桌面信任机制")


def verify_node(client, host, user, password, target_users):
    logger.info(f"==> 验证节点 {host} 修复结果...")
    verify_script = """#!/bin/bash
echo "=== 1. 验证命令路径与执行包装 ==="
which FlClash
which flclash
head -n 12 /usr/local/bin/flclash-launcher

echo "=== 2. 验证应用程序菜单与桌面快捷方式 ==="
ls -la /usr/share/applications/FlClash.desktop
cat /usr/share/applications/FlClash.desktop

echo "=== 3. 验证各用户桌面属性 ==="
for u in winbot his_test dgxdeploy; do
    if [ -d "/home/$u/Desktop" ]; then
        echo "--- 用户 $u 桌面快捷方式 ---"
        ls -la /home/$u/Desktop/*.desktop
        for f in /home/$u/Desktop/*.desktop; do
            echo "属性 $f:"
            gio info "$f" 2>/dev/null | grep -i metadata || true
        done
    fi
done

echo "=== 4. 模拟启动测试 (若有活跃会话) ==="
ACT_DISP=$(ls /tmp/.X11-unix/ 2>/dev/null | sed 's/X/:/' | head -n 1)
if [ -n "$ACT_DISP" ]; then
    echo "检测到活跃 DISPLAY: $ACT_DISP，正在测试启动 FlClash 4 秒..."
    export DISPLAY=$ACT_DISP
    # 精确清理旧进程
    killall -9 FlClash FlClashCore 2>/dev/null || true
    sleep 1

    /usr/local/bin/flclash-launcher > /tmp/fl_verify.log 2>&1 &
    PID=$!
    sleep 3
    ps aux | grep -E "FlClash|FlClashCore" | grep -v grep || echo "进程已退出"
    echo "--- 检查窗口 ---"
    xwininfo -root -tree | grep -i clash || echo "无窗口"
    echo "--- 日志截取 ---"
    head -n 15 /tmp/fl_verify.log
    # 清理验证进程
    killall -9 FlClash FlClashCore 2>/dev/null || true
else
    echo "当前无活跃 DISPLAY 会话，跳过实时图形窗口验证"
fi
"""
    run_remote_script(client, verify_script, password, desc=f"在 {host} 进行最终验证")


def main():
    init_env()

    parser = argparse.ArgumentParser(
        description="修复双工作站 FlClash 桌面应用启动与快捷方式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--node",
        choices=["all", "node0", "node1"],
        default="all",
        help="目标节点: all, node0, node1 (默认: all)",
    )
    args = parser.parse_args()

    host0 = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user0 = os.getenv("SSH_NODE0_USER", "winbot")
    pass0 = os.getenv("SSH_NODE0_PASS", "winbot")

    host1 = os.getenv("NODE1_MGMT_IP", "172.19.49.159")
    user1 = os.getenv("SSH_NODE1_USER", "dgxdeploy")
    pass1 = os.getenv("SSH_NODE1_PASS", "winning")

    if args.node in ("all", "node0"):
        c0 = get_ssh_client(host0, user0, pass0)
        try:
            fix_node(c0, host0, user0, pass0, ["winbot"])
            verify_node(c0, host0, user0, pass0, ["winbot"])
        finally:
            c0.close()

    if args.node in ("all", "node1"):
        c1 = get_ssh_client(host1, user1, pass1)
        try:
            fix_node(c1, host1, user1, pass1, ["his_test", "dgxdeploy"])
            verify_node(c1, host1, user1, pass1, ["his_test", "dgxdeploy"])
        finally:
            c1.close()

    logger.info("所有节点 FlClash 桌面应用修复与验证全部完成！")


if __name__ == "__main__":
    main()
