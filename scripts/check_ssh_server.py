#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查本机 OpenSSH Server 的服务、监听、防火墙、认证配置与端口连通性。

适用平台：Windows 10/11（本机 DESKTOP-MLQ4P1S）。
脚本只读取配置与状态，不修改系统；不会输出私钥或口令内容。

示例：
    python scripts/check_ssh_server.py
    python scripts/check_ssh_server.py --host 172.19.50.63 --host 100.120.170.99
    python scripts/check_ssh_server.py --port 22 --timeout 5 --verbose
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
LOGGER = logging.getLogger("check_ssh_server")

PROGRAM_DATA = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
SSH_DIR = PROGRAM_DATA / "ssh"
SSHD_CONFIG = SSH_DIR / "sshd_config"
ADMIN_KEYS = SSH_DIR / "administrators_authorized_keys"
SSHD_EXE = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "OpenSSH" / "sshd.exe"

# 判定为“本机接入地址”时排除的网段：198.18.0.0/15 为 CuteCloud 代理虚拟网段
EXCLUDED_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)


class CheckResult:
    """单条检查结果。"""

    def __init__(self, name: str, ok: bool, detail: str, critical: bool = True) -> None:
        self.name = name
        self.ok = ok
        self.detail = detail
        self.critical = critical


def run_powershell(script: str, timeout: int = 30) -> str:
    """执行 PowerShell 片段并返回标准输出（UTF-8）。"""
    command = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + script,
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip() or "PowerShell 执行失败")
    return completed.stdout.strip()


def ps_json(script: str, timeout: int = 30) -> Any:
    """执行 PowerShell 并把结果解析为 JSON 对象。"""
    output = run_powershell(script + " | ConvertTo-Json -Compress -Depth 6", timeout=timeout)
    if not output:
        return None
    return json.loads(output)


def as_list(value: Any) -> list[Any]:
    """把 PowerShell 单个对象或数组统一为列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def check_platform() -> CheckResult:
    if os.name != "nt":
        return CheckResult("运行平台", False, "本脚本仅支持 Windows", critical=True)
    version = platform.version()
    return CheckResult(
        "运行平台",
        True,
        f"{platform.node()} / Windows {version} / {platform.machine()}",
    )


def check_service() -> CheckResult:
    try:
        data = ps_json(
            "Get-Service -Name sshd -ErrorAction Stop | "
            "Select-Object @{n='Name';e={$_.Name}},"
            "@{n='Status';e={[string]$_.Status}},"
            "@{n='StartType';e={[string]$_.StartType}}"
        )
    except Exception as exc:  # noqa: BLE001 - 需要把底层错误转成检查项
        return CheckResult("sshd 服务", False, f"查询失败：{exc}")

    if not data:
        return CheckResult("sshd 服务", False, "未安装或未注册 sshd 服务")

    status = str(data.get("Status", "Unknown"))
    start_type = str(data.get("StartType", "Unknown"))
    ok = status == "Running"
    detail = f"状态={status}，启动类型={start_type}"
    if status == "Running" and start_type != "Automatic":
        detail += "（建议设为 Automatic，避免重启后失联）"
    return CheckResult("sshd 服务", ok, detail)


def check_listener(port: int) -> CheckResult:
    try:
        data = ps_json(
            f"Get-NetTCPConnection -State Listen -LocalPort {port} "
            "-ErrorAction SilentlyContinue | "
            "Select-Object LocalAddress,LocalPort,OwningProcess"
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult("端口监听", False, f"查询失败：{exc}")

    listeners = as_list(data)
    if not listeners:
        return CheckResult("端口监听", False, f"未发现 TCP/{port} 监听")

    addresses = sorted({str(item.get("LocalAddress")) for item in listeners})
    pids = sorted({str(item.get("OwningProcess")) for item in listeners})
    return CheckResult(
        "端口监听",
        True,
        f"TCP/{port} 正在监听：{', '.join(addresses)}（PID {', '.join(pids)}）",
    )


def check_firewall(port: int) -> CheckResult:
    try:
        data = ps_json(
            "Get-NetFirewallRule -ErrorAction SilentlyContinue | "
            "Where-Object { $_.DisplayName -like '*SSH*' } | "
            "ForEach-Object { $pf = $_ | Get-NetFirewallPortFilter; "
            "[pscustomobject]@{"
            "Name=$_.DisplayName;"
            "Enabled=[string]$_.Enabled;"
            "Direction=[string]$_.Direction;"
            "Action=[string]$_.Action;"
            "Profile=[string]$_.Profile;"
            "Protocol=[string]$pf.Protocol;"
            "LocalPort=[string]$pf.LocalPort"
            "} }"
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult("防火墙规则", False, f"查询失败：{exc}")

    rules = as_list(data)
    effective = [
        rule
        for rule in rules
        if str(rule.get("Enabled")).lower() == "true"
        and str(rule.get("Direction")).lower() == "inbound"
        and str(rule.get("Action")).lower() == "allow"
        and str(rule.get("LocalPort")) in {str(port), "Any"}
    ]
    if not effective:
        return CheckResult(
            "防火墙规则",
            False,
            f"未发现启用的入站放行规则（TCP/{port}）",
        )

    summary = "; ".join(
        f"{rule.get('Name')}(Profile={rule.get('Profile')},Port={rule.get('LocalPort')})"
        for rule in effective
    )
    return CheckResult("防火墙规则", True, f"已放行入站：{summary}")


def check_sshd_config() -> tuple[CheckResult, dict[str, str]]:
    if not SSHD_CONFIG.exists():
        return CheckResult("sshd 配置", False, f"配置文件不存在：{SSHD_CONFIG}"), {}
    if not SSHD_EXE.exists():
        return CheckResult("sshd 配置", False, f"未找到 sshd.exe：{SSHD_EXE}"), {}

    try:
        completed = subprocess.run(
            [str(SSHD_EXE), "-T"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult("sshd 配置", False, f"读取生效配置失败：{exc}"), {}

    if completed.returncode != 0:
        return CheckResult(
            "sshd 配置",
            False,
            f"配置校验失败：{(completed.stderr or completed.stdout).strip()}",
        ), {}

    effective: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            effective[parts[0].lower()] = parts[1].strip()

    watched = ("port", "listenaddress", "passwordauthentication", "pubkeyauthentication")
    detail = "，".join(f"{key}={effective.get(key, '未设置')}" for key in watched)
    return CheckResult("sshd 配置", True, detail), effective


def check_sftp_subsystem(effective: dict[str, str]) -> CheckResult:
    value = effective.get("subsystem", "").strip()
    if not value:
        return CheckResult("SFTP 子系统", False, "未配置 sftp 子系统", critical=False)

    parts = value.split(None, 1)
    if len(parts) < 2:
        return CheckResult("SFTP 子系统", False, f"配置不完整：{value}", critical=False)

    name, command = parts
    executable = command.strip().strip('"')
    path = Path(executable)
    if not path.is_absolute():
        path = SSHD_EXE.parent / path

    if path.exists():
        return CheckResult("SFTP 子系统", True, f"{name} -> {executable}")
    return CheckResult(
        "SFTP 子系统",
        False,
        f"{name} -> {executable}（文件不存在；相对路径可能无法被服务解析，建议改为绝对路径）",
        critical=False,
    )


def check_host_keys() -> CheckResult:
    keys = sorted(SSH_DIR.glob("ssh_host_*_key.pub"))
    if not keys:
        return CheckResult("主机密钥", False, f"未找到主机公钥：{SSH_DIR}\\ssh_host_*_key.pub")
    return CheckResult("主机密钥", True, "已存在：" + ", ".join(key.name for key in keys))


def is_admin_user() -> bool:
    """判断当前进程是否以管理员权限运行。"""
    try:
        output = run_powershell(
            "([Security.Principal.WindowsPrincipal]"
            "[Security.Principal.WindowsIdentity]::GetCurrent())"
            ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"
        )
        return output.strip().lower() == "true"
    except Exception:  # noqa: BLE001
        return False


def count_authorized_keys(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            count += 1
    return count


def check_authorized_keys(is_admin: bool) -> list[CheckResult]:
    results: list[CheckResult] = []

    admin_count = count_authorized_keys(ADMIN_KEYS)
    if ADMIN_KEYS.exists():
        detail = f"{ADMIN_KEYS}（{admin_count} 个公钥）"
        try:
            acl = run_powershell(
                f"(Get-Acl -LiteralPath '{ADMIN_KEYS}').Access | "
                "ForEach-Object { $_.IdentityReference.Value + ':' + $_.FileSystemRights }"
            )
            if acl:
                detail += "；ACL=" + ", ".join(acl.splitlines())
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("读取 administrators_authorized_keys ACL 失败：%s", exc)
        results.append(CheckResult("管理员公钥", True, detail))
    else:
        results.append(
            CheckResult(
                "管理员公钥",
                False,
                f"不存在：{ADMIN_KEYS}（管理员账号暂时只能使用密码登录）",
                critical=False,
            )
        )

    user_keys = Path.home() / ".ssh" / "authorized_keys"
    if user_keys.exists():
        results.append(
            CheckResult(
                "当前用户公钥",
                True,
                f"{user_keys}（{count_authorized_keys(user_keys)} 个公钥）",
            )
        )
    elif is_admin and admin_count > 0:
        results.append(
            CheckResult(
                "当前用户公钥",
                True,
                f"未配置：{user_keys}；管理员账号实际使用 administrators_authorized_keys，属正常",
            )
        )
    else:
        results.append(
            CheckResult(
                "当前用户公钥",
                False,
                f"不存在：{user_keys}（当前用户未配置公钥登录）",
                critical=False,
            )
        )
    return results


def local_ipv4_addresses() -> Iterable[str]:
    """返回本机可用于对外访问的 IPv4 地址（排除回环、链路本地与代理网段）。"""
    try:
        data = ps_json(
            "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
            "Select-Object InterfaceAlias,IPAddress"
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("自动获取本机地址失败：%s", exc)
        return []

    addresses: list[str] = []
    for item in as_list(data):
        raw = str(item.get("IPAddress", "")).strip()
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if address.is_loopback or address.is_link_local:
            continue
        if any(address in network for network in EXCLUDED_NETWORKS):
            continue
        addresses.append(raw)
    return sorted(set(addresses))


def probe_banner(host: str, port: int, timeout: float) -> str:
    """连接 SSH 端口并读取协议横幅。"""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        data = sock.recv(255)
    return data.decode("ascii", errors="replace").strip()


def check_reachability(host: str, port: int, timeout: float, critical: bool) -> CheckResult:
    try:
        banner = probe_banner(host, port, timeout)
    except OSError as exc:
        return CheckResult(f"连通性 {host}:{port}", False, f"连接失败：{exc}", critical=critical)
    ok = banner.startswith("SSH-2.0-")
    detail = f"横幅：{banner}" if banner else "未收到 SSH 协议横幅"
    return CheckResult(f"连通性 {host}:{port}", ok, detail, critical=critical)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查本机 OpenSSH Server 的服务状态、监听端口、防火墙与认证配置。",
    )
    parser.add_argument("--port", type=int, default=22, help="SSH 端口，默认 22")
    parser.add_argument(
        "--host",
        action="append",
        default=[],
        metavar="地址",
        help="额外测试连通性的目标地址，可重复指定",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="单次连接超时秒数，默认 5")
    parser.add_argument(
        "--no-auto-host",
        action="store_true",
        help="不自动探测本机网卡地址，仅测试 127.0.0.1 与 --host 指定的地址",
    )
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.port < 1 or args.port > 65535:
        LOGGER.error("端口不合法：%d", args.port)
        return 2

    results: list[CheckResult] = [check_platform()]
    if not results[0].ok:
        LOGGER.error(results[0].detail)
        return 2

    results.append(check_service())
    results.append(check_listener(args.port))
    results.append(check_firewall(args.port))

    config_result, effective = check_sshd_config()
    results.append(config_result)
    results.append(check_sftp_subsystem(effective))
    results.append(check_host_keys())
    results.extend(check_authorized_keys(is_admin_user()))

    if effective.get("passwordauthentication", "").lower() == "yes":
        LOGGER.warning("当前启用密码认证；完成公钥部署后建议关闭 PasswordAuthentication")
    if effective.get("permitrootlogin", "").lower() not in {"", "no", "prohibit-password", "without-password"}:
        LOGGER.warning("PermitRootLogin 配置较宽松：%s", effective.get("permitrootlogin"))

    targets: list[str] = ["127.0.0.1"]
    if not args.no_auto_host:
        targets.extend(local_ipv4_addresses())
    targets.extend(args.host)

    seen: set[str] = set()
    for host in targets:
        if host in seen:
            continue
        seen.add(host)
        critical = host == "127.0.0.1" or host in args.host
        results.append(check_reachability(host, args.port, args.timeout, critical))

    failures = [item for item in results if not item.ok and item.critical]
    warnings = [item for item in results if not item.ok and not item.critical]

    LOGGER.info("=" * 62)
    for item in results:
        if item.ok:
            level = logging.INFO
            tag = "通过"
        elif item.critical:
            level = logging.ERROR
            tag = "失败"
        else:
            level = logging.WARNING
            tag = "警告"
        LOGGER.log(level, "[%s] %s：%s", tag, item.name, item.detail)
    LOGGER.info("=" * 62)
    LOGGER.info(
        "检查完成：%d 项通过，%d 项失败，%d 项警告",
        sum(1 for item in results if item.ok),
        len(failures),
        len(warnings),
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
