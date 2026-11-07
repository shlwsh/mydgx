#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/deploy_gnome_session.py - 将工作站远程桌面统一到 GNOME Desktop

背景：
    部分工作站（如 Node123 / cube-fe5e）的 xrdp 远程会话被家目录下的可执行
    `~/.xsession` 锁定到 XFCE4（`exec startxfce4`），与 Node0 / Node1 的
    GNOME Desktop 基线不一致。

原理：
    `/etc/X11/Xsession` 的会话选择链路为
        40x11-common_xsessionrc  → source ~/.xsessionrc（仅导出环境变量）
        50x11-common_determine-startup
            ├─ 若 ~/.xsession 存在且可执行 → STARTUP=~/.xsession
            └─ 否则 → x-session-manager → /usr/bin/gnome-session（GNOME）
        55gnome-session_gnomerc    → 由 XDG_CURRENT_DESKTOP 选中 ubuntu.session
        99x11-common_start         → exec $STARTUP

    两个关键坑位（均为实测踩坑）：
    1. **必须显式指定会话**：无参数的 `gnome-session-binary` 不会选中
       `ubuntu.session`，导致 gnome-shell 静默不启动（登录后黑屏）。
       故入口脚本使用 `exec gnome-session --session=ubuntu`。
    2. **必须清除本地会话变量**：本机曾启用 GDM 自动登录，systemd 用户环境
       会向 xrdp 会话注入 `XDG_SESSION_TYPE=tty` / `WAYLAND_DISPLAY`，
       gnome-shell 检测到非 X11 图形会话类型将拒绝启动。
       故入口脚本显式导出 `XDG_SESSION_TYPE=x11` 并 unset Wayland 变量；
       同时关闭 GDM 自动登录，消除与本地图形会话的 D-Bus 总线名争抢。

执行内容：
    1. 备份既有 XFCE 版 `~/.xsession`（保留 `.xsession.xfce.bak.*` 可随时回滚）；
    2. 写入 `~/.xsessionrc`（对齐 Node0 基线，导出 ubuntu:GNOME / X11 后端）；
    3. 安装 GNOME 会话入口 `~/.xsession`（显式 `gnome-session --session=ubuntu`）；
    4. 部署色彩管理 polkit 豁免（`45-allow-colord.pkla`，抑制 colord 授权弹窗）；
    5. 对齐 `/etc/xrdp/sesman.ini` 的 `[Xorg]` 参数（与 Node0 一致）；
    6. 禁用 GDM Wayland 并关闭 GDM 自动登录（对齐双机基线，需重启 gdm 生效）；
    7. 清理残留 XFCE 会话并重启 `xrdp-sesman`，使下次连接重建 GNOME 会话。

用法:
    python scripts/deploy_gnome_session.py --host 172.19.51.123 --user bot --password ***
    python scripts/deploy_gnome_session.py --host 172.19.51.123 --user bot --password *** --apply
    python scripts/deploy_gnome_session.py --host 172.19.51.123 --user bot --password *** --apply --restart-gdm
    python scripts/deploy_gnome_session.py --host 172.19.51.123 --user bot --password *** --rollback
"""

import argparse
import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from env_helper import setup_logger, init_env
except ImportError:
    def setup_logger(name="GNOME-Deploy", level=20):
        import logging

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        return logging.getLogger(name)

    def init_env():
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


logger = setup_logger("GNOME-Deploy")

# ---------------------------------------------------------------- 模板
XSESSIONRC_TEMPLATE = """\
# 由 scripts/deploy_gnome_session.py 生成：对齐 Node0/Node1 的 GNOME Desktop 基线
export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_SESSION_DESKTOP=ubuntu
export XDG_DATA_DIRS=/usr/share/ubuntu:/usr/local/share:/usr/share:/var/lib/snapd/desktop
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg

# 强制 X11 会话类型：Node123 开启了 GDM 自动登录，systemd 用户环境会残留
# XDG_SESSION_TYPE=tty，gnome-shell 检测到非图形会话类型将拒绝启动（表现为黑屏）。
# 同时清除 Wayland 相关变量，确保走 X11 渲染路径。
unset WAYLAND_DISPLAY
unset GNOME_SHELL_SESSION_MODE_BAK
export XDG_SESSION_TYPE=x11
export DESKTOP_SESSION=ubuntu

# 强制 X11 后端，避免 Wayland 渲染路径导致的无响应/黑屏
export GDK_BACKEND=x11
export QT_QPA_PLATFORM=xcb
export LIBGL_ALWAYS_SOFTWARE=1
"""

COLORD_PKLA = """\
[Allow Colord all Users]
Identity=unix-user:*
Action=org.freedesktop.color-manager.create-device;org.freedesktop.color-manager.create-profile;org.freedesktop.color-manager.delete-device;org.freedesktop.color-manager.delete-profile;org.freedesktop.color-manager.modify-device;org.freedesktop.color-manager.modify-profile
ResultAny=no
ResultInactive=no
ResultActive=yes
"""

# ---------------------------------------------------------------- 远端脚本
# 说明：全部通过 base64 传输，避免多层 shell 转义问题。
SCRIPT_CHECK = r"""
echo '=== 当前会话入口 ==='
ls -la "$HOME"/.xsession "$HOME"/.xsessionrc "$HOME"/.gnomerc 2>&1
echo '=== ~/.xsession 内容 ==='
cat "$HOME"/.xsession 2>&1
echo '=== x-session-manager 指向 ==='
readlink -f /etc/alternatives/x-session-manager 2>&1
echo '=== GNOME 组件 ==='
for b in gnome-session gnome-shell gsettings; do
    printf '%s -> ' "$b"; command -v "$b" || echo MISSING
done
dpkg -l gnome-session gnome-shell ubuntu-session 2>/dev/null | awk '/^(ii|un)/{print $1, $2, $3}'
echo '=== ubuntu.session 可用性 ==='
ls /usr/share/gnome-session/sessions/ubuntu.session 2>&1
echo '=== 当前 XFCE 进程 ==='
pgrep -u "$(id -u)" -a -f 'xfce4-session|startxfce4' 2>/dev/null || echo '(无)'
echo '=== 当前 gnome-shell 进程 ==='
pgrep -u "$(id -u)" -a -x gnome-shell 2>/dev/null || echo '(无)'
echo '=== Wayland 开关 ==='
grep -nE '^WaylandEnable' /etc/gdm3/custom.conf 2>/dev/null || echo '(未设置)'
echo '=== colord 豁免 ==='
ls -l /etc/polkit-1/localauthority/50-local.d/ 2>/dev/null | grep -i color || echo '(无)'
"""

SCRIPT_APPLY = r"""
set -u
STAMP="$(date +%Y%m%d%H%M%S)"

echo '### 1) 备份既有 ~/.xsession（若为 XFCE 版本）###'
if [ -f "$HOME/.xsession" ] && [ ! -L "$HOME/.xsession" ] \
   && grep -q 'startxfce4' "$HOME/.xsession" 2>/dev/null; then
    cp "$HOME/.xsession" "$HOME/.xsession.xfce.bak.$STAMP"
    echo "已备份 XFCE 版 ~/.xsession 为 .xsession.xfce.bak.$STAMP"
else
    echo '(无需备份：~/.xsession 不存在或已是 GNOME 版本)'
fi

echo '### 2) 写入 ~/.xsessionrc ###'
cat > "$HOME/.xsessionrc" <<'XSESSIONRC_EOF'
__XSESSIONRC__
XSESSIONRC_EOF
chmod 644 "$HOME/.xsessionrc"
echo "已写入 $HOME/.xsessionrc"

echo '### 2b) 写入 ~/.xsession-gnome 兜底脚本（覆盖 Xsession 环境变量污染）###'
cat > "$HOME/.xsession-gnome" <<'XSESSGNOME_EOF'
#!/bin/sh
# 由 scripts/deploy_gnome_session.py 生成：
# 作为 ~/.xsession 入口，显式固定 GNOME 会话环境变量后直接拉起 ubuntu:GNOME 会话。
# 背景：Node123 启用 GDM 自动登录后，systemd 用户环境会向会话注入
# XDG_SESSION_TYPE=tty / WAYLAND_DISPLAY 等本地会话变量，
# gnome-shell 检测到非 X11 图形会话类型将拒绝启动（远程表现为黑屏）。

# 独立日志：便于排障（Xsession 会把 stdout/stderr 重定向到 ~/.xsession-errors）
GNOME_SESSION_LOG="$HOME/.gnome-session.log"
exec >>"$GNOME_SESSION_LOG" 2>&1
echo "----- 会话启动 $(date '+%F %T') DISPLAY=$DISPLAY -----"

export XDG_SESSION_TYPE=x11
export XDG_SESSION_DESKTOP=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export GNOME_SHELL_SESSION_MODE=ubuntu
export DESKTOP_SESSION=ubuntu
unset WAYLAND_DISPLAY
unset XDG_SESSION_CLASS
export GDK_BACKEND=x11
export QT_QPA_PLATFORM=xcb
export LIBGL_ALWAYS_SOFTWARE=1

if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi
if [ -x /usr/bin/dbus-update-activation-environment ]; then
    dbus-update-activation-environment --verbose --systemd \
        DBUS_SESSION_BUS_ADDRESS DISPLAY XAUTHORITY \
        XDG_CURRENT_DESKTOP XDG_SESSION_TYPE XDG_SESSION_DESKTOP \
        GNOME_SHELL_SESSION_MODE DESKTOP_SESSION GDK_BACKEND || true
fi

echo "环境: XDG_SESSION_TYPE=$XDG_SESSION_TYPE XDG_CURRENT_DESKTOP=$XDG_CURRENT_DESKTOP DISPLAY=$DISPLAY"
echo "启动 gnome-session --session=ubuntu ..."
exec gnome-session --session=ubuntu
XSESSGNOME_EOF
chmod 755 "$HOME/.xsession-gnome"
echo "已写入 $HOME/.xsession-gnome"

echo '### 2c) 安装 ~/.xsession 入口（真实文件，保留 XFCE 备份）###'
# 说明：必须使用真实文件而非符号链接。50x11-common_determine-startup 以 [ -e ] / [ -x ]
# 判定 ~/.xsession 是否可用；历史验证中符号链接版本曾未被执行，导致会话回退失败。
#
# 关键：Xsession 通过 50x11-common_determine-startup 选择会话程序，其优先级为
#   ~/.xsession 存在 → STARTUP=~/.xsession
#   否则          → STARTUP=x-session-manager（→ gnome-session-binary，缺少 --session 参数）
# 而无参数的 gnome-session-binary 不会选中 ubuntu.session，导致 gnome-shell 不启动。
# 因此这里让 ~/.xsession 显式执行 `gnome-session --session=ubuntu`。
if [ -f "$HOME/.xsession" ] && grep -q 'startxfce4' "$HOME/.xsession" 2>/dev/null; then
    cp "$HOME/.xsession" "$HOME/.xsession.xfce.bak.$STAMP"
    echo "已备份 XFCE 版 ~/.xsession 为 .xsession.xfce.bak.$STAMP"
fi
rm -f "$HOME/.xsession"
cp "$HOME/.xsession-gnome" "$HOME/.xsession"
chmod 755 "$HOME/.xsession"
ls -la "$HOME"/.xsession "$HOME"/.xsession-gnome 2>&1
echo '--- 入口脚本首行校验 ---'
head -n 1 "$HOME/.xsession"
echo '--- 入口脚本关键调用校验（应为 gnome-session --session=ubuntu）---'
grep -n 'exec gnome-session' "$HOME/.xsession" 2>&1

echo '### 3) 部署 colord 豁免 ###'
sudo -S mkdir -p /etc/polkit-1/localauthority/50-local.d <<< "$SUDO_PASS" 2>/dev/null
sudo -S tee /etc/polkit-1/localauthority/50-local.d/45-allow-colord.pkla >/dev/null <<< "$SUDO_PASS" <<'PKLA_EOF'
__PKLA__
PKLA_EOF
sudo -S chmod 644 /etc/polkit-1/localauthority/50-local.d/45-allow-colord.pkla <<< "$SUDO_PASS" 2>/dev/null
ls -l /etc/polkit-1/localauthority/50-local.d/45-allow-colord.pkla 2>&1

echo '### 4) 对齐 sesman.ini [Xorg] 参数（与 Node0 基线一致）###'
sudo -S cp /etc/xrdp/sesman.ini /etc/xrdp/sesman.ini.bak.$STAMP <<< "$SUDO_PASS" 2>/dev/null
# 说明：Node0 使用相对路径 param=xrdp/xorg.conf（相对 /usr/lib/xorg/），
#       但该机实际配置文件位于 /etc/X11/xrdp/xorg.conf；
#       Node123 当前已是绝对路径 /etc/X11/xrdp/xorg.conf（更稳妥），故保留不动，
#       仅移除 Node0 不存在的 -keeptty / -novtswitch 参数以对齐行为。
sudo -S sed -i '/^param=-keeptty$/d; /^param=-novtswitch$/d' /etc/xrdp/sesman.ini <<< "$SUDO_PASS" 2>/dev/null
sed -n '/^\[Xorg\]/,/^\[Xvnc\]/p' /etc/xrdp/sesman.ini | grep -E '^param' 2>&1

echo '### 5) 禁用 GDM Wayland（对齐双机基线）###'
# 注意：sed 分隔符不能与模式中的 '#' 冲突，故统一使用 '|' 作为分隔符
if grep -qE '^[[:space:]]*#[[:space:]]*WaylandEnable=false' /etc/gdm3/custom.conf 2>/dev/null; then
    sudo -S sed -i 's|^[[:space:]]*#[[:space:]]*WaylandEnable=false|WaylandEnable=false|' /etc/gdm3/custom.conf <<< "$SUDO_PASS" 2>/dev/null
elif grep -qE '^WaylandEnable=' /etc/gdm3/custom.conf 2>/dev/null; then
    sudo -S sed -i 's|^WaylandEnable=.*|WaylandEnable=false|' /etc/gdm3/custom.conf <<< "$SUDO_PASS" 2>/dev/null
else
    sudo -S sed -i 's|^\[daemon\]|[daemon]\nWaylandEnable=false|' /etc/gdm3/custom.conf <<< "$SUDO_PASS" 2>/dev/null
fi
grep -nE '^WaylandEnable' /etc/gdm3/custom.conf 2>&1
echo '(提示：需重启 gdm 方可生效，本脚本默认不重启，避免中断本地控制台会话)'

echo '### 5b) 关闭 GDM 自动登录（对齐 Node0/Node1 基线）###'
# 关键：Node123 原先启用了 AutomaticLoginEnable=true，本地会常驻一个同用户的
# 图形 GNOME 会话，与远程 xrdp 会话争抢同一用户 D-Bus 总线上的
# org.gnome.Shell / org.gnome.Mutter.ServiceChannel 等全局总线名，
# 导致后启动的远程 gnome-shell 因获取不到总线名而静默退出（表现为登录后黑屏）。
# Node0 / Node1 均未启用自动登录，故此处一并关闭以消除总线名冲突。
sudo -S sed -i 's|^[[:space:]]*AutomaticLoginEnable[[:space:]]*=.*|AutomaticLoginEnable = false|' /etc/gdm3/custom.conf <<< "$SUDO_PASS" 2>/dev/null
sudo -S sed -i 's|^[[:space:]]*AutomaticLogin[[:space:]]*=.*|# AutomaticLogin = bot|' /etc/gdm3/custom.conf <<< "$SUDO_PASS" 2>/dev/null
grep -nE 'AutomaticLogin|WaylandEnable' /etc/gdm3/custom.conf 2>&1
echo '(提示：需重启 gdm 后本地自动登录会话退出，远程 xrdp 会话不再与本地争抢总线名)'

echo '### 6) 清理残留 XFCE 会话并重启 xrdp-sesman ###'
pkill -u "$(id -u)" -f 'xfce4-session' 2>/dev/null
pkill -u "$(id -u)" -f 'startxfce4' 2>/dev/null
pkill -u "$(id -u)" -f 'xfce4-panel' 2>/dev/null
sleep 1
sudo -S systemctl restart xrdp-sesman <<< "$SUDO_PASS" 2>/dev/null
systemctl is-active xrdp-sesman 2>&1

echo '### 完成，下次 xrdp 连接将重建 GNOME 会话 ###'
"""

SCRIPT_RESTART_GDM = r"""
set -u
echo '=== 重启 gdm（使 Wayland/自动登录开关生效）==='
sudo -S systemctl restart gdm3 <<< "$SUDO_PASS" 2>/dev/null || sudo -S systemctl restart gdm <<< "$SUDO_PASS" 2>/dev/null
sleep 5
systemctl is-active gdm3 2>&1
echo '--- 当前 gdm 会话（确认本地自动登录已退出）---'
loginctl list-sessions --no-legend 2>/dev/null | while read -r sid rest; do
    u=$(loginctl show-session "$sid" -p Name --value 2>/dev/null)
    t=$(loginctl show-session "$sid" -p Type --value 2>/dev/null)
    c=$(loginctl show-session "$sid" -p Class --value 2>/dev/null)
    echo "session=$sid user=$u type=$t class=$c"
done
echo '--- 同用户残留 gnome-shell（应为空）---'
pgrep -u "$(id -u)" -a -x gnome-shell 2>/dev/null || echo '(无残留 gnome-shell)'
"""

SCRIPT_ROLLBACK = r"""
set -u
echo '### 回滚：恢复 XFCE 版 ~/.xsession ###'
BAK="$(ls -t "$HOME"/.xsession.xfce.bak.* 2>/dev/null | head -n 1)"
if [ -n "$BAK" ]; then
    cp "$BAK" "$HOME/.xsession"
    chmod 755 "$HOME/.xsession"
    echo "已恢复 $HOME/.xsession（来自 $BAK）"
else
    echo '(未找到 XFCE 备份，无法回滚）'
fi
echo '### 回滚：移除 GNOME 辅助文件 ###'
rm -f "$HOME/.xsession-gnome" "$HOME/.xsessionrc" && echo '已删除 ~/.xsession-gnome 与 ~/.xsessionrc'
echo '### 回滚：恢复 sesman.ini ###'
BAK2="$(ls -t /etc/xrdp/sesman.ini.bak.* 2>/dev/null | head -n 1)"
if [ -n "$BAK2" ]; then
    sudo -S cp "$BAK2" /etc/xrdp/sesman.ini <<< "$SUDO_PASS" 2>/dev/null
    echo "已恢复 /etc/xrdp/sesman.ini（来自 $BAK2）"
fi
echo '### 回滚完成 ###'
"""


def run_ssh(host: str, user: str, password: str, script: str, sudo_pass: str = "",
            timeout: int = 90) -> str:
    """通过 SSH 执行远端脚本（base64 编码传输，规避多层转义问题）。"""
    import paramiko

    payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, port=22, username=user, password=password or None, timeout=12)
        env_prefix = f"export SUDO_PASS={sudo_pass!r}; " if sudo_pass else ""
        cmd = f"{env_prefix}echo {payload} | base64 -d | bash"
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        if err.strip():
            out += f"\n[stderr]\n{err}"
        return out
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="将工作站远程桌面统一到 GNOME Desktop")
    parser.add_argument("--host", required=True, help="目标主机 IP")
    parser.add_argument("--user", required=True, help="SSH 用户名")
    parser.add_argument("--password", default="", help="SSH 口令（缺省读环境变量 SSH_PASS）")
    parser.add_argument("--apply", action="store_true", help="执行切换（默认仅巡检）")
    parser.add_argument("--rollback", action="store_true", help="回滚到 XFCE 会话")
    parser.add_argument("--skip-wayland", action="store_true",
                        help="不改动 GDM Wayland 开关（保持本地控制台现状）")
    parser.add_argument("--restart-gdm", action="store_true",
                        help="部署后重启 gdm（使 Wayland/自动登录开关生效；会中断本地控制台会话）")
    args = parser.parse_args()

    init_env()
    password = args.password or os.getenv("SSH_PASS", "")

    logger.info("=" * 60)
    logger.info(f"目标节点: {args.user}@{args.host}")
    logger.info("=" * 60)

    logger.info("[阶段 1/2] 会话基线巡检")
    print(run_ssh(args.host, args.user, password, SCRIPT_CHECK))

    if args.rollback:
        logger.info("[阶段 2/2] 执行回滚（恢复 XFCE 会话）")
        print(run_ssh(args.host, args.user, password, SCRIPT_ROLLBACK, sudo_pass=password))
        return 0

    if not args.apply:
        logger.info("巡检完成。加 --apply 执行切换到 GNOME Desktop。")
        return 0

    script = SCRIPT_APPLY.replace("__XSESSIONRC__", XSESSIONRC_TEMPLATE).replace("__PKLA__", COLORD_PKLA)
    if args.skip_wayland:
        script = script.replace(
            "sudo -S sed -i 's|^[[:space:]]*#[[:space:]]*WaylandEnable=false|WaylandEnable=false|' "
            "/etc/gdm3/custom.conf <<< \"$SUDO_PASS\" 2>/dev/null",
            "echo '(已按 --skip-wayland 跳过 Wayland 开关改动)'")

    logger.info("[阶段 2/3] 执行切换到 GNOME Desktop")
    print(run_ssh(args.host, args.user, password, script, sudo_pass=password))

    if args.restart_gdm:
        logger.info("[阶段 3/3] 重启 gdm 使开关生效")
        print(run_ssh(args.host, args.user, password, SCRIPT_RESTART_GDM, sudo_pass=password))
    else:
        logger.info("[阶段 3/3] 未重启 gdm（加 --restart-gdm 可使 Wayland/自动登录开关生效）")

    logger.info("等待 xrdp-sesman 就绪…")
    time.sleep(2)
    logger.info("复检切换结果：")
    print(run_ssh(args.host, args.user, password, SCRIPT_CHECK))
    return 0


if __name__ == "__main__":
    sys.exit(main())
