#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/fix_chatgpt_app.py - ChatGPT/Codex 桌面应用启动修复工具

背景：
    本机桌面应用通过 Clash(http://127.0.0.1:7890) 代理访问外网，但
    GNOME 系统代理长期处于 none，导致 Electron(Chromium) 网络栈绕过
    代理直连 DNS，出现 ERR_NAME_NOT_RESOLVED，应用白屏/卡死。另有一次
    异常退出后残留 Chromium 单实例锁，后续启动会被误判为“已有实例”。

功能：
    1. 将 GNOME 系统代理设置为可用的本地 HTTP 代理（供 Electron 使用）；
    2. 清理异常退出后残留的 Chromium 单实例锁（Singleton*）；
    3. 可选地直接拉起 ChatGPT 应用，并透传 URL 等参数；
    4. --check 模式输出当前代理、单实例锁与进程状态。

用法示例：
    scripts/fix_chatgpt_app.py apply          # 仅修复系统代理与残留锁
    scripts/fix_chatgpt_app.py check          # 查看当前状态
    scripts/fix_chatgpt_app.py launch [URL]   # 修复后启动应用
"""

import argparse
import logging
import os
import re
import socket
import subprocess
import sys

APP_BIN = os.getenv("CHATGPT_BIN", "/usr/lib/chatgpt/ChatGPT")
DEFAULT_PROXY = os.getenv("CHATGPT_PROXY", "http://127.0.0.1:7890")
DEFAULT_LAUNCH_LOG = os.path.expanduser("~/.cache/chatgpt-launch.log")

IGNORE_HOSTS = (
    "['localhost','127.0.0.1','192.168.0.0/16','10.0.0.0/8',"
    "'172.16.0.0/12','::1','*.local','.local']"
)

logger = logging.getLogger("ChatGPTFix")


def setup_logger(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logger


def ensure_dbus_env():
    """桌面环境启动时通常已有 DBUS 地址；终端内运行时尽量补全。"""
    if os.getenv("DBUS_SESSION_BUS_ADDRESS"):
        return True
    bus_path = "/run/user/%d/bus" % os.getuid()
    if os.path.exists(bus_path):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + bus_path
        return True
    logger.warning("未找到用户 D-Bus 会话，无法修改 GNOME 系统代理")
    return False


def gsettings(*args, check=True):
    cmd = ["gsettings", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            "执行失败: %s\n%s" % (" ".join(cmd), result.stderr.strip())
        )
    return result


def parse_proxy(proxy_url):
    """解析 http://host:port，返回 (host, port)。"""
    raw = proxy_url.strip()
    raw = re.sub(r"^[a-zA-Z0-9]+://", "", raw)
    raw = raw.rstrip("/")
    if "@" in raw:
        raw = raw.rsplit("@", 1)[1]
    host, _, port = raw.partition(":")
    if not host or not port.isdigit():
        raise ValueError("无法解析代理地址: %s" % proxy_url)
    return host, int(port)


def proxy_reachable(host, port, timeout=3.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def apply_proxy(proxy_url):
    if not ensure_dbus_env():
        return None
    host, port = parse_proxy(proxy_url)
    if not proxy_reachable(host, port):
        logger.warning("代理 %s:%d 当前不可达，仍写入系统代理配置", host, port)

    gsettings("set", "org.gnome.system.proxy", "mode", "manual")
    for scheme in ("http", "https"):
        gsettings("set", "org.gnome.system.proxy.%s" % scheme, "host", host)
        gsettings("set", "org.gnome.system.proxy.%s" % scheme, "port", str(port))
    gsettings("set", "org.gnome.system.proxy", "ignore-hosts", IGNORE_HOSTS)

    logger.info("已将 GNOME 系统代理设置为 %s:%d", host, port)
    return host, port


def config_dir():
    base = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "Codex")


def _lock_pid_alive(lock_path):
    """Chromium 的 SingletonLock 是指向 hostname-pid 的符号链接。"""
    if not os.path.islink(lock_path):
        return None
    target = os.readlink(lock_path)
    match = re.search(r"-(\d+)$", target)
    if not match:
        return None
    pid = int(match.group(1))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def clean_stale_singleton():
    cfg = config_dir()
    removed = []
    lock_path = os.path.join(cfg, "SingletonLock")
    if os.path.islink(lock_path) or os.path.exists(lock_path):
        alive = _lock_pid_alive(lock_path)
        if alive is False:
            os.unlink(lock_path)
            removed.append(lock_path)
    socket_path = os.path.join(cfg, "SingletonSocket")
    if os.path.islink(socket_path):
        target = os.readlink(socket_path)
        if not os.path.exists(target):
            os.unlink(socket_path)
            removed.append(socket_path)
    cookie_path = os.path.join(cfg, "SingletonCookie")
    if os.path.islink(cookie_path) and not os.path.exists(
        os.path.join(cfg, "SingletonSocket")
    ):
        os.unlink(cookie_path)
        removed.append(cookie_path)

    if removed:
        logger.info("已清理残留单实例锁: %s", ", ".join(removed))
    else:
        logger.info("单实例锁状态正常，无需清理")
    return removed


def app_running():
    try:
        out = subprocess.run(
            ["pgrep", "-x", "ChatGPT"], capture_output=True, text=True
        )
        return out.returncode == 0 and bool(out.stdout.strip())
    except FileNotFoundError:
        return False


def launch(app_args, proxy_url, log_path):
    if not os.path.isfile(APP_BIN):
        raise FileNotFoundError("未找到 ChatGPT 可执行文件: %s" % APP_BIN)

    clean_stale_singleton()
    apply_proxy(proxy_url)

    if app_running():
        logger.info("检测到 ChatGPT 已在运行，将交由现有实例处理参数")
    else:
        logger.info("正在启动 ChatGPT...")

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "wb") as log_file:
        subprocess.Popen(
            [APP_BIN, *app_args],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    logger.info("启动请求已提交，运行日志: %s", log_path)


def do_check(proxy_url):
    host, port = parse_proxy(proxy_url)
    ok = True
    print("== ChatGPT 启动环境检查 ==")
    print("应用路径     :", APP_BIN, "(存在)" if os.path.isfile(APP_BIN) else "(缺失)")
    print("应用进程     :", "运行中" if app_running() else "未运行")

    if ensure_dbus_env():
        mode = gsettings("get", "org.gnome.system.proxy", "mode", check=False)
        h = gsettings("get", "org.gnome.system.proxy.http", "host", check=False)
        p = gsettings("get", "org.gnome.system.proxy.http", "port", check=False)
        mode = mode.stdout.strip() if mode.returncode == 0 else "unknown"
        h = h.stdout.strip() if h.returncode == 0 else "unknown"
        p = p.stdout.strip() if p.returncode == 0 else "unknown"
        print("GNOME 代理模式:", mode)
        print("GNOME HTTP 代理:", "%s:%s" % (h, p))
        if mode != "'manual'":
            ok = False
    else:
        ok = False

    print(
        "代理连通性   :",
        "可达" if proxy_reachable(**{"host": host, "port": port}) else "不可达",
    )

    cfg = config_dir()
    lock_path = os.path.join(cfg, "SingletonLock")
    if os.path.islink(lock_path):
        alive = _lock_pid_alive(lock_path)
        print(
            "单实例锁     :",
            "指向存活进程" if alive else "残留（需清理）",
            "->",
            os.readlink(lock_path),
        )
        if alive is False:
            ok = False
    else:
        print("单实例锁     : 不存在")

    print("结论         :", "正常" if ok else "存在待修复项，请执行 apply")
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(
        description="修复 ChatGPT/Codex 桌面应用的白屏、卡死与启动失败问题"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="apply",
        choices=["apply", "check", "launch"],
        help="apply=修复代理与残留锁；check=检查状态；launch=修复后启动应用",
    )
    parser.add_argument(
        "--proxy",
        default=DEFAULT_PROXY,
        help="本地 HTTP 代理地址，默认 %s" % DEFAULT_PROXY,
    )
    parser.add_argument(
        "--launch-log",
        default=DEFAULT_LAUNCH_LOG,
        help="launch 模式下应用标准输出日志路径，默认 %s" % DEFAULT_LAUNCH_LOG,
    )
    parser.add_argument(
        "--debug", action="store_true", help="输出调试日志"
    )
    parser.add_argument(
        "app_args", nargs=argparse.REMAINDER, help="透传给应用的参数（通常为 URL）"
    )
    args = parser.parse_args()

    setup_logger(logging.DEBUG if args.debug else logging.INFO)

    try:
        if args.mode == "check":
            return do_check(args.proxy)
        if args.mode == "launch":
            launch(args.app_args, args.proxy, args.launch_log)
            return 0
        clean_stale_singleton()
        apply_proxy(args.proxy)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("修复失败: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
