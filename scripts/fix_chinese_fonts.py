#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双工作站中文字体与 FlClash 界面字符乱码彻底修复脚本
1. 安装文泉驿微米黑 (fonts-wqy-microhei) 与文泉驿正黑 (fonts-wqy-zenhei)
2. 配置全局 Fontconfig (/etc/fonts/local.conf)，将 Noto Sans CJK SC 与文泉驿作为全局通用字体 (sans-serif / monospace / serif) 首选 CJK 回退
3. 优化 FlClash 启动包装器 (/usr/local/bin/flclash-launcher) 的字符集与字体环境
4. 刷新系统字体缓存并做双机验证
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
logger = logging.getLogger("FixChineseFonts")

GLOBAL_FONTS_CONF = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
    <!-- 默认无衬线字体 sans-serif 回退链: 英文字体 + 简体中文思源黑体 + 文泉驿微米黑 -->
    <match target="pattern">
        <test qual="any" name="family">
            <string>sans-serif</string>
        </test>
        <edit name="family" mode="prepend" binding="strong">
            <string>DejaVu Sans</string>
            <string>Noto Sans CJK SC</string>
            <string>WenQuanYi Micro Hei</string>
            <string>WenQuanYi Zen Hei</string>
        </edit>
    </match>

    <!-- 默认等宽字体 monospace 回退链 -->
    <match target="pattern">
        <test qual="any" name="family">
            <string>monospace</string>
        </test>
        <edit name="family" mode="prepend" binding="strong">
            <string>DejaVu Sans Mono</string>
            <string>Noto Sans Mono CJK SC</string>
            <string>WenQuanYi Micro Hei Mono</string>
        </edit>
    </match>

    <!-- 默认衬线字体 serif 回退链 -->
    <match target="pattern">
        <test qual="any" name="family">
            <string>serif</string>
        </test>
        <edit name="family" mode="prepend" binding="strong">
            <string>DejaVu Serif</string>
            <string>Noto Serif CJK SC</string>
            <string>AR PL UMing CN</string>
        </edit>
    </match>

    <!-- 常见中文字体别名重定向 -->
    <match target="pattern">
        <test qual="any" name="family">
            <string>SimSun</string>
        </test>
        <edit name="family" mode="assign" binding="same">
            <string>Noto Serif CJK SC</string>
        </edit>
    </match>
    <match target="pattern">
        <test qual="any" name="family">
            <string>SimHei</string>
        </test>
        <edit name="family" mode="assign" binding="same">
            <string>Noto Sans CJK SC</string>
        </edit>
    </match>
    <match target="pattern">
        <test qual="any" name="family">
            <string>Microsoft YaHei</string>
        </test>
        <edit name="family" mode="assign" binding="same">
            <string>Noto Sans CJK SC</string>
        </edit>
    </match>
</fontconfig>
"""

LAUNCHER_SCRIPT_CONTENT = """#!/bin/bash
# FlClash 优化启动包装器
# 1. 解决 xrdp 虚拟显卡无硬件 DRI3 导致的 OpenGL 帧超时
# 2. 导出 CJK 字符类型环境，保障界面中文与 Unicode 字符清晰渲染

export GDK_BACKEND=x11
export LIBGL_ALWAYS_SOFTWARE=1
export LC_CTYPE="zh_CN.UTF-8"
export LD_LIBRARY_PATH="/usr/share/FlClash/lib:${LD_LIBRARY_PATH:-}"

# 进入程序基准目录
cd /usr/share/FlClash || exit 1

exec /usr/share/FlClash/FlClash "$@"
"""


def get_ssh_client(host, user, password, port=22):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=port, username=user, password=password, timeout=10)
    return client


def run_remote_script(client, script_content, sudo_password=None, desc=""):
    sftp = client.open_sftp()
    remote_script_path = "/tmp/fix_fonts_tmp.sh"
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

    client.exec_command(f"rm -f {remote_script_path}")

    clean_lines = [l for l in out.splitlines() if not l.startswith("[sudo] password for ")]
    clean_out = "\n".join(clean_lines).strip()
    if clean_out:
        logger.info(f"[执行输出]\n{clean_out}")

    if code != 0:
        raise RuntimeError(f"任务执行失败 (退出码={code}):\n{clean_out}")
    return clean_out


def fix_fonts_on_node(client, host, user, password):
    logger.info(f"==================== 配置节点字体与字符环境 {host} ====================")

    setup_script = f"""#!/bin/bash
set -e

echo "=== 1. 安装文泉驿微米黑、文泉驿正黑与思源黑体 ==="
apt-get update -qq
apt-get install -y fonts-wqy-microhei fonts-wqy-zenhei fonts-noto-cjk

echo "=== 2. 部署全局 /etc/fonts/local.conf 规则 ==="
cat << 'EOF' > /etc/fonts/local.conf
{GLOBAL_FONTS_CONF}
EOF
chmod 644 /etc/fonts/local.conf

echo "=== 3. 刷新系统字体缓存 ==="
fc-cache -f

echo "=== 4. 更新 /usr/local/bin/flclash-launcher 启动包装器 ==="
cat << 'EOF' > /usr/local/bin/flclash-launcher
{LAUNCHER_SCRIPT_CONTENT}
EOF
chmod 755 /usr/local/bin/flclash-launcher

echo "=== 5. 确保中文字符集环境 zh_CN.UTF-8 已生成 ==="
locale-gen zh_CN.UTF-8 en_US.UTF-8
update-locale

echo "配置完成！"
"""
    run_remote_script(client, setup_script, password, desc=f"在 {host} 安装中文字体与配置 Fontconfig")


def verify_fonts_on_node(client, host, user, password):
    logger.info(f"==> 验证节点 {host} 字体与字符匹配...")
    verify_script = """#!/bin/bash
echo "=== 1. 验证中文字符 '中'(4e2d) 匹配字体 ==="
fc-match "sans-serif:charset=4e2d"

echo "=== 2. 验证 sans-serif 前 5 项回退顺序 ==="
fc-match -s "sans-serif" | head -n 5

echo "=== 3. 验证 monospace 前 5 项回退顺序 ==="
fc-match -s "monospace" | head -n 5

echo "=== 4. 验证 FlClash 包装器配置 ==="
head -n 14 /usr/local/bin/flclash-launcher
"""
    run_remote_script(client, verify_script, password, desc=f"在 {host} 验证字体匹配结果")


def main():
    init_env()

    parser = argparse.ArgumentParser(
        description="修复双工作站中文字体与 FlClash 字符乱码",
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
            fix_fonts_on_node(c0, host0, user0, pass0)
            verify_fonts_on_node(c0, host0, user0, pass0)
        finally:
            c0.close()

    if args.node in ("all", "node1"):
        c1 = get_ssh_client(host1, user1, pass1)
        try:
            fix_fonts_on_node(c1, host1, user1, pass1)
            verify_fonts_on_node(c1, host1, user1, pass1)
        finally:
            c1.close()

    logger.info("所有节点字体与 FlClash 字符乱码修复完成！")


if __name__ == "__main__":
    main()
