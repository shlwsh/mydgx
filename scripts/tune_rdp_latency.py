#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给工作站 xrdp 打开 TCP_NODELAY，并关掉远程 GNOME 动画。

同一房间里鼠标发滞，多半是 Nagle 组包和合成器动画，不是跨网段。
本脚本只改这两项，然后重启 xrdp 监听进程（已有会话会断开，重连即可）。

用法:
    python scripts/tune_rdp_latency.py
    python scripts/tune_rdp_latency.py --node node0
"""

import argparse
import os
import sys

import paramiko

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from env_helper import init_env, setup_logger  # noqa: E402

logger = setup_logger("RDP-Latency")

REMOTE = r"""
set -u
INI=/etc/xrdp/xrdp.ini
ts=$(date +%Y%m%d%H%M%S)
cp -a "$INI" "$INI.bak.latency.$ts"
python3 - "$INI" << 'PY'
import sys
path = sys.argv[1]
with open(path, encoding="utf-8", errors="replace") as fh:
    text = fh.read()
lines = text.splitlines()
wanted = {"tcp_nodelay": "true", "tcp_keepalive": "true"}
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0].strip()
    if key in wanted:
        out.append(f"{key}={wanted[key]}")
        seen.add(key)
    else:
        out.append(line)
if len(seen) < len(wanted):
    inserted = []
    placed = False
    for line in out:
        inserted.append(line)
        if not placed and line.strip() == "[Globals]":
            for key, val in wanted.items():
                if key not in seen:
                    inserted.append(f"{key}={val}")
            placed = True
    out = inserted
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("xrdp.ini " + " ".join(f"{k}={v}" for k, v in wanted.items()))
PY
systemctl restart xrdp
sleep 1
state=$(systemctl is-active xrdp)
echo "xrdp=${state}"
test "${state}" = "active"
getent passwd | awk -F: '$3>=1000 && $3<65000 {print $1":"$3}' | while IFS=: read -r user uid; do
    bus="/run/user/${uid}/bus"
    if [ -S "$bus" ]; then
        sudo -u "$user" DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}" \
            gsettings set org.gnome.desktop.interface enable-animations false \
            && echo "animations_off ${user}" || echo "animations_fail ${user}"
    fi
done
grep -E '^(tcp_nodelay|tcp_keepalive)=' "$INI" || true
"""


def targets(selected: str) -> list:
    """组装可登录的节点。口令或密钥缺失的节点直接跳过。"""
    init_env()
    rows = [
        (
            "node0",
            os.getenv("NODE0_MGMT_IP", "172.19.50.70"),
            os.getenv("SSH_NODE0_USER", "winbot"),
            os.getenv("SSH_NODE0_PASS", ""),
            "",
        ),
        (
            "node1",
            os.getenv("NODE1_MGMT_IP", "172.19.49.159"),
            os.getenv("SSH_OPS_USER", "dgxdeploy"),
            os.getenv("SSH_OPS_PASS", ""),
            os.getenv("SSH_OPS_KEY_PATH", ""),
        ),
        (
            "node123",
            os.getenv("NODE123_MGMT_IP", "172.19.51.123"),
            os.getenv("SSH_NODE123_USER", "bot"),
            os.getenv("SSH_NODE123_PASS", ""),
            "",
        ),
    ]
    if selected != "all":
        rows = [row for row in rows if row[0] == selected]
    return rows


def run_node(name: str, host: str, user: str, password: str, key_path: str) -> bool:
    """连上节点并执行调优。sudo 优先免密，否则用该节点自己的 SSH 口令。"""
    if not password and not (key_path and os.path.isfile(key_path)):
        logger.warning("%s 没有可用的 SSH 口令或密钥，跳过", name)
        return True

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host,
            username=user,
            password=password or None,
            key_filename=key_path if key_path and os.path.isfile(key_path) else None,
            timeout=12,
            banner_timeout=12,
            auth_timeout=12,
            look_for_keys=False,
            allow_agent=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("%s SSH 失败: %s", name, exc)
        return False

    probe_in, probe_out, _probe_err = client.exec_command("sudo -n true && echo NOPASS || echo NEEDPASS", timeout=15)
    probe_in.close()
    need_pass = "NEEDPASS" in probe_out.read().decode("utf-8", "replace")
    if need_pass and not password:
        logger.error("%s 需要 sudo 口令，但环境变量里没有", name)
        client.close()
        return False
    if need_pass:
        stdin, stdout, stderr = client.exec_command("sudo -S -p '' bash -s", timeout=40)
        stdin.write(password + "\n" + REMOTE)
    else:
        stdin, stdout, stderr = client.exec_command("sudo -n bash -s", timeout=40)
        stdin.write(REMOTE)
    stdin.channel.shutdown_write()

    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
    client.close()
    if out:
        logger.info("%s\n%s", name, out)
    if code != 0:
        logger.error("%s 调优失败 exit=%s %s", name, code, err[-500:])
        return False
    if err:
        logger.info("%s stderr: %s", name, err[-400:])
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="降低三台工作站远程桌面的操作延迟")
    parser.add_argument("--node", choices=["all", "node0", "node1", "node123"], default="all")
    args = parser.parse_args()

    ok = True
    for name, host, user, password, key_path in targets(args.node):
        logger.info("开始调优 %s (%s)", name, host)
        ok = run_node(name, host, user, password, key_path) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
