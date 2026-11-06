#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/install_software.py - 双工作站 OpenCode 与 Clash (FlClash) 自动化安装与配置脚本

支持目标：
- Node0 工作站 (172.19.50.70)
- Node1 工作站 (172.19.49.159)

安装内容：
1. OpenCode (opencode-ai):
   - 从国内高速源 (npmmirror) 获取针对 aarch64 (Linux ARM64) 编译的原生二进制并部署到 /usr/local/bin/opencode；
   - 创建桌面与应用程序菜单启动项 (opencode.desktop)，使 RDP 远程桌面支持一键点击启动。
2. Clash 代理软件 (FlClash + Mihomo/ClashCore):
   - 同步部署统一的 FlClash 图标及桌面客户端 (基于 ClashMeta)；
   - 软链接 /usr/local/bin/clash 与 /usr/local/bin/mihomo，满足命令行与后台运维需求；
   - 在各用户 Desktop 配置已信任的快捷方式。
"""

import os
import sys
import argparse
import urllib.request
import tarfile
import io

# 导入通用环境引导模块（自动切换 .venv 并初始化）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env, ROOT_DIR

import paramiko

logger = setup_logger("InstallSoftware")

OPENCODE_TGZ_URL = "https://registry.npmmirror.com/opencode-linux-arm64/-/opencode-linux-arm64-1.19.5.tgz"

OPENCODE_DESKTOP_CONTENT = """[Desktop Entry]
Name=OpenCode
Comment=OpenCode AI Assistant Terminal
Exec=gnome-terminal --title="OpenCode AI" -- opencode
Icon=utilities-terminal
Terminal=false
Type=Application
Categories=Development;
"""


def get_ssh_client(host, user, password, timeout=15):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=22, username=user, password=password, timeout=timeout)
    return client


def run_remote_script(client, script_text, sudo_pass, desc=None):
    """通过 SFTP 上传并以 root 执行脚本，避免任何 shell 引号与换行转义问题"""
    if desc:
        logger.info(f"==> {desc}")
    remote_sh = "/tmp/_run_task.sh"
    sftp = client.open_sftp()
    with sftp.file(remote_sh, "w") as f:
        # 确保使用 Unix LF 换行
        f.write("#!/usr/bin/env bash\nset -euo pipefail\n" + script_text.replace("\r\n", "\n") + "\n")
    sftp.close()

    stdin, stdout, stderr = client.exec_command(f"echo {sudo_pass} | sudo -S chmod +x {remote_sh} && echo {sudo_pass} | sudo -S {remote_sh}")
    out = stdout.read().decode("utf-8").strip()
    err = stderr.read().decode("utf-8").strip()
    code = stdout.channel.recv_exit_status()

    # 清理临时脚本
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


def prepare_opencode_binary(cache_dir):
    """从 npmmirror 下载并提取 Linux aarch64 版本的 opencode 独立可执行文件"""
    bin_path = os.path.join(cache_dir, "opencode_arm64")
    if os.path.isfile(bin_path) and os.path.getsize(bin_path) > 100 * 1024 * 1024:
        logger.info(f"使用本地已缓存的 OpenCode 二进制: {bin_path} ({os.path.getsize(bin_path) / 1024 / 1024:.1f} MB)")
        return bin_path

    logger.info(f"正在从高速镜像源下载 OpenCode Linux ARM64 二进制: {OPENCODE_TGZ_URL} ...")
    req = urllib.request.Request(OPENCODE_TGZ_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()

    logger.info(f"下载完成 ({len(data) / 1024 / 1024:.1f} MB)，正在解压提取 package/bin/opencode ...")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = tar.getmember("package/bin/opencode")
        extracted_f = tar.extractfile(member)
        with open(bin_path, "wb") as out_f:
            out_f.write(extracted_f.read())

    logger.info(f"OpenCode 二进制准备就绪: {bin_path} ({os.path.getsize(bin_path) / 1024 / 1024:.1f} MB)")
    return bin_path


def prepare_flclash_deb(cache_dir, node1_host, node1_user, node1_pass):
    """从 Node1 获取已通过 dpkg-repack 打包的标准 FlClash deb 安装包"""
    deb_path = os.path.join(cache_dir, "flclash_arm64.deb")
    if os.path.isfile(deb_path) and os.path.getsize(deb_path) > 30 * 1024 * 1024:
        logger.info(f"使用本地已缓存的 FlClash deb 包: {deb_path} ({os.path.getsize(deb_path) / 1024 / 1024:.1f} MB)")
        return deb_path

    logger.info(f"正在从 Node1 ({node1_host}) 提取 FlClash deb 包...")
    c1 = get_ssh_client(node1_host, node1_user, node1_pass)
    try:
        run_remote_script(c1, "dpkg-repack flclash", node1_pass, desc="在 Node1 打包 flclash")
        sftp = c1.open_sftp()
        # 寻找生成的 deb
        remote_debs = [f for f in sftp.listdir(".") if f.startswith("flclash_") and f.endswith(".deb")]
        if not remote_debs:
            remote_debs = [f for f in sftp.listdir("/tmp") if f.startswith("flclash_") and f.endswith(".deb")]
            remote_deb = f"/tmp/{remote_debs[0]}"
        else:
            remote_deb = remote_debs[0]

        logger.info(f"正在下载远程 deb 包: {remote_deb} -> {deb_path} ...")
        sftp.get(remote_deb, deb_path)
        sftp.close()
        logger.info(f"FlClash deb 包已成功缓存至本地: {deb_path} ({os.path.getsize(deb_path) / 1024 / 1024:.1f} MB)")
    finally:
        c1.close()

    return deb_path


def install_opencode_on_node(client, host, user, password, local_opencode_bin, target_users):
    logger.info(f"--> [OpenCode] 正在安装至节点 {host} ...")
    
    # 检查是否已部署且版本一致
    stdin, stdout, _ = client.exec_command("/usr/local/bin/opencode --version 2>/dev/null || true")
    cur_ver = stdout.read().decode().strip()
    if "1.19.5" in cur_ver:
        logger.info(f"节点 {host} 上已是最新版 OpenCode ({cur_ver})，跳过上传")
    else:
        sftp = client.open_sftp()
        remote_tmp_bin = "/tmp/opencode_bin"
        logger.info(f"上传 OpenCode 二进制至 {remote_tmp_bin} ...")
        sftp.put(local_opencode_bin, remote_tmp_bin)
        sftp.close()

    sftp = client.open_sftp()
    remote_desktop_file = "/tmp/opencode.desktop"
    with sftp.file(remote_desktop_file, "w") as f:
        f.write(OPENCODE_DESKTOP_CONTENT)
    sftp.close()

    # 部署至 /usr/local/bin 与桌面快捷方式
    deploy_script = f"""
if [ -f /tmp/opencode_bin ]; then
    mv /tmp/opencode_bin /usr/local/bin/opencode
    chmod 755 /usr/local/bin/opencode
    echo "已部署 /usr/local/bin/opencode"
fi

if [ -f /tmp/opencode.desktop ]; then
    mv /tmp/opencode.desktop /usr/share/applications/opencode.desktop
    chmod 644 /usr/share/applications/opencode.desktop
    echo "已部署 /usr/share/applications/opencode.desktop"
fi

for u in {' '.join(target_users)}; do
    user_desktop="/home/$u/Desktop"
    if [ -d "$user_desktop" ]; then
        cp /usr/share/applications/opencode.desktop "$user_desktop/"
        chown $u:$u "$user_desktop/opencode.desktop"
        chmod +x "$user_desktop/opencode.desktop"
        gio set "$user_desktop/opencode.desktop" metadata::trusted true 2>/dev/null || true
        echo "已为用户 $u 部署 OpenCode 桌面快捷方式"
    fi
done
"""
    run_remote_script(client, deploy_script, password, desc=f"在 {host} 部署 OpenCode 二进制与桌面快捷方式")


def install_flclash_on_node(client, host, user, password, local_flclash_deb, target_users):
    logger.info(f"--> [Clash/FlClash] 正在配置节点 {host} ...")

    # 检查是否已安装
    stdin, stdout, _ = client.exec_command("dpkg -l | grep -q 'flclash' && echo 'INSTALLED' || echo 'NOT_INSTALLED'")
    res = stdout.read().decode().strip()

    if res != "INSTALLED":
        logger.info(f"在 {host} 上安装 FlClash deb 软件包...")
        sftp = client.open_sftp()
        remote_deb = "/tmp/flclash_install.deb"
        sftp.put(local_flclash_deb, remote_deb)
        sftp.close()

        # 使用 apt-get install 本地 deb 包以自动解决依赖
        install_script = f"""
apt-get update
apt-get install -y {remote_deb}
rm -f {remote_deb}
echo "FlClash deb 软件包安装成功"
"""
        run_remote_script(client, install_script, password, desc=f"在 {host} 执行 apt 安装 FlClash")
    else:
        logger.info(f"节点 {host} 已安装 FlClash，继续固化命令行与桌面快捷方式")

    # 配置软链接与桌面快捷方式
    link_script = f"""
if [ -f /usr/share/FlClash/FlClashCore ]; then
    ln -sf /usr/share/FlClash/FlClashCore /usr/local/bin/clash
    ln -sf /usr/share/FlClash/FlClashCore /usr/local/bin/mihomo
    echo "已建立命令行软链接: /usr/local/bin/clash -> /usr/share/FlClash/FlClashCore"
fi

for u in {' '.join(target_users)}; do
    user_desktop="/home/$u/Desktop"
    if [ -d "$user_desktop" ] && [ -f /usr/share/applications/FlClash.desktop ]; then
        cp /usr/share/applications/FlClash.desktop "$user_desktop/"
        chown $u:$u "$user_desktop/FlClash.desktop"
        chmod +x "$user_desktop/FlClash.desktop"
        gio set "$user_desktop/FlClash.desktop" metadata::trusted true 2>/dev/null || true
        echo "已为用户 $u 部署 FlClash 桌面快捷方式"
    fi
done
"""
    run_remote_script(client, link_script, password, desc=f"配置 clash 命令行与 FlClash 桌面快捷方式在 {host}")


def verify_installation(client, host, user, password, target_users):
    logger.info(f"==> 验证节点 {host} 软件安装结果...")
    verify_script = f"""
echo "=== 1. OpenCode 命令与版本 ==="
which opencode || echo "未找到 opencode 命令"
opencode --version 2>&1 || echo "执行 opencode 异常"

echo "=== 2. Clash 命令行与内核版本 ==="
which clash || echo "未找到 clash 命令"
clash -v 2>&1 || echo "执行 clash -v 异常"

echo "=== 3. FlClash GUI 安装状态 ==="
dpkg -l | grep flclash || echo "未安装 flclash deb"

echo "=== 4. 桌面快捷方式检查 ==="
for u in {' '.join(target_users)}; do
    echo "--- 用户 $u 桌面快捷方式 ---"
    ls -l /home/$u/Desktop/*.desktop 2>/dev/null || echo "无快捷方式"
done
"""
    run_remote_script(client, verify_script, password, desc=f"检查 {host} 软件与快捷方式")


def main():
    init_env()

    parser = argparse.ArgumentParser(
        description="双工作站 OpenCode 与 Clash (FlClash) 自动化安装工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--node",
        choices=["all", "node0", "node1"],
        default="all",
        help="目标节点: all, node0, node1 (默认: all)",
    )
    args = parser.parse_args()

    cache_dir = os.path.join(ROOT_DIR, ".cache_packages")
    os.makedirs(cache_dir, exist_ok=True)

    host0 = os.getenv("NODE0_MGMT_IP", "172.19.50.70")
    user0 = os.getenv("SSH_NODE0_USER", "winbot")
    pass0 = os.getenv("SSH_NODE0_PASS", "winbot")

    host1 = os.getenv("NODE1_MGMT_IP", "172.19.49.159")
    user1 = os.getenv("SSH_NODE1_USER", "dgxdeploy")
    pass1 = os.getenv("SSH_NODE1_PASS", "winning")

    logger.info("=" * 60)
    logger.info("开始准备 OpenCode 与 Clash (FlClash) 安装文件...")
    logger.info("=" * 60)

    # 1. 准备 OpenCode 原生 ARM64 二进制
    local_opencode_bin = prepare_opencode_binary(cache_dir)

    # 2. 准备 FlClash ARM64 deb 包 (从 Node1 提取并缓存)
    local_flclash_deb = prepare_flclash_deb(cache_dir, host1, user1, pass1)

    nodes_to_install = []
    if args.node in ("all", "node0"):
        nodes_to_install.append(("Node0 工作站", host0, user0, pass0, ["winbot"]))
    if args.node in ("all", "node1"):
        nodes_to_install.append(("Node1 工作站", host1, user1, pass1, ["dgxdeploy", "his_test"]))

    # 3. 依次对目标节点进行安装与配置
    for name, host, user, pwd, users in nodes_to_install:
        logger.info("=" * 60)
        logger.info(f"正在对 {name} ({host}) 执行 OpenCode 与 Clash 安装配置...")
        logger.info("=" * 60)

        c = get_ssh_client(host, user, pwd)
        try:
            # 安装 OpenCode
            install_opencode_on_node(c, host, user, pwd, local_opencode_bin, users)

            # 安装/配置 Clash (FlClash)
            install_flclash_on_node(c, host, user, pwd, local_flclash_deb, users)

            # 验证安装
            verify_installation(c, host, user, pwd, users)
        finally:
            c.close()

    logger.info("=" * 60)
    logger.info("所有工作站节点 OpenCode 与 Clash (FlClash) 安装部署顺利完成！")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
