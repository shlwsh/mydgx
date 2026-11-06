#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/rdp_connect.py - 双工作站远程桌面（RDP）跨平台连接工具

功能：
1. 自动探测本机可用的 RDP 客户端（Windows mstsc / Linux FreeRDP+RDP/Remmina / macOS Microsoft Remote Desktop）；
2. 生成跨平台连接描述文件（Windows `.rdp`、Linux `.remmina`、通用 `.freerdp` 命令行参数）；
3. 直接以子进程方式拉起本机 RDP 客户端完成连接；
4. 端口连通性预检与远端 xrdp 会话状态巡检（SSH）；
5. 敏感凭证统一从项目根目录 `.env` 或系统环境变量读取，禁止硬编码。

用法:
    python scripts/rdp_connect.py --list                # 列出可用客户端与节点
    python scripts/rdp_connect.py --node all --check    # 仅做连通性与服务巡检
    python scripts/rdp_connect.py --node node0          # 连接 Node0
    python scripts/rdp_connect.py --node node1          # 连接 Node1
    python scripts/rdp_connect.py --node all --generate # 仅生成连接描述文件
    python scripts/rdp_connect.py --node node0 --client freerdp --size 1920x1080
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from env_helper import ROOT_DIR, setup_logger, init_env
except ImportError:  # 允许脚本脱离 env_helper 独立运行
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def setup_logger(name="RDP-Connect", level=20):
        import logging

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        return logging.getLogger(name)

    def init_env():
        return os.path.join(ROOT_DIR, ".env")


logger = setup_logger("RDP-Connect")

RDP_PORT = 3389

# ---------------------------------------------------------------- 节点定义
# 节点清单不包含任何密码，密码一律从 .env / 环境变量读取
NODES = {
    "node0": {
        "label": "工作站 Node0",
        "hostname": "cube-f22b",
        "ip_env": "NODE0_MGMT_IP",
        "ip_default": "172.19.50.70",
        "user_env": "NODE0_RDP_USER",
        "user_default": "winbot",
        "pass_env": "NODE0_RDP_PASS",
        "ssh_user_env": "SSH_NODE0_USER",
        "ssh_user_default": "winbot",
        "ssh_pass_env": "SSH_NODE0_PASS",
    },
    "node1": {
        "label": "工作站 Node1",
        "hostname": "cube-0137",
        "ip_env": "NODE1_MGMT_IP",
        "ip_default": "172.19.49.159",
        "user_env": "NODE1_RDP_USER",
        "user_default": "dgxdeploy",
        "pass_env": "NODE1_RDP_PASS",
        "ssh_user_env": "SSH_OPS_USER",
        "ssh_user_default": "dgxdeploy",
        "ssh_pass_env": "SSH_OPS_PASS",
    },
}


def node_ip(node: dict) -> str:
    return os.getenv(node["ip_env"], node["ip_default"]).strip()


def node_user(node: dict) -> str:
    return os.getenv(node["user_env"], node["user_default"]).strip()


def node_pass(node: dict) -> str:
    return os.getenv(node["pass_env"], "").strip()


# ------------------------------------------------------- 客户端探测
def find_freerdp() -> str:
    """查找 FreeRDP 3.x 客户端：优先系统 PATH，其次项目内便携目录。"""
    for name in ("xfreerdp3", "xfreerdp"):
        path = shutil.which(name)
        if path:
            return path

    local_dir = os.path.join(ROOT_DIR, ".cache_packages", "freerdp")
    if os.path.isdir(local_dir):
        for base in (os.path.join(local_dir, "usr", "bin"), local_dir):
            for name in ("xfreerdp3", "xfreerdp"):
                cand = os.path.join(base, name)
                if os.path.isfile(cand) and os.access(cand, os.X_OK):
                    return cand
    return ""


def find_clients() -> dict:
    """返回本机可用的 RDP 客户端清单。"""
    clients = {}
    if sys.platform == "win32":
        mstsc = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "mstsc.exe")
        if os.path.isfile(mstsc):
            clients["mstsc"] = mstsc
    else:
        for name, keys in (
            ("freerdp", ()),
            ("remmina", ()),
            ("rdesktop", ()),
        ):
            path = shutil.which(name)
            if path:
                clients[name] = path
        if "freerdp" not in clients:
            frdp = find_freerdp()
            if frdp:
                clients["freerdp"] = frdp

        if sys.platform == "darwin":
            for name in ("Microsoft Remote Desktop", "Windows App"):
                for app in (
                    f"/Applications/{name}.app",
                    os.path.expanduser(f"~/Applications/{name}.app"),
                ):
                    if os.path.isdir(app):
                        clients["msrdc"] = app
                        break
                if "msrdc" in clients:
                    break
    return clients


def freerdp_libdir(binary: str) -> str:
    """便携版 FreeRDP 的动态库目录（LD_LIBRARY_PATH）。"""
    local_dir = os.path.join(ROOT_DIR, ".cache_packages", "freerdp")
    libdir = os.path.join(local_dir, "usr", "lib", "aarch64-linux-gnu")
    if os.path.isdir(libdir):
        return libdir
    for cand in (
        os.path.join(os.path.dirname(os.path.dirname(binary)), "lib", "aarch64-linux-gnu"),
        os.path.join(os.path.dirname(os.path.dirname(binary)), "lib"),
    ):
        if os.path.isdir(cand):
            return cand
    return ""


# ------------------------------------------------------- 连接描述文件
def gen_windows_rdp(path: str, ip: str, user: str, width: int, height: int) -> None:
    """生成 Windows 原生 mstsc 连接文件。"""
    content = (
        f"full address:s:{ip}:{RDP_PORT}\r\n"
        f"username:s:{user}\r\n"
        "prompt for credentials:i:1\r\n"
        "administrative session:i:1\r\n"
        "screen mode id:i:2\r\n"
        "use multimon:i:0\r\n"
        f"desktopwidth:i:{width}\r\n"
        f"desktopheight:i:{height}\r\n"
        "session bpp:i:32\r\n"
        "compression:i:1\r\n"
        "keyboardhook:i:2\r\n"
        "audiocapturemode:i:0\r\n"
        "videoplaybackmode:i:1\r\n"
        "connection type:i:7\r\n"
        "networkautodetect:i:1\r\n"
        "bandwidthautodetect:i:1\r\n"
        "displayconnectionbar:i:1\r\n"
        "enableworkspacereconnect:i:0\r\n"
        "disable wallpaper:i:0\r\n"
        "allow font smoothing:i:1\r\n"
        "allow desktop composition:i:1\r\n"
        "disable full window drag:i:0\r\n"
        "disable menu anims:i:0\r\n"
        "disable themes:i:0\r\n"
        "disable cursor setting:i:0\r\n"
        "bitmapcachepersistenable:i:1\r\n"
        "authentication level:i:2\r\n"
        "negotiate security layer:i:1\r\n"
        "redirectclipboard:i:1\r\n"
        "drivestoredirect:s:*\r\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"已生成 Windows 连接文件: {path}")


def gen_remmina(path: str, ip: str, user: str, width: int, height: int, group: str = "双工作站") -> None:
    """生成 Linux Remmina 远程桌面配置（TOML 格式）。"""
    content = (
        '[remmina]\n'
        'name=' + group + '\n'
        'protocol=RDP\n'
        'server=' + ip + ':' + str(RDP_PORT) + '\n'
        'username=' + user + '\n'
        'password=\n'
        'domain=\n'
        'group=' + group + '\n'
        'resolution=' + str(width) + 'x' + str(height) + '\n'
        'viewmode=1\n'
        'viewscale=0\n'
        'colordepth=32\n'
        'quality=0\n'
        'compression=0\n'
        'bitmapcache=1\r\n'
        'keyboardhook=1\r\n'
        'gateway=0\r\n'
        'multitransport=0\r\n'
        'sound=1\r\n'
        'microphone=0\r\n'
        'rdpdrives=1\r\n'
        'rdpclipboard=1\r\n'
        'freerdp-extraargs=\r\n'
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"已生成 Remmina 连接配置: {path}")


def gen_freerdp_args(ip: str, user: str, pwd: str, width: int, height: int) -> list:
    """构造跨平台通用 FreeRDP 命令行参数。"""
    args = [
        f"/v:{ip}:{RDP_PORT}",
        f"/u:{user}",
        "/cert:ignore",
        f"/size:{width}x{height}",
        "/dynamic-resolution",
        "/clipboard",
        "/sound",
        "+auto-reconnect",
        "/timeout:15000",
    ]
    if pwd:
        args.insert(2, f"/p:{pwd}")
    return args


# ------------------------------------------------------- 连通性与巡检
def test_tcp_port(ip: str, port: int = RDP_PORT, timeout: int = 3) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


def remote_xrdp_status(ip: str, ssh_user: str, ssh_pass: str) -> str:
    """通过 SSH 读取远端 xrdp 服务状态，返回可读摘要。"""
    try:
        import paramiko
    except ImportError:
        return "paramiko 未安装，跳过 SSH 巡检"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ip,
            port=22,
            username=ssh_user,
            password=ssh_pass or None,
            timeout=12,
        )
        cmd = (
            "printf 'xrdp=%s\\n' \"$(systemctl is-active xrdp 2>/dev/null)\"; "
            "printf 'sesman=%s\\n' \"$(systemctl is-active xrdp-sesman 2>/dev/null)\"; "
            "printf 'listen=%s\\n' \"$(ss -tln 2>/dev/null | grep -c ':3389')\"; "
            "printf 'sessions=%s\\n' \"$(ls /tmp/.X11-unix 2>/dev/null | tr '\\n' ' ')\""
        )
        _, stdout, _ = client.exec_command(cmd)
        return stdout.read().decode("utf-8", "ignore").strip()
    except Exception as exc:  # noqa: BLE001
        return f"SSH 巡检失败: {exc}"
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------- 连接执行
def launch_freerdp(binary: str, ip: str, user: str, pwd: str, width: int, height: int) -> int:
    args = [binary] + gen_freerdp_args(ip, user, pwd, width, height)
    env = os.environ.copy()
    libdir = freerdp_libdir(binary)
    if libdir:
        env["LD_LIBRARY_PATH"] = libdir + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    logger.info(f"启动 FreeRDP: {' '.join(a if '/p:' not in a else '/p:******' for a in args)}")
    return subprocess.call(args, env=env)


def launch_remmina(binary: str, ip: str, user: str, width: int, height: int,
                   profile_path: str = "") -> int:
    """启动 Remmina 图形客户端。

    优先使用已生成的连接描述文件（.remmina），缺失时回退到 URI 参数方式。
    同时强制 GDK_BACKEND=x11：在 Wayland 合成器会话下，Remmina 的 X11 窗口
    无法被 xwd 等 X11 截图工具抓取，强制 X11 后端可保证窗口可被正常观测。
    """
    env = os.environ.copy()
    env["GDK_BACKEND"] = "x11"

    if profile_path and os.path.isfile(profile_path):
        logger.info(f"启动 Remmina 连接 {ip}:{RDP_PORT} (用户 {user}) [配置文件: {os.path.basename(profile_path)}]")
        return subprocess.call([binary, "-c", profile_path], env=env)

    logger.info(f"启动 Remmina 连接 {ip}:{RDP_PORT} (用户 {user})")
    uri = f"rdp://{user}@{ip}:{RDP_PORT}"
    return subprocess.call([binary, uri], env=env)


def launch_rdesktop(binary: str, ip: str, user: str, pwd: str, width: int, height: int) -> int:
    args = [binary, "-u", user, "-g", f"{width}x{height}", "-P"]
    if pwd:
        args.append(pwd)
    args.append(f"{ip}:{RDP_PORT}")
    logger.info(f"启动 rdesktop 连接 {ip}:{RDP_PORT}")
    return subprocess.call(args)


def launch_mstsc(binary: str, ip: str, user: str, width: int, height: int) -> int:
    logger.info(f"启动 Windows 远程桌面 (mstsc) 连接 {ip}:{RDP_PORT}")
    return subprocess.call([binary, f"{ip}:{RDP_PORT}"])


def launch_msrdc(app: str, ip: str, user: str) -> int:
    logger.info(f"启动 macOS Microsoft Remote Desktop 连接 {ip}:{RDP_PORT}")
    return subprocess.call(["open", "-a", app, f"ms-rd:\\{ip}"])


def launch(clients: dict, client: str, ip: str, user: str, pwd: str, width: int, height: int,
           profile_path: str = "") -> int:
    """按优先级选择客户端并建立连接。"""
    order = ["freerdp", "remmina", "rdesktop", "mstsc", "msrdc"] if client == "auto" else [client]

    for name in order:
        binary = clients.get(name)
        if not binary:
            continue
        if name == "freerdp":
            return launch_freerdp(binary, ip, user, pwd, width, height)
        if name == "remmina":
            return launch_remmina(binary, ip, user, width, height, profile_path)
        if name == "rdesktop":
            return launch_rdesktop(binary, ip, user, pwd, width, height)
        if name == "mstsc":
            return launch_mstsc(binary, ip, user, width, height)
        if name == "msrdc":
            return launch_msrdc(binary, ip, user)

    logger.error("本机未发现可用 RDP 客户端，请先安装："
                 "Linux → apt install freerdp3-x11 / remmina remmina-plugin-rdp；"
                 "Windows → 系统自带 mstsc；macOS → Microsoft Remote Desktop")
    return 127


# ------------------------------------------------------- 主流程
def main() -> int:
    parser = argparse.ArgumentParser(
        description="双工作站远程桌面（RDP）跨平台连接工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--node", choices=["all", "node0", "node1"], default="all",
                        help="目标节点: node0 / node1 / all (默认 all)")
    parser.add_argument("--client", choices=["auto", "freerdp", "remmina", "rdesktop", "mstsc", "msrdc"],
                        default="auto", help="指定 RDP 客户端，默认自动探测")
    parser.add_argument("--size", default="1920x1080", help="桌面分辨率, 形如 1920x1080")
    parser.add_argument("--generate", action="store_true", help="仅生成连接描述文件，不发起连接")
    parser.add_argument("--check", action="store_true", help="仅执行端口连通性与远端服务巡检")
    parser.add_argument("--list", action="store_true", help="列出本机可用客户端与节点信息")
    args = parser.parse_args()

    init_env()

    try:
        width, height = (int(v) for v in args.size.lower().split("x", 1))
    except ValueError:
        logger.error(f"--size 格式非法: {args.size}，应为 宽x高，例如 1920x1080")
        return 2

    clients = find_clients()
    targets = list(NODES.keys()) if args.node == "all" else [args.node]

    if args.list:
        logger.info("=" * 60)
        logger.info("本机 RDP 客户端探测结果:")
        if clients:
            for name, path in clients.items():
                logger.info(f"  [可用] {name:<10} {path}")
        else:
            logger.warning("  未探测到任何 RDP 客户端")
        logger.info("-" * 60)
        logger.info("节点清单:")
        for key in NODES:
            node = NODES[key]
            logger.info(f"  {key}: {node['label']} ({node['hostname']}) -> {node_ip(node)}:{RDP_PORT}"
                        f" 用户 {node_user(node)}")
        logger.info("=" * 60)
        return 0

    out_dir = os.path.join(ROOT_DIR, "scripts")
    os.makedirs(out_dir, exist_ok=True)

    for key in targets:
        node = NODES[key]
        ip = node_ip(node)
        user = node_user(node)
        pwd = node_pass(node)

        logger.info("=" * 60)
        logger.info(f"{node['label']} ({node['hostname']}) -> {ip}:{RDP_PORT}")
        logger.info("=" * 60)

        # 1) TCP 连通性
        ok = test_tcp_port(ip)
        logger.info(f"[端口检测] {ip}:{RDP_PORT} -> {'[成功 PASS]' if ok else '[失败 FAIL]'}")

        # 2) 远端 xrdp 服务巡检
        ssh_user = os.getenv(node["ssh_user_env"], node["ssh_user_default"]).strip()
        ssh_pass = os.getenv(node["ssh_pass_env"], "").strip()
        logger.info("[远端巡检] " + remote_xrdp_status(ip, ssh_user, ssh_pass).replace("\n", " | "))

        if args.check:
            continue

        # 3) 生成连接描述文件
        tag = node["label"].split()[-1]
        rdp_path = os.path.join(out_dir, f"工作站_{tag}_{ip}.rdp")
        remmina_path = os.path.join(out_dir, f"工作站_{tag}_{ip}.remmina")
        gen_windows_rdp(rdp_path, ip, user, width, height)
        gen_remmina(remmina_path, ip, user, width, height, group=f"工作站_{tag}")

        if args.generate:
            continue

        if not ok:
            logger.error(f"{node['label']} 端口不可达，跳过连接")
            continue

        launch(clients, args.client, ip, user, pwd, width, height, profile_path=remmina_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
