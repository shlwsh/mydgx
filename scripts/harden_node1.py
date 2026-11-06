#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/harden_node1.py - 159 工作站 (Node1) 全面环境加固与长期稳定保障脚本

加固范围：
1. 远程桌面 (xrdp) 环境深度加固：
   - 优化 /etc/xrdp/startwm.sh（隔离 DBUS 环境变量，显式执行 gnome-session）；
   - 固化 /etc/xrdp/sesman.ini（Policy=U 用户单会话复用绑定）；
   - 补全 xrdp 用户至 ssl-cert 组，修正私钥权限；
   - 部署 Polkit 免弹窗认证规则（消除色彩管理与网络授权弹窗）；
2. 网络连接与激活策略加固（防弹窗与防误连）：
   - 将所有未插线的空置有线连接 (有线连接 2/3/4) 以及易冲突的访客 WiFi (WN-Visitor) 禁用自动连接 (autoconnect=no)；
   - 在 /etc/NetworkManager/conf.d/ 中禁用公网连通性检测 (connectivity-check)，避免隔离内网下的网络受限提示；
3. 系统句柄与电源抗干扰加固：
   - 调高系统 inotify 句柄上限 (max_user_watches=524288, max_user_instances=8192)，彻底根治句柄耗尽；
   - 屏蔽 sleep、suspend、hibernate 目标，防止工作站进入睡眠导致远程桌面与网络中断；
4. 英文目录与桌面快捷方式固化：
   - 确保 dgxdeploy 与 his_test 用户桌面图标信任可用，无任何中文目录干扰。
"""

import os
import sys
import argparse

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

import paramiko

logger = setup_logger("HardenNode1")

REMOTE_HARDEN_SCRIPT = r'''#!/usr/bin/env bash
set -euo pipefail

echo "=========================================="
echo "开始对 159 工作站 (Node1) 执行全维度加固"
echo "=========================================="

# ----------------------------------------------------
# 1. 远程桌面 (xrdp) 环境固化
# ----------------------------------------------------
echo "--> 1. 固化 /etc/xrdp/startwm.sh"
cat << 'EOF' > /etc/xrdp/startwm.sh
#!/bin/sh
if test -r /etc/profile; then
    . /etc/profile
fi

if test -r ~/.profile; then
    . ~/.profile
fi

# 关键：隔离旧 DBUS 会话，确保干净的独立 X11 桌面总线
unset DBUS_SESSION_BUS_ADDRESS
unset XDG_RUNTIME_DIR

export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_DATA_DIRS=/usr/share/ubuntu:/usr/local/share:/usr/share:/var/lib/snapd/desktop
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg

exec gnome-session --session=ubuntu
EOF
chmod 755 /etc/xrdp/startwm.sh

echo "--> 2. 固化 /etc/xrdp/sesman.ini (Policy=U 单用户会话锁定复用)"
if [ -f /etc/xrdp/sesman.ini ]; then
    if grep -q "^Policy=" /etc/xrdp/sesman.ini; then
        sed -i 's/^Policy=.*/Policy=U/g' /etc/xrdp/sesman.ini
    elif grep -q "\[Sessions\]" /etc/xrdp/sesman.ini; then
        sed -i '/\[Sessions\]/a Policy=U' /etc/xrdp/sesman.ini
    fi
    sed -i 's/^KillDisconnected=.*/KillDisconnected=false/g' /etc/xrdp/sesman.ini 2>/dev/null || true
fi

echo "--> 3. 修正 xrdp 证书与用户组权限"
adduser xrdp ssl-cert 2>/dev/null || true
if [ -f /etc/xrdp/key.pem ]; then
    chmod 640 /etc/xrdp/key.pem
    chown root:ssl-cert /etc/xrdp/key.pem
fi

echo "--> 4. 配置 Polkit 规则 (消除 colord 色彩管理与桌面权限弹窗)"
mkdir -p /etc/polkit-1/localauthority/50-local.d
cat << 'EOF' > /etc/polkit-1/localauthority/50-local.d/45-allow-colord.pkla
[Allow Colord all Users]
Identity=unix-user:*
Action=org.freedesktop.color-manager.create-device;org.freedesktop.color-manager.create-profile;org.freedesktop.color-manager.delete-device;org.freedesktop.color-manager.delete-profile;org.freedesktop.color-manager.modify-device;org.freedesktop.color-manager.modify-profile
ResultAny=no
ResultInactive=no
ResultActive=yes
EOF

# ----------------------------------------------------
# 2. 网络连接与激活策略加固 (防弹窗与防误连)
# ----------------------------------------------------
echo "--> 5. 禁用未插线有线网口与访客网络的自动连接 (防激活失败弹窗)"
for conn in "有线连接 2" "有线连接 3" "有线连接 4" "WN-Visitor"; do
    if nmcli connection show "$conn" >/dev/null 2>&1; then
        nmcli connection modify "$conn" connection.autoconnect no 2>/dev/null || true
        echo "已将 $conn 设置为 autoconnect=no"
    fi
done

echo "--> 6. 禁用 NetworkManager 公网连通性探测 (防隔离内网受限网络提示)"
mkdir -p /etc/NetworkManager/conf.d
cat << 'EOF' > /etc/NetworkManager/conf.d/20-disable-connectivity-check.conf
[connectivity]
enabled=false
uri=
EOF

# ----------------------------------------------------
# 3. 系统级防崩与稳定性加固 (inotify 句柄与防休眠)
# ----------------------------------------------------
echo "--> 7. 调优系统 inotify 句柄上限 (根治 Too many open files)"
cat << 'EOF' > /etc/sysctl.d/99-custom-limits.conf
fs.inotify.max_user_watches = 524288
fs.inotify.max_user_instances = 8192
EOF
sysctl -p /etc/sysctl.d/99-custom-limits.conf >/dev/null 2>&1 || true

echo "--> 8. 屏蔽系统睡眠/休眠目标 (防止工作站锁屏后失联)"
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true

# ----------------------------------------------------
# 4. 桌面环境与快捷方式信任固化
# ----------------------------------------------------
echo "--> 9. 固化各用户 Desktop 图标信任"
for u in dgxdeploy his_test; do
    user_desktop="/home/$u/Desktop"
    if [ -d "$user_desktop" ]; then
        for f in "$user_desktop"/*.desktop; do
            [ -f "$f" ] || continue
            chown "$u:$u" "$f"
            chmod +x "$f"
            gio set "$f" metadata::trusted true 2>/dev/null || true
        done
        echo "已加固用户 $u 桌面快捷方式"
    fi
done

# ----------------------------------------------------
# 5. 重启并验证核心服务
# ----------------------------------------------------
echo "--> 10. 重启应用 xrdp 服务"
systemctl daemon-reload
systemctl restart xrdp xrdp-sesman
systemctl enable xrdp xrdp-sesman >/dev/null 2>&1 || true

sleep 2
echo "=========================================="
echo "159 工作站 (Node1) 加固脚本执行完毕"
echo "=========================================="
'''


def get_ssh_client(host, user, password, timeout=15):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=22, username=user, password=password, timeout=timeout)
    return client


def run_remote_script(client, script_text, sudo_pass, desc=None):
    if desc:
        logger.info(f"==> {desc}")
    remote_sh = "/tmp/_harden_node1.sh"
    sftp = client.open_sftp()
    with sftp.file(remote_sh, "w") as f:
        f.write("#!/usr/bin/env bash\nset -euo pipefail\n" + script_text.replace("\r\n", "\n") + "\n")
    sftp.close()

    stdin, stdout, stderr = client.exec_command(f"echo {sudo_pass} | sudo -S chmod +x {remote_sh} && echo {sudo_pass} | sudo -S {remote_sh}")
    out = stdout.read().decode("utf-8").strip()
    err = stderr.read().decode("utf-8").strip()
    code = stdout.channel.recv_exit_status()

    # 清除临时脚本
    client.exec_command(f"echo {sudo_pass} | sudo -S rm -f {remote_sh}")

    clean_err = "\n".join(
        line for line in err.splitlines() if "[sudo] password for" not in line
    ).strip()

    if out:
        logger.info(f"[命令输出]\n{out}")
    if clean_err:
        if code != 0:
            logger.error(f"[错误输出]\n{clean_err}")
        else:
            logger.debug(f"[标准错误]\n{clean_err}")

    if code != 0:
        raise RuntimeError(f"命令执行失败 (退出码={code}):\n{clean_err}")
    return out


def verify_node1_status(client, sudo_pass):
    logger.info("==> 正在核验 159 工作站各项加固指标...")
    verify_script = """
echo "=== 1. xrdp 与 3389 服务状态 ==="
systemctl is-active xrdp xrdp-sesman
ss -tlpn | grep 3389

echo "=== 2. inotify 句柄配置 ==="
cat /proc/sys/fs/inotify/max_user_watches
cat /proc/sys/fs/inotify/max_user_instances

echo "=== 3. 睡眠休眠屏蔽状态 ==="
systemctl is-enabled sleep.target suspend.target hibernate.target

echo "=== 4. 未插线有线连接自动连接状态 ==="
nmcli -f NAME,AUTOCONNECT connection show | grep -E "有线连接 [234]|WN-Visitor" || true

echo "=== 5. startwm.sh 与 sesman.ini 策略 ==="
grep "^Policy=" /etc/xrdp/sesman.ini || echo "无 Policy"
tail -n 3 /etc/xrdp/startwm.sh
"""
    run_remote_script(client, verify_script, sudo_pass, desc="核验加固结果")


def main():
    init_env()

    parser = argparse.ArgumentParser(
        description="159 工作站 (Node1) 深度环境加固工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    args = parser.parse_args()

    host1 = os.getenv("NODE1_MGMT_IP", "172.19.49.159")
    user1 = os.getenv("SSH_NODE1_USER", "dgxdeploy")
    pass1 = os.getenv("SSH_NODE1_PASS", "winning")

    logger.info("=" * 60)
    logger.info(f"开始对 159 工作站 ({user1}@{host1}) 执行同步环境加固...")
    logger.info("=" * 60)

    client1 = get_ssh_client(host1, user1, pass1)
    try:
        # 执行加固
        run_remote_script(client1, REMOTE_HARDEN_SCRIPT, pass1, desc="应用全维度加固配置")

        # 核验加固结果
        verify_node1_status(client1, pass1)

        logger.info("=" * 60)
        logger.info("159 工作站 (Node1) 同步加固与稳定性优化圆满完成！")
        logger.info("=" * 60)
    finally:
        client1.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
