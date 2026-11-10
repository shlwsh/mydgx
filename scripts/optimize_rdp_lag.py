#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/optimize_rdp_lag.py - 远程桌面（xrdp over Tailscale）卡顿诊断与调优工具

背景：
    跨运营商链路存在 10%～20% 的固定丢包（时延平稳、无抖动），Linux 内核默认的
    CUBIC 拥塞控制在丢包环境下会把拥塞窗口压到 2 个 MSS，RDP 下行吞吐坍塌到
    1Mbps 以下，表现为画面卡顿、输入延迟数秒。

功能：
    1. --check     全链路诊断：丢包/抖动、单流与多流 TCP 吞吐、拥塞控制算法、
                   xrdp 关键参数、会话分辨率/色深、GNOME 动画与重负载进程；
    2. --apply     应用优化：启用 BBR + fq（持久化）、关闭 GNOME 动画；
                   可选 --bpp 24 降低 xrdp 色深以进一步节省带宽；
    3. --rollback  回滚到调优前状态（依据 /var/lib/rdp-lag-opt/state.json）。

用法示例：
    .venv/bin/python scripts/optimize_rdp_lag.py --node node123 --check
    .venv/bin/python scripts/optimize_rdp_lag.py --node node123 --check --throughput
    .venv/bin/python scripts/optimize_rdp_lag.py --node node123 --apply
    .venv/bin/python scripts/optimize_rdp_lag.py --node all --apply
    .venv/bin/python scripts/optimize_rdp_lag.py --node node123 --rollback
"""

import argparse
import json
import os
import random
import re
import socket
import subprocess
import sys
import time

import paramiko

# 引入项目通用环境引导模块（自动切换到 .venv 并加载 .env）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import setup_logger, init_env

logger = setup_logger("RdpLagOptimizer")

# 远端调优状态文件（记录调优前的原始值，供 --rollback 使用）
STATE_DIR = "/var/lib/rdp-lag-opt"
STATE_FILE = STATE_DIR + "/state.json"

# 远端吞吐测试服务监听端口（临时，测试完成后立即回收）
# 吞吐测试端口在每次运行时随机选取（5400-5599），避免与残留服务冲突

SYSCTL_FILE = "/etc/sysctl.d/99-rdp-bbr.conf"
MODULES_FILE = "/etc/modules-load.d/rdp-bbr.conf"

SYSCTL_CONTENT = """# 由 scripts/optimize_rdp_lag.py 生成：远程桌面高丢包链路 TCP 调优
# 跨运营商链路存在 10%~20% 固定丢包，CUBIC 会崩到 ~1Mbps，BBR 可提升至 ~8Mbps
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
# 空闲后不重置拥塞窗口，避免交互式会话每次停顿后重新慢启动
net.ipv4.tcp_slow_start_after_idle = 0
# 保持适度发送缓冲上限，避免大缓冲造成交互延迟膨胀
net.ipv4.tcp_wmem = 4096 16384 16777216
net.ipv4.tcp_rmem = 4096 131072 33554432
"""

NODE_SPECS = {
    "node0": {
        "name": "Node0 (cube-f22b)",
        "ts_ip_env": "NODE0_TAILSCALE_IP",
        "user_env": "SSH_NODE0_USER",
        "pass_env": "SSH_NODE0_PASS",
        "rdp_user_env": "NODE0_RDP_USER",
    },
    "node1": {
        "name": "Node1 (cube-0137)",
        "ts_ip_env": "NODE1_TAILSCALE_IP",
        "user_env": "SSH_OPS_USER",
        "pass_env": "SSH_OPS_PASS",
        "rdp_user_env": "NODE1_RDP_USER",
    },
    "node123": {
        "name": "Node123 (cube-fe5e)",
        "ts_ip_env": "NODE123_TAILSCALE_IP",
        "user_env": "SSH_NODE123_USER",
        "pass_env": "SSH_NODE123_PASS",
        "rdp_user_env": "NODE123_RDP_USER",
    },
}


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------
def node_conf(key):
    spec = NODE_SPECS[key]
    return {
        "key": key,
        "name": spec["name"],
        "host": os.getenv(spec["ts_ip_env"]),
        "user": os.getenv(spec["user_env"]),
        "pwd": os.getenv(spec["pass_env"]),
        "rdp_user": os.getenv(spec["rdp_user_env"]) or os.getenv(spec["user_env"]),
    }


def ssh_connect(conf):
    """建立 SSH 连接（跳板内网丢包严重，放宽超时并重试）"""
    last = None
    for attempt in range(3):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                conf["host"],
                username=conf["user"],
                password=conf["pwd"],
                timeout=25,
                banner_timeout=45,
                auth_timeout=45,
            )
            return client
        except Exception as exc:  # noqa: BLE001
            last = exc
            logger.warning("SSH 连接 %s 第 %d 次失败: %s", conf["host"], attempt + 1, exc)
            time.sleep(2)
    raise RuntimeError(f"SSH 连接 {conf['host']} 失败: {last}")


def run(client, cmd, timeout=120):
    """执行远端命令并返回合并输出"""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    return out + err


def sudo(client, conf, cmd, timeout=120):
    """以 sudo 执行远端命令（口令走 .env 中的 SSH 口令）"""
    wrapped = f"echo '{conf['pwd']}' | sudo -S -p '' bash -c {json.dumps(cmd)} 2>&1"
    return run(client, wrapped, timeout=timeout)


# --------------------------------------------------------------------------
# 本机 → 节点的链路测量
# --------------------------------------------------------------------------
def ping_stats(host, count=60, interval=0.2):
    """本机 ping 节点，返回丢包率与 RTT 统计"""
    try:
        out = subprocess.run(
            ["ping", "-c", str(count), "-i", str(interval), "-q", host],
            capture_output=True, text=True, timeout=count * interval + 30,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    loss_m = re.search(r"([\d.]+)% packet loss", out)
    rtt_m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
    if not loss_m:
        return {"ok": False, "error": out.strip()[-200:]}
    return {
        "ok": True,
        "loss": float(loss_m.group(1)),
        "min": float(rtt_m.group(1)) if rtt_m else 0.0,
        "avg": float(rtt_m.group(2)) if rtt_m else 0.0,
        "max": float(rtt_m.group(3)) if rtt_m else 0.0,
        "jitter": float(rtt_m.group(4)) if rtt_m else 0.0,
    }


TP_SERVER = r'''
import socket, threading, time
def serve(c):
    c.settimeout(15)
    buf = b"x" * 65536
    tot = 0
    t0 = time.time()
    try:
        while time.time() - t0 < 8:
            c.sendall(buf)
            tot += len(buf)
    except Exception:
        pass
    try:
        c.close()
    except Exception:
        pass
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", %d))
s.listen(8)
s.settimeout(25)
try:
    while True:
        c, _ = s.accept()
        threading.Thread(target=serve, args=(c,), daemon=True).start()
except Exception:
    pass
'''


def measure_throughput(client, host, streams=1, seconds=6):
    """在节点上启动临时 TCP 发送服务，测量节点→本机的下行吞吐（Mbps）"""
    port = random.randint(5400, 5599)
    remote_script = f"/tmp/rdp_lag_tp_{port}.py"
    try:
        sftp = client.open_sftp()
        with sftp.file(remote_script, "w") as handle:
            handle.write(TP_SERVER % port)
        sftp.close()
        run(client, f"nohup python3 {remote_script} >/tmp/rdp_lag_tp_{port}.log 2>&1 & sleep 1")

        socks = [socket.create_connection((host, port), timeout=10) for _ in range(streams)]
        for sock in socks:
            sock.settimeout(6)
        total = 0
        start = time.time()
        while time.time() - start < seconds:
            for sock in socks:
                try:
                    chunk = sock.recv(262144)
                    if chunk:
                        total += len(chunk)
                except socket.timeout:
                    pass
        for sock in socks:
            sock.close()
        elapsed = time.time() - start
        return total * 8 / elapsed / 1e6
    except Exception as exc:  # noqa: BLE001
        logger.warning("吞吐测试失败: %s", exc)
        return None
    finally:
        try:
            run(client, f"rm -f {remote_script}")
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# 远端状态采集
# --------------------------------------------------------------------------
REMOTE_PROBE = r'''
echo "###SYS###"
nproc; uptime | sed 's/^/LOAD:/'
echo "###CC###"
sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null
sysctl -n net.core.default_qdisc 2>/dev/null
sysctl -n net.ipv4.tcp_available_congestion_control 2>/dev/null
echo "###XRDP###"
grep -E "^(max_bpp|use_fastpath|bitmap_compression|bulk_compression|crypt_level|tcp_send_buffer_bytes|tcp_recv_buffer_bytes|tcp_nodelay)" /etc/xrdp/xrdp.ini 2>/dev/null
echo "###SESSION###"
for d in /tmp/.X11-unix/X*; do
  n=$(basename "$d" | sed 's/^X//')
  echo "DISPLAY=$n $(DISPLAY=:$n timeout 3 xdpyinfo 2>/dev/null | awk '/dimensions:/{print $2}')"
done
echo "###ANIM###"
true
echo "###TOP###"
ps -eo pcpu,comm --sort=-pcpu | head -6
echo "###XPROC###"
ps -eo pid,args | grep -E "[X]org :" | head -5
'''

def collect_remote(client, conf):
    """采集远端关键指标"""
    out = run(client, REMOTE_PROBE, timeout=90)
    data = {"raw": out}

    def block(tag):
        match = re.search(r"###%s###(.*?)(?=###[A-Z]+###|\Z)" % tag, out, re.S)
        return match.group(1).strip() if match else ""

    data["load"] = [ln for ln in block("SYS").splitlines() if ln.startswith("LOAD:")]
    data["load"] = data["load"][0][5:] if data["load"] else "?"
    cc_lines = block("CC").splitlines()
    data["cc"] = cc_lines[0].strip() if cc_lines else "?"
    data["qdisc"] = cc_lines[1].strip() if len(cc_lines) > 1 else "?"
    data["cc_avail"] = cc_lines[2].strip() if len(cc_lines) > 2 else ""
    data["xrdp"] = dict(
        line.split("=", 1) for line in block("XRDP").splitlines() if "=" in line
    )
    data["displays"] = [ln for ln in block("SESSION").splitlines() if ln.startswith("DISPLAY=")]
    data["top"] = block("TOP").splitlines()
    data["xprocs"] = [ln for ln in block("XPROC").splitlines() if ln.strip()]

    # 会话内 GNOME 动画状态（读取 dconf 无需会话总线）
    data["anim"] = sudo(
        client, conf,
        f"runuser -u {conf['rdp_user']} -- dbus-run-session -- "
        "gsettings get org.gnome.desktop.interface enable-animations",
        timeout=30,
    ).strip()
    return data


def print_report(conf, ping, data, tp1, tp4):
    """输出诊断结论"""
    logger.info("=" * 66)
    logger.info("节点 %s (%s) 诊断结果", conf["name"], conf["host"])
    logger.info("=" * 66)

    if ping.get("ok"):
        level = "ERROR" if ping["loss"] >= 5 else ("WARN" if ping["loss"] >= 1 else "INFO")
        getattr(logger, level.lower())(
            "链路质量：丢包 %.1f%% | RTT 最小/平均/最大 = %.1f/%.1f/%.1f ms | 抖动 %.1f ms",
            ping["loss"], ping["min"], ping["avg"], ping["max"], ping["jitter"],
        )
    else:
        logger.error("链路质量：探测失败 (%s)", ping.get("error"))

    logger.info("拥塞控制：%s | 队列规则：%s | 可用算法：%s",
                data["cc"], data["qdisc"], data["cc_avail"] or "?")
    logger.info("系统负载：%s", data["load"])

    xrdp = data["xrdp"]
    logger.info("xrdp 参数：max_bpp=%s use_fastpath=%s bitmap_compression=%s bulk_compression=%s",
                xrdp.get("max_bpp", "?"), xrdp.get("use_fastpath", "?"),
                xrdp.get("bitmap_compression", "?"), xrdp.get("bulk_compression", "?"))

    if data["displays"]:
        for line in data["displays"]:
            logger.info("会话 %s", line)
    else:
        logger.info("会话：当前无活动 X11 会话")

    logger.info("GNOME 动画：%s", data["anim"] or "未知")

    if tp1 is not None and tp4 is not None:
        logger.info("下行吞吐：单流 %.1f Mbps | 4 并发 %.1f Mbps", tp1, tp4)
        if tp1 < 3:
            logger.error("单流吞吐严重不足（<3Mbps），远程桌面必然卡顿")
        elif tp1 < 8:
            logger.warning("单流吞吐偏低（<8Mbps），画面仍会明显掉帧")

    logger.info("-" * 66)
    logger.info("高 CPU 进程：")
    for line in data["top"]:
        logger.info("    %s", line)

    logger.info("-" * 66)
    logger.info("优化建议：")
    if data["cc"] != "bbr":
        logger.info("    [ ] 执行 --apply 启用 BBR+fq（丢包链路吞吐可提升 5~10 倍）")
    if xrdp.get("max_bpp") not in ("24", None):
        logger.info("    [ ] 可选：--bpp 24 降低色深省带宽（会失去 RemoteFX 32bpp 能力，需权衡）")
    if data["anim"] != "false":
        logger.info("    [ ] 关闭 GNOME 动画，减少无谓的画面刷新")
    logger.info("    [ ] 客户端关闭 Retina/高分屏模式，分辨率降到 1920x1080 或以下")
    logger.info("    [ ] 客户端优先使用支持 RemoteFX 的 FreeRDP（/gfx:rfx /bpp:24）")


# --------------------------------------------------------------------------
# 应用优化 / 回滚
# --------------------------------------------------------------------------
def detect_session_env(client, rdp_user):
    """从 gnome-shell 进程读取会话环境，便于向运行中的会话下发 dconf 设置"""
    out = run(client, "pgrep -u %s -x gnome-shell | head -1" % rdp_user, timeout=20).strip()
    if not out.isdigit():
        return None
    pid = out.splitlines()[0]
    env_raw = run(client, f"tr '\\0' '\\n' < /proc/{pid}/environ 2>/dev/null", timeout=20)
    env = {}
    for line in env_raw.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def set_animations(client, conf, enabled):
    """在会话用户上下文中持久化 GNOME 动画开关"""
    rdp_user = conf["rdp_user"]
    value = "true" if enabled else "false"
    env = detect_session_env(client, rdp_user)
    if env and env.get("DBUS_SESSION_BUS_ADDRESS"):
        prefix = " ".join(
            f"{k}={json.dumps(env[k])}"
            for k in ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "XAUTHORITY")
            if env.get(k)
        )
        sudo(client, conf,
             f"runuser -u {rdp_user} -- env {prefix} "
             f"gsettings set org.gnome.desktop.interface enable-animations {value}",
             timeout=30)
    # 兜底：写入该用户的 dconf 数据库（下次登录生效）
    sudo(client, conf,
         f"runuser -u {rdp_user} -- dbus-run-session -- "
         f"gsettings set org.gnome.desktop.interface enable-animations {value}",
         timeout=30)
    return sudo(client, conf,
                f"runuser -u {rdp_user} -- dbus-run-session -- "
                "gsettings get org.gnome.desktop.interface enable-animations",
                timeout=30).strip()


def apply_node(conf, bpp_target=None):
    """应用优化"""
    logger.info("—— 开始调优 %s (%s) ——", conf["name"], conf["host"])
    client = ssh_connect(conf)
    try:
        state = {
            "cc": run(client, "sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null").strip(),
            "qdisc": run(client, "sysctl -n net.core.default_qdisc 2>/dev/null").strip(),
            "max_bpp": "",
            "anim": "",
        }
        bpp = run(client, "grep -E '^max_bpp' /etc/xrdp/xrdp.ini 2>/dev/null").strip()
        if bpp:
            state["max_bpp"] = bpp.split("=", 1)[1]
        state["anim"] = sudo(
            client, conf,
            f"runuser -u {conf['rdp_user']} -- dbus-run-session -- "
            "gsettings get org.gnome.desktop.interface enable-animations",
            timeout=30,
        ).strip().replace("'", "")

        logger.info("调优前状态：CC=%s qdisc=%s max_bpp=%s animations=%s",
                    state["cc"], state["qdisc"], state["max_bpp"] or "?", state["anim"] or "?")

        # 1) 加载 BBR 模块并持久化
        sudo(client, conf, "modprobe tcp_bbr 2>/dev/null || true")
        avail = run(client, "sysctl -n net.ipv4.tcp_available_congestion_control").strip()
        if "bbr" not in avail:
            logger.error("内核不支持 BBR（可用算法：%s），跳过拥塞控制调优", avail)
        else:
            sudo(client, conf, f"mkdir -p /etc/sysctl.d && printf 'tcp_bbr\\n' > {MODULES_FILE}")
            sftp = client.open_sftp()
            with sftp.file("/tmp/99-rdp-bbr.conf", "w") as handle:
                handle.write(SYSCTL_CONTENT)
            sftp.close()
            sudo(client, conf, f"cp /tmp/99-rdp-bbr.conf {SYSCTL_FILE} && chmod 644 {SYSCTL_FILE}")
            sudo(client, conf, "sysctl --system >/dev/null 2>&1")
            now_cc = run(client, "sysctl -n net.ipv4.tcp_congestion_control").strip()
            now_qdisc = run(client, "sysctl -n net.core.default_qdisc").strip()
            logger.info("拥塞控制已切换：%s + %s（已写入 %s）", now_cc, now_qdisc, SYSCTL_FILE)

        # 2) 可选：降低 xrdp 色深（--bpp 24 时生效，会关闭 RemoteFX 能力，需权衡）
        if bpp_target and state["max_bpp"] not in (bpp_target, ""):
            sudo(client, conf,
                 "cp -n /etc/xrdp/xrdp.ini /etc/xrdp/xrdp.ini.bak-rdplag && "
                 f"sed -i 's/^max_bpp=.*/max_bpp={bpp_target}/' /etc/xrdp/xrdp.ini && "
                 "grep -E '^max_bpp' /etc/xrdp/xrdp.ini")
            logger.info("xrdp max_bpp 已调整为 %s（原 %s）", bpp_target, state["max_bpp"])

        # 3) 关闭 GNOME 动画
        anim = set_animations(client, conf, enabled=False)
        logger.info("GNOME 动画状态：%s", anim or "未知")

        # 4) 记录原始状态
        sudo(client, conf, f"mkdir -p {STATE_DIR}")
        sftp = client.open_sftp()
        with sftp.file("/tmp/rdp-lag-state.json", "w") as handle:
            handle.write(json.dumps(state, ensure_ascii=False))
        sftp.close()
        sudo(client, conf, f"cp /tmp/rdp-lag-state.json {STATE_FILE} && chmod 600 {STATE_FILE}")

        logger.info("提示：xrdp 强制重启才会让新色深对新建会话生效；当前会话需重新登录。")
        logger.info("—— %s 调优完成 ——", conf["name"])
    finally:
        client.close()


def rollback_node(conf):
    """回滚优化"""
    logger.info("—— 回滚 %s (%s) ——", conf["name"], conf["host"])
    client = ssh_connect(conf)
    try:
        state_raw = sudo(client, conf, f"cat {STATE_FILE} 2>/dev/null")
        state = {}
        try:
            state = json.loads(state_raw[state_raw.index("{"):state_raw.rindex("}") + 1])
        except Exception:  # noqa: BLE001
            logger.warning("未找到调优前状态文件，使用默认值回滚")

        sudo(client, conf, f"rm -f {SYSCTL_FILE} {MODULES_FILE}")
        cc = state.get("cc") or "cubic"
        qdisc = state.get("qdisc") or "fq_codel"
        sudo(client, conf, f"sysctl -w net.ipv4.tcp_congestion_control={cc} "
                           f"net.core.default_qdisc={qdisc} >/dev/null")

        bpp = state.get("max_bpp")
        if bpp and bpp != "24":
            sudo(client, conf, f"sed -i 's/^max_bpp=.*/max_bpp={bpp}/' /etc/xrdp/xrdp.ini")

        set_animations(client, conf, enabled=True)
        logger.info("已回滚：CC=%s qdisc=%s max_bpp=%s", cc, qdisc, bpp or "保持")
    finally:
        client.close()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="远程桌面卡顿诊断与调优（Tailscale 高丢包链路 + xrdp 参数）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--node", default="node123",
                        choices=["node0", "node1", "node123", "all"],
                        help="目标节点，默认 node123")
    parser.add_argument("--check", action="store_true", help="仅诊断，不做任何变更")
    parser.add_argument("--apply", action="store_true", help="应用优化")
    parser.add_argument("--rollback", action="store_true", help="回滚优化")
    parser.add_argument("--throughput", action="store_true",
                        help="诊断时附带 TCP 吞吐测试（每个节点约 20 秒）")
    parser.add_argument("--bpp", choices=["16", "24", "32"], default=None,
                        help="可选：调整 xrdp max_bpp（24 可省带宽，但会失去 RemoteFX 32bpp 能力）")
    parser.add_argument("--samples", type=int, default=60, help="丢包探测包数量，默认 60")
    args = parser.parse_args()

    init_env()
    if not (args.check or args.apply or args.rollback):
        args.check = True

    keys = list(NODE_SPECS) if args.node == "all" else [args.node]
    confs = [node_conf(key) for key in keys]

    for conf in confs:
        if not conf["host"] or not conf["user"] or not conf["pwd"]:
            logger.error("%s 缺少 .env 配置（%s / %s），跳过",
                         conf["name"], NODE_SPECS[conf["key"]]["ts_ip_env"],
                         NODE_SPECS[conf["key"]]["pass_env"])
            continue

        if args.rollback:
            rollback_node(conf)
            continue
        if args.apply:
            apply_node(conf, bpp_target=args.bpp)
            if not args.check:
                continue

        # 诊断
        ping = ping_stats(conf["host"], count=args.samples)
        try:
            client = ssh_connect(conf)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 无法诊断：%s", conf["name"], exc)
            continue
        try:
            data = collect_remote(client, conf)
            tp1 = tp4 = None
            if args.throughput:
                logger.info("正在进行 TCP 吞吐测试（约 20 秒）...")
                tp1 = measure_throughput(client, conf["host"], streams=1)
                tp4 = measure_throughput(client, conf["host"], streams=4)
        finally:
            client.close()
        print_report(conf, ping, data, tp1, tp4)

    if not (args.check or args.apply or args.rollback):
        parser.print_help()


if __name__ == "__main__":
    main()
