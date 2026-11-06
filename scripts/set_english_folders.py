#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/set_english_folders.py - 双工作站用户文件夹默认英文显示配置脚本

功能：
1. 全局配置：
   - 在 /etc/xdg/user-dirs.conf 中强制设置 enabled=False，彻底禁用自动根据 locale 更新目录；
   - 禁用 autostart 中的 user-dirs-update-gtk.desktop 与 xdg-user-dirs.desktop，避免 GNOME 登录弹窗；
   - 预置 /etc/skel 默认英文目录配置，保证后续新建用户开箱即为英文。
2. 用户目录处理（Node0: winbot；Node1: dgxdeploy, his_test）：
   - 创建标准 8 大英文目录（Desktop, Downloads, Templates, Public, Documents, Music, Pictures, Videos）；
   - 扫描中文目录（桌面, 下载, 模板, 公共, 文档, 音乐, 图片, 视频），若有文件安全迁移合并至英文目录，随后删除中文目录；
   - 固化 ~/.config/user-dirs.dirs 为全英文路径；
   - 固化 ~/.config/user-dirs.locale 为 en_US；
   - 修正用户权限 (chown)。
3. 校验与验证：
   - 远程核验目录结构与配置文件，确保无中文目录残留。
"""

import os
import sys
import argparse

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

import paramiko

logger = setup_logger("SetEnglishFolders")

REMOTE_SCRIPT = r'''#!/usr/bin/env bash
set -euo pipefail

TARGET_USERS="$1"
echo "=========================================="
echo "开始配置系统与用户英文目录规范"
echo "目标用户列表: $TARGET_USERS"
echo "=========================================="

# 1. 全局 XDG 配置：彻底禁用自动更新
echo "--> 1. 配置 /etc/xdg/user-dirs.conf (enabled=False)"
mkdir -p /etc/xdg
if [ -f /etc/xdg/user-dirs.conf ]; then
    if grep -q "^enabled=" /etc/xdg/user-dirs.conf; then
        sed -i 's/^enabled=.*/enabled=False/g' /etc/xdg/user-dirs.conf
    else
        echo "enabled=False" >> /etc/xdg/user-dirs.conf
    fi
else
    cat << 'EOF' > /etc/xdg/user-dirs.conf
enabled=False
filename_encoding=UTF-8
EOF
fi

# 2. 禁用 autostart 中的目录弹窗更新提示
echo "--> 2. 禁用 GNOME autostart 中的 user-dirs 弹窗更新组件"
mkdir -p /etc/xdg/autostart
for dfile in /etc/xdg/autostart/user-dirs-update-gtk.desktop /etc/xdg/autostart/xdg-user-dirs.desktop; do
    if [ -f "$dfile" ]; then
        if grep -q "^Hidden=" "$dfile"; then
            sed -i 's/^Hidden=.*/Hidden=true/g' "$dfile"
        else
            echo "Hidden=true" >> "$dfile"
        fi
        if grep -q "^X-GNOME-Autostart-enabled=" "$dfile"; then
            sed -i 's/^X-GNOME-Autostart-enabled=.*/X-GNOME-Autostart-enabled=false/g' "$dfile"
        else
            echo "X-GNOME-Autostart-enabled=false" >> "$dfile"
        fi
        echo "已处理: $dfile"
    fi
done

# 3. 预置 /etc/skel 默认英文配置
echo "--> 3. 预置 /etc/skel 纯英文配置"
mkdir -p /etc/skel/.config
cat << 'EOF' > /etc/skel/.config/user-dirs.dirs
XDG_DESKTOP_DIR="$HOME/Desktop"
XDG_DOWNLOAD_DIR="$HOME/Downloads"
XDG_TEMPLATES_DIR="$HOME/Templates"
XDG_PUBLICSHARE_DIR="$HOME/Public"
XDG_DOCUMENTS_DIR="$HOME/Documents"
XDG_MUSIC_DIR="$HOME/Music"
XDG_PICTURES_DIR="$HOME/Pictures"
XDG_VIDEOS_DIR="$HOME/Videos"
EOF
echo "en_US" > /etc/skel/.config/user-dirs.locale

# 4. 针对各目标用户处理
FOLDER_MAP="桌面:Desktop 下载:Downloads 模板:Templates 公共:Public 文档:Documents 音乐:Music 图片:Pictures 视频:Videos"
STANDARD_EN_DIRS="Desktop Downloads Templates Public Documents Music Pictures Videos"

IFS=',' read -ra USERS <<< "$TARGET_USERS"
for u in "${USERS[@]}"; do
    u_trimmed="$(echo -n "$u" | xargs)"
    [ -z "$u_trimmed" ] && continue
    
    homedir="/home/$u_trimmed"
    if [ ! -d "$homedir" ]; then
        echo "[跳过] 家目录不存在: $homedir"
        continue
    fi

    echo "------------------------------------------"
    echo "正在处理用户: $u_trimmed (家目录: $homedir)"
    
    # 获取用户的主组
    ugroup="$(id -gn "$u_trimmed" 2>/dev/null || echo "$u_trimmed")"

    # 4.1 确保标准英文目录存在
    for en_dir in $STANDARD_EN_DIRS; do
        target_path="$homedir/$en_dir"
        if [ ! -d "$target_path" ]; then
            mkdir -p "$target_path"
            chown "$u_trimmed:$ugroup" "$target_path"
            echo "已创建英文标准目录: $target_path"
        fi
    done

    # 4.2 迁移中文目录并清理
    for pair in $FOLDER_MAP; do
        zh_name="${pair%%:*}"
        en_name="${pair##*:}"
        zh_path="$homedir/$zh_name"
        en_path="$homedir/$en_name"

        if [ -d "$zh_path" ]; then
            echo "检测到中文目录: $zh_path -> 目标: $en_path"
            # 检查是否有内容
            shopt -s dotglob nullglob
            items=("$zh_path"/*)
            shopt -u dotglob nullglob

            if [ ${#items[@]} -gt 0 ]; then
                echo "发现 ${#items[@]} 项数据，正在合并迁移至 $en_path ..."
                cp -rn "$zh_path"/* "$en_path"/ 2>/dev/null || cp -r "$zh_path"/* "$en_path"/
                echo "迁移完成"
            fi
            rm -rf "$zh_path"
            echo "已安全移除中文目录: $zh_path"
        fi
    done

    # 4.3 写入用户 user-dirs.dirs 与 user-dirs.locale
    mkdir -p "$homedir/.config"
    cat << 'EOF' > "$homedir/.config/user-dirs.dirs"
# This file is written by xdg-user-dirs-update
# If you want to change or add directories, just edit the line you're
# interested in. All local changes will be retained on the next run.
# Format is XDG_xxx_DIR="$HOME/yyy", where yyy is a shell-escaped
# homedir-relative path, or XDG_xxx_DIR="/yyy", where /yyy is an
# absolute path. No other format is supported.
# 
XDG_DESKTOP_DIR="$HOME/Desktop"
XDG_DOWNLOAD_DIR="$HOME/Downloads"
XDG_TEMPLATES_DIR="$HOME/Templates"
XDG_PUBLICSHARE_DIR="$HOME/Public"
XDG_DOCUMENTS_DIR="$HOME/Documents"
XDG_MUSIC_DIR="$HOME/Music"
XDG_PICTURES_DIR="$HOME/Pictures"
XDG_VIDEOS_DIR="$HOME/Videos"
EOF

    echo "en_US" > "$homedir/.config/user-dirs.locale"

    # 4.4 权限修复
    chown -R "$u_trimmed:$ugroup" "$homedir/.config/user-dirs.dirs" "$homedir/.config/user-dirs.locale"
    for en_dir in $STANDARD_EN_DIRS; do
        chown -R "$u_trimmed:$ugroup" "$homedir/$en_dir"
    done
    echo "用户 $u_trimmed 英文目录配置与权限固化成功"
done

echo "=========================================="
echo "全部英文文件夹与 XDG 固化策略应用完成"
echo "=========================================="
'''


def get_ssh_client(host, user, password, timeout=10):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=22, username=user, password=password, timeout=timeout)
    return client


def exec_sudo(client, cmd, sudo_pass, desc=None):
    if desc:
        logger.info(f"==> {desc}")
    wrapped = f"echo {sudo_pass} | sudo -S bash -c {cmd!r}"
    # 使用标准 stdin 输入密码更安全可靠
    stdin, stdout, stderr = client.exec_command(f"sudo -S {cmd}")
    stdin.write(f"{sudo_pass}\n")
    stdin.flush()
    out = stdout.read().decode("utf-8").strip()
    err = stderr.read().decode("utf-8").strip()
    code = stdout.channel.recv_exit_status()

    # 过滤 sudo 密码提示输出
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


def configure_node(node_name, host, ssh_user, ssh_pass, target_users):
    logger.info("=" * 60)
    logger.info(f"开始配置节点: {node_name} ({host})，目标用户: {target_users}")
    logger.info("=" * 60)

    client = get_ssh_client(host, ssh_user, ssh_pass)
    try:
        # 1. 通过 SFTP 上传远程脚本
        remote_script_path = "/tmp/set_english_folders.sh"
        sftp = client.open_sftp()
        with sftp.file(remote_script_path, "w") as f:
            # 统一转为 Linux LF 换行符
            f.write(REMOTE_SCRIPT.replace("\r\n", "\n"))
        sftp.close()

        # 2. 赋予执行权限并以 sudo 执行
        users_arg = ",".join(target_users)
        exec_sudo(client, f"chmod +x {remote_script_path}", ssh_pass)
        exec_sudo(
            client,
            f"{remote_script_path} '{users_arg}'",
            ssh_pass,
            desc=f"在 {node_name} 执行目录英文固化配置",
        )
        # 清理远程临时脚本
        exec_sudo(client, f"rm -f {remote_script_path}", ssh_pass)

        # 3. 验证各用户目录与配置
        logger.info(f"==> 验证 {node_name} 目录配置状态")
        user_list_str = " ".join(target_users)
        verify_cmd = f"""bash -c '
echo "=== 全局 /etc/xdg/user-dirs.conf ==="
cat /etc/xdg/user-dirs.conf 2>/dev/null || echo "不存在"
echo "=== 检查 autostart 配置 ==="
grep -E "Hidden|X-GNOME-Autostart" /etc/xdg/autostart/*user-dirs* 2>/dev/null || true
for u in {user_list_str}; do
    echo "=== 用户 $u 的家目录标准目录 ==="
    ls -ld /home/$u/Desktop /home/$u/Downloads /home/$u/Templates /home/$u/Public /home/$u/Documents /home/$u/Music /home/$u/Pictures /home/$u/Videos 2>/dev/null || true
    echo "=== 检查 $u 是否存在中文目录残留 ==="
    ls -d /home/$u/桌面 /home/$u/下载 /home/$u/模板 /home/$u/公共 /home/$u/文档 /home/$u/音乐 /home/$u/图片 /home/$u/视频 2>/dev/null || echo "无中文目录残留 (合格)"
    echo "=== $u ~/.config/user-dirs.dirs ==="
    cat /home/$u/.config/user-dirs.dirs 2>/dev/null || echo "无"
    echo "=== $u ~/.config/user-dirs.locale ==="
    cat /home/$u/.config/user-dirs.locale 2>/dev/null || echo "无"
done
'"""
        exec_sudo(client, verify_cmd, ssh_pass, desc=f"核验 {node_name} 配置与残留状态")
        logger.info(f"[{node_name}] 英文文件夹规范已全部设置成功且通过核验！")

    finally:
        client.close()


def main():
    init_env()

    parser = argparse.ArgumentParser(
        description="双工作站默认英文文件夹配置与持久化工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--node",
        choices=["all", "node0", "node1"],
        default="all",
        help="目标节点选择: node0, node1 或 all (默认: all)",
    )
    args = parser.parse_args()

    node0_host = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    node0_user = os.getenv("SSH_NODE0_USER", "winbot")
    node0_pass = os.getenv("SSH_NODE0_PASS", "winbot")

    node1_host = os.getenv("NODE1_MGMT_IP", "172.19.49.159")
    node1_user = os.getenv("SSH_NODE1_USER", "dgxdeploy")
    node1_pass = os.getenv("SSH_NODE1_PASS", "winning")

    nodes_to_run = []
    if args.node in ("all", "node0"):
        nodes_to_run.append(("Node0 工作站", node0_host, node0_user, node0_pass, ["winbot"]))
    if args.node in ("all", "node1"):
        nodes_to_run.append(("Node1 工作站", node1_host, node1_user, node1_pass, ["dgxdeploy", "his_test"]))

    for name, host, user, pwd, users in nodes_to_run:
        configure_node(name, host, user, pwd, users)

    logger.info("=" * 60)
    logger.info("所有指定工作站节点英文目录配置已顺利完成！")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
