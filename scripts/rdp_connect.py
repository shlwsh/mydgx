#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/rdp_connect.py - 三工作站远程桌面（RDP）跨平台连接工具

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
    python scripts/rdp_connect.py --node node123          # 连接 Node123
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
        # 高速互联口地址：管理网（WLAN）不可达时自动回退
        "fabric_ip_env": "NODE0_FABRIC_IP",
        "fabric_ip_default": "192.168.100.10",
    },
    "node1": {
        "label": "工作站 Node1",
        "hostname": "cube-0137",
        "ip_env": "NODE1_MGMT_IP",
        "ip_default": "172.19.49.159",
        "user_env": "NODE1_RDP_USER",
        "user_default": "his_test",
        "pass_env": "NODE1_RDP_PASS",
        "ssh_user_env": "SSH_OPS_USER",
        "ssh_user_default": "dgxdeploy",
        "ssh_pass_env": "SSH_OPS_PASS",
        # 高速互联口地址：管理网（WLAN）不可达时自动回退，
        # 避免节点 IP 漂移导致完全连不上。
        "fabric_ip_env": "NODE1_FABRIC_IP",
        "fabric_ip_default": "192.168.100.11",
    },
    "node123": {
        "label": "工作站 Node123",
        "hostname": "cube-fe5e",
        "ip_env": "NODE123_MGMT_IP",
        "ip_default": "172.19.51.123",
        "user_env": "NODE123_RDP_USER",
        "user_default": "bot",
        "pass_env": "NODE123_RDP_PASS",
        "ssh_user_env": "SSH_NODE123_USER",
        "ssh_user_default": "bot",
        "ssh_pass_env": "SSH_NODE123_PASS",
    },
}


def node_fabric_ip(node: dict) -> str:
    """读取节点的高速互联口地址（可能为空表示未配置）。"""
    env_key = node.get("fabric_ip_env")
    if not env_key:
        return ""
    return os.getenv(env_key, node.get("fabric_ip_default", "")).strip()


def resolve_node_addr(node: dict, timeout: int = 5) -> tuple:
    """解析节点可用地址。

    优先使用管理网 IP；若不可达则回退到高速互联口 IP。
    返回 (地址, 来源描述)，两者皆不可达时地址为空串。

    说明：高速互联口（192.168.100.0/24）仅在 Node0↔Node1 之间物理直连，
    普通管理机并无该网卡，因此本机直连高速口通常不可达——此情形下
    调用方需借道 Node0 跳板建立隧道（见 open_jump_tunnel）。
    """
    mgmt_ip = node_ip(node)
    if mgmt_ip and probe_rdp_service(mgmt_ip, timeout=timeout):
        return mgmt_ip, "管理网"

    fabric_ip = node_fabric_ip(node)
    if fabric_ip:
        logger.warning(f"管理网地址 {mgmt_ip} 不可达，回退到高速互联口 {fabric_ip}")
        return fabric_ip, "高速互联口(需跳板)"

    return mgmt_ip, "管理网(不可达)"


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
    """生成 Windows 原生 mstsc 连接文件（深度局域网极速调优版）。"""
    content = (
        f"full address:s:{ip}:{RDP_PORT}\r\n"
        f"username:s:{user}\r\n"
        "prompt for credentials:i:1\r\n"
        "administrative session:i:0\r\n"
        "screen mode id:i:2\r\n"
        "use multimon:i:0\r\n"
        f"desktopwidth:i:{width}\r\n"
        f"desktopheight:i:{height}\r\n"
        "session bpp:i:24\r\n"
        "compression:i:1\r\n"
        "keyboardhook:i:2\r\n"
        "audiocapturemode:i:0\r\n"
        "videoplaybackmode:i:1\r\n"
        "connection type:i:6\r\n"
        "networkautodetect:i:0\r\n"
        "bandwidthautodetect:i:0\r\n"
        "displayconnectionbar:i:1\r\n"
        "enableworkspacereconnect:i:1\r\n"
        "disable wallpaper:i:1\r\n"
        "allow font smoothing:i:1\r\n"
        "allow desktop composition:i:0\r\n"
        "disable full window drag:i:1\r\n"
        "disable menu anims:i:1\r\n"
        "disable themes:i:0\r\n"
        "disable cursor setting:i:0\r\n"
        "bitmapcachepersistenable:i:1\r\n"
        "authentication level:i:0\r\n"
        "negotiate security layer:i:1\r\n"
        "redirectclipboard:i:1\r\n"
        "drivestoredirect:s:\r\n"
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


def gen_freerdp_args(ip: str, user: str, pwd: str, width: int, height: int,
                     dynamic_resolution: bool = False, gfx: str = "rfx",
                     bpp: int = 24, local_port: int = 0,
                     kbd_layout: str = "0x00000409") -> list:
    """构造跨平台通用 FreeRDP 命令行参数。

    默认走最兼容模式（固定分辨率 + RemoteFX + bpp 24），与远端
    xrdp 0.9.x / xorgxrdp 0.9.x 的握手成功率最高；如需自适应分辨率，
    显式传入 dynamic_resolution=True（对应 --dynamic-resolution）。

    local_port 非 0 时连接本机该端口（用于跳板转发场景）。
    """
    target = f"127.0.0.1:{local_port}" if local_port else f"{ip}:{RDP_PORT}"
    args = [
        f"/v:{target}",
        f"/u:{user}",
        "/cert:ignore",
        f"/size:{width}x{height}",
        f"/bpp:{bpp}",
        "/clipboard",
        "/sound",
        "+auto-reconnect",
        "/timeout:15000",
    ]
    if gfx == "rfx":
        args.append("/rfx")
    elif gfx == "gfx":
        args.append("/gfx:avc444")
    # gfx == "auto" 时不追加编码参数，由 FreeRDP 自行协商
    if dynamic_resolution:
        args.append("/dynamic-resolution")
    # 固定键盘布局为美式：客户端若上报中文布局(0x00000804)，服务端无对应
    # keymap 会回退到 km-00000409.ini，且 xrdp 登录窗位图字体无法渲染中文，
    # 表现为密码框"无焦点"、无法输入。统一锁定 us 布局可规避。
    if kbd_layout:
        args.append(f"/kbd:layout:{kbd_layout}")
    if pwd:
        args.insert(2, f"/p:{pwd}")
    return args


# ------------------------------------------------------- 连通性与巡检
def probe_rdp_service(ip: str, port: int = RDP_PORT, timeout: int = 5) -> bool:
    """通过 RDP 协议握手判定对端是否为真实 RDP 服务。

    单纯的 TCP 连接检测在 Clash TUN 模式下不可靠：本机存在 TUN 虚拟网卡时，
    所有被路由到代理的连接都会「假性」建立成功，导致不存在的地址也被判定为可达。
    因此改为发送 X.224 Connection Request 并校验响应类型字节（0x03）。
    """
    req = b"\x01\x00\x08\x00" + b"\x03\x00\x00\x00" + b"\x01\x00\x08\x00"
    try:
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(req)
            resp = sock.recv(19)
            return len(resp) >= 4 and resp[0] == 0x03
    except (socket.timeout, OSError):
        return False


def test_tcp_port(ip: str, port: int = RDP_PORT, timeout: int = 3) -> bool:
    """TCP 端口可达性检测（仅作辅助，代理环境下可能假阳性）。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


def remote_xrdp_status(ip: str, ssh_user: str, ssh_pass: str, jump=None) -> str:
    """通过 SSH 读取远端 xrdp 服务状态，返回可读摘要。

    传入 jump（已连接的 paramiko SSHClient）时，经该跳板建立隧道访问目标。
    """
    try:
        import paramiko
    except ImportError:
        return "paramiko 未安装，跳过 SSH 巡检"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if jump is not None:
            channel = jump.get_transport().open_channel(
                "direct-tcpip", (ip, 22), ("127.0.0.1", 0)
            )
            client.connect(hostname=ip, port=22, username=ssh_user,
                           password=ssh_pass or None, sock=channel, timeout=15)
        else:
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


def open_jump_tunnel(node: dict):
    """尝试经 Node0 跳板建立到目标高速口的 SSH 隧道。

    高速互联口（192.168.100.0/24）仅在 Node0↔Node1 之间物理直连，
    普通管理机并无该网卡，因此需借道 Node0 才能触达。

    返回 (jump_client, target_client) 或 (None, None)。
    """
    try:
        import paramiko
    except ImportError:
        return None, None

    jump_ip = os.getenv("NODE0_MGMT_IP", "172.19.50.70").strip()
    jump_user = os.getenv("SSH_NODE0_USER", "winbot").strip()
    jump_pass = os.getenv("SSH_NODE0_PASS", "").strip()
    target_ip = node_fabric_ip(node)
    target_user = os.getenv(node["ssh_user_env"], node["ssh_user_default"]).strip()
    target_pass = os.getenv(node["ssh_pass_env"], "").strip()

    if not target_ip:
        return None, None

    jump = paramiko.SSHClient()
    jump.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        jump.connect(hostname=jump_ip, port=22, username=jump_user,
                     password=jump_pass or None, timeout=15)
        channel = jump.get_transport().open_channel(
            "direct-tcpip", (target_ip, 22), ("127.0.0.1", 0)
        )
        target = paramiko.SSHClient()
        target.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        target.connect(hostname=target_ip, port=22, username=target_user,
                       password=target_pass or None, sock=channel, timeout=15)
        return jump, target
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"经 Node0 跳板连接高速口 {target_ip} 失败: {exc}")
        try:
            jump.close()
        except Exception:  # noqa: BLE001
            pass
        return None, None


def start_local_rdp_forward(node: dict, local_port: int = 3390) -> tuple:
    """经 Node0 跳板把目标节点的高速口 RDP 转发到本机本地端口。

    返回 (jump_client, 本地监听端口) 或 (None, 0)。
    """
    try:
        import paramiko
    except ImportError:
        return None, 0

    jump_ip = os.getenv("NODE0_MGMT_IP", "172.19.50.70").strip()
    jump_user = os.getenv("SSH_NODE0_USER", "winbot").strip()
    jump_pass = os.getenv("SSH_NODE0_PASS", "").strip()
    target_ip = node_fabric_ip(node)
    if not target_ip:
        return None, 0

    jump = paramiko.SSHClient()
    jump.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        jump.connect(hostname=jump_ip, port=22, username=jump_user,
                     password=jump_pass or None, timeout=15)
        transport = jump.get_transport()

        def forward(channel, origin, dest):
            chan = None
            try:
                chan = transport.open_channel("direct-tcpip", (target_ip, RDP_PORT), origin)
                while True:
                    data = channel.recv(32768)
                    if not data:
                        break
                    chan.sendall(data)
                    data = chan.recv(32768)
                    if not data:
                        break
                    channel.sendall(data)
            except Exception:  # noqa: BLE001
                pass
            finally:
                for sock in (chan, channel):
                    try:
                        if sock is not None:
                            sock.close()
                    except Exception:  # noqa: BLE001
                        pass

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", local_port))
        server.listen(8)
        server.settimeout(1.0)

        import threading

        def serve():
            while True:
                try:
                    conn, addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=forward, args=(conn, addr, None), daemon=True).start()

        threading.Thread(target=serve, daemon=True).start()
        logger.info(f"已建立 RDP 转发: 127.0.0.1:{local_port} -> {target_ip}:{RDP_PORT} (经 Node0 跳板)")
        return jump, local_port
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"建立 RDP 跳板转发失败: {exc}")
        try:
            jump.close()
        except Exception:  # noqa: BLE001
            pass
        return None, 0


# ------------------------------------------------------- 连接执行
def launch_freerdp(binary: str, ip: str, user: str, pwd: str, width: int, height: int,
                   dynamic_resolution: bool = False, gfx: str = "rfx",
                   bpp: int = 24, local_port: int = 0,
                   kbd_layout: str = "0x00000409") -> int:
    args = [binary] + gen_freerdp_args(ip, user, pwd, width, height,
                                       dynamic_resolution=dynamic_resolution,
                                       gfx=gfx, bpp=bpp, local_port=local_port,
                                       kbd_layout=kbd_layout)
    env = os.environ.copy()
    libdir = freerdp_libdir(binary)
    if libdir:
        env["LD_LIBRARY_PATH"] = libdir + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    logger.info(f"启动 FreeRDP: {' '.join(a if '/p:' not in a else '/p:******' for a in args)}")
    return subprocess.call(args, env=env)


def launch_remmina(binary: str, ip: str, user: str, width: int, height: int,
                   profile_path: str = "", local_port: int = 0) -> int:
    """启动 Remmina 图形客户端。

    优先使用已生成的连接描述文件（.remmina），缺失时回退到 URI 参数方式。
    同时强制 GDK_BACKEND=x11：在 Wayland 合成器会话下，Remmina 的 X11 窗口
    无法被 xwd 等 X11 截图工具抓取，强制 X11 后端可保证窗口可被正常观测。
    """
    env = os.environ.copy()
    env["GDK_BACKEND"] = "x11"

    target = f"127.0.0.1:{local_port}" if local_port else f"{ip}:{RDP_PORT}"

    if profile_path and os.path.isfile(profile_path) and not local_port:
        logger.info(f"启动 Remmina 连接 {target} (用户 {user}) [配置文件: {os.path.basename(profile_path)}]")
        return subprocess.call([binary, "-c", profile_path], env=env)

    logger.info(f"启动 Remmina 连接 {target} (用户 {user})")
    uri = f"rdp://{user}@{target}"
    return subprocess.call([binary, uri], env=env)


def launch_rdesktop(binary: str, ip: str, user: str, pwd: str, width: int, height: int) -> int:
    args = [binary, "-u", user, "-g", f"{width}x{height}", "-P"]
    if pwd:
        args.append(pwd)
    args.append(f"{ip}:{RDP_PORT}")
    logger.info(f"启动 rdesktop 连接 {ip}:{RDP_PORT}")
    return subprocess.call(args)


def launch_mstsc(binary: str, ip: str, user: str, width: int, height: int, profile_path: str = "") -> int:
    cmd = [binary, profile_path] if profile_path and os.path.isfile(profile_path) else [binary, f"{ip}:{RDP_PORT}"]
    logger.info(f"启动 Windows 远程桌面 (mstsc) 连接 {ip}:{RDP_PORT} [配置文件: {os.path.basename(profile_path) if profile_path else '无'}]")
    # Windows 下以非阻塞 Popen 拉起独立 GUI 窗口，避免命令行终端被阻塞卡死
    subprocess.Popen(cmd)
    logger.info("Windows 远程桌面客户端已在独立窗口拉起，连接就绪")
    return 0


def launch_msrdc(app: str, ip: str, user: str) -> int:
    logger.info(f"启动 macOS Microsoft Remote Desktop 连接 {ip}:{RDP_PORT}")
    return subprocess.call(["open", "-a", app, f"ms-rd:\\{ip}"])


def launch(clients: dict, client: str, ip: str, user: str, pwd: str, width: int, height: int,
           profile_path: str = "", dynamic_resolution: bool = False,
           gfx: str = "rfx", bpp: int = 24, local_port: int = 0,
           kbd_layout: str = "0x00000409") -> int:
    """按优先级选择客户端并建立连接。"""
    order = ["freerdp", "remmina", "rdesktop", "mstsc", "msrdc"] if client == "auto" else [client]

    for name in order:
        binary = clients.get(name)
        if not binary:
            continue
        if name == "freerdp":
            return launch_freerdp(binary, ip, user, pwd, width, height,
                                  dynamic_resolution=dynamic_resolution,
                                  gfx=gfx, bpp=bpp, local_port=local_port,
                                  kbd_layout=kbd_layout)
        if name == "remmina":
            return launch_remmina(binary, ip, user, width, height, profile_path, local_port=local_port)
        if name == "rdesktop":
            return launch_rdesktop(binary, ip, user, pwd, width, height)
        if name == "mstsc":
            return launch_mstsc(binary, ip, user, width, height, profile_path)
        if name == "msrdc":
            return launch_msrdc(binary, ip, user)

    logger.error("本机未发现可用 RDP 客户端，请先安装："
                 "Linux → apt install freerdp3-x11 / remmina remmina-plugin-rdp；"
                 "Windows → 系统自带 mstsc；macOS → Microsoft Remote Desktop")
    return 127


# ------------------------------------------------------- 主流程
def main() -> int:
    parser = argparse.ArgumentParser(
        description="三工作站远程桌面（RDP）跨平台连接工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--node", choices=["all", "node0", "node1", "node123"], default="all",
                        help="目标节点: node0 / node1 / node123 / all (默认 all)")
    parser.add_argument("--client", choices=["auto", "freerdp", "remmina", "rdesktop", "mstsc", "msrdc"],
                        default="auto", help="指定 RDP 客户端，默认自动探测")
    parser.add_argument("--size", default="1920x1080", help="桌面分辨率, 形如 1920x1080")
    parser.add_argument("--dynamic-resolution", action="store_true",
                        help="启用动态分辨率自适应（默认关闭；远端 xrdp 0.9.x 兼容性差，开启后可能握手失败）")
    parser.add_argument("--gfx", choices=["rfx", "gfx", "auto"], default="rfx",
                        help="图形编码: rfx=RemoteFX(默认，最兼容) / gfx=AVC444 / auto=由客户端协商")
    parser.add_argument("--bpp", type=int, choices=[16, 24, 32], default=24,
                        help="色深(默认 24，与服务端 sesman 配置一致)")
    parser.add_argument("--kbd", default="0x00000409",
                        help="键盘布局(默认 us。中文布局 0x00000804 在服务端无对应 keymap，"
                             "会导致 xrdp 登录窗无法输入)")
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
            fabric = node_fabric_ip(node)
            extra = f" | 高速口 {fabric}" if fabric else ""
            logger.info(f"  {key}: {node['label']} ({node['hostname']}) -> {node_ip(node)}:{RDP_PORT}"
                        f"{extra} 用户 {node_user(node)}")
        logger.info("=" * 60)
        return 0

    out_dir = os.path.join(ROOT_DIR, "scripts")
    os.makedirs(out_dir, exist_ok=True)

    for key in targets:
        node = NODES[key]
        user = node_user(node)
        pwd = node_pass(node)

        # 0) 地址解析：管理网优先，不可达时回退高速互联口（需经 Node0 跳板）
        ip, addr_src = resolve_node_addr(node)
        use_jump = addr_src.startswith("高速互联口")

        logger.info("=" * 60)
        logger.info(f"{node['label']} ({node['hostname']}) -> {ip}:{RDP_PORT} [{addr_src}]")
        logger.info("=" * 60)

        # 1) RDP 服务可达性（协议级握手校验，避免代理环境假阳性）
        ok = probe_rdp_service(ip)
        logger.info(f"[服务探测] {ip}:{RDP_PORT} -> {'[成功 PASS]' if ok else '[失败 FAIL]'}")

        # 2) 远端 xrdp 服务巡检（高速口场景经 Node0 跳板）
        ssh_user = os.getenv(node["ssh_user_env"], node["ssh_user_default"]).strip()
        ssh_pass = os.getenv(node["ssh_pass_env"], "").strip()
        jump = None
        if use_jump:
            jump, _ = open_jump_tunnel(node)
            if jump is None:
                logger.error(f"{node['label']} 跳板隧道建立失败，跳过该节点")
                continue
            logger.info("已通过 Node0 跳板访问目标高速互联口")
            status = remote_xrdp_status(ip, ssh_user, ssh_pass, jump=jump)
        else:
            status = remote_xrdp_status(ip, ssh_user, ssh_pass)
        logger.info("[远端巡检] " + status.replace("\n", " | "))

        if args.check:
            if jump is not None:
                jump.close()
            continue

        # 3) 生成连接描述文件
        tag = node["label"].split()[-1]
        rdp_path = os.path.join(out_dir, f"工作站_{tag}_{ip}.rdp")
        remmina_path = os.path.join(out_dir, f"工作站_{tag}_{ip}.remmina")
        gen_windows_rdp(rdp_path, ip, user, width, height)
        gen_remmina(remmina_path, ip, user, width, height, group=f"工作站_{tag}")

        if args.generate:
            if jump is not None:
                jump.close()
            continue

        if not ok:
            logger.error(f"{node['label']} 服务不可达，跳过连接")
            if jump is not None:
                jump.close()
            continue

        if use_jump:
            # 高速口仅节点间直连，本机无该网卡：建立本地 RDP 转发后连本地端口
            jump2, local_port = start_local_rdp_forward(node)
            if jump2 is None:
                logger.error(f"{node['label']} RDP 转发建立失败，跳过连接")
                if jump is not None:
                    jump.close()
                continue
            try:
                launch(clients, args.client, "127.0.0.1", user, pwd, width, height,
                       profile_path=(rdp_path if sys.platform == "win32" else remmina_path),
                       dynamic_resolution=args.dynamic_resolution,
                       gfx=args.gfx, bpp=args.bpp,
                       local_port=local_port, kbd_layout=args.kbd)
            finally:
                jump2.close()
            if jump is not None:
                jump.close()
            continue

        target_profile = rdp_path if sys.platform == "win32" else remmina_path
        launch(clients, args.client, ip, user, pwd, width, height, profile_path=target_profile,
               dynamic_resolution=args.dynamic_resolution, gfx=args.gfx, bpp=args.bpp,
               kbd_layout=args.kbd)

    return 0


if __name__ == "__main__":
    sys.exit(main())
