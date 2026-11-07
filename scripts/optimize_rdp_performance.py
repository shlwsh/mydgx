#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双节点 xrdp 深度性能调优与卡顿彻底根除脚本
1. 移除 xorg.conf 中无头 GPU 导致的 DRI3 超时陷阱
2. 调大 xrdp.ini TCP 发送/接收缓冲区至 4MB (4194304 bytes)
3. 开启 fastpath 与 bulk_compression
4. 修复 sesman.ini 异常断开会话保留策略
5. 双节点同步应用并重启服务
"""

import logging
import paramiko
from env_helper import init_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("optimize_rdp")

init_env()

NODES = [
    {"host": "172.19.50.70", "user": "winbot", "pwd": "winbot"},
    {"host": "172.19.49.159", "user": "dgxdeploy", "pwd": "winning"}
]

# 优化后的 /etc/X11/xrdp/xorg.conf（移除引发无头 DRM 渲染超时的 DRI3 / renderD128 绑定）
OPTIMIZED_XORG_CONF = """
Section "ServerLayout"
    Identifier "X11 Server"
    Screen "Screen (xrdpdev)"
    InputDevice "xrdpMouse" "CorePointer"
    InputDevice "xrdpKeyboard" "CoreKeyboard"
EndSection

Section "ServerFlags"
    Option "DefaultServerLayout" "X11 Server"
    Option "DontVTSwitch" "on"
    Option "AutoAddDevices" "off"
EndSection

Section "Module"
    Load "dbe"
    Load "ddc"
    Load "extmod"
    Load "glx"
    Load "int10"
    Load "record"
    Load "vbe"
    Load "glamoregl"
    Load "xorgxrdp"
    Load "fb"
EndSection

Section "InputDevice"
    Identifier "xrdpKeyboard"
    Driver "xrdpkeyb"
EndSection

Section "InputDevice"
    Identifier "xrdpMouse"
    Driver "xrdpmouse"
EndSection

Section "Monitor"
    Identifier "Monitor"
    Option "DPMS"
    HorizSync 30-80
    VertRefresh 60-75
    ModeLine "1920x1080" 138.500 1920 1968 2000 2080 1080 1083 1088 1111 +hsync -vsync
    ModeLine "1280x720" 74.25 1280 1720 1760 1980 720 725 730 750 +HSync +VSync
    Modeline "1368x768" 72.25 1368 1416 1448 1528 768 771 781 790 +hsync -vsync
    Modeline "1600x900" 119.00 1600 1696 1864 2128 900 901 904 932 -hsync +vsync
EndSection

Section "Device"
    Identifier "Video Card (xrdpdev)"
    Driver "xrdpdev"
EndSection

Section "Screen"
    Identifier "Screen (xrdpdev)"
    Device "Video Card (xrdpdev)"
    Monitor "Monitor"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "640x480" "800x600" "1024x768" "1280x720" "1280x1024" "1600x900" "1920x1080"
    EndSubSection
EndSection
"""


def tune_server(node):
    host = node["host"]
    user = node["user"]
    pwd = node["pwd"]
    logger.info(f"========== 调优节点: {host} ==========")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=pwd, timeout=10)

    # 1. 更新 xorg.conf
    logger.info(f"[{host}] 写入优化后的 xorg.conf (移除 DRI3 无头硬件超时)...")
    sftp = c.open_sftp()
    with sftp.file("/tmp/xorg.conf.opt", "w") as f:
        f.write(OPTIMIZED_XORG_CONF.strip() + "\n")
    sftp.close()
    c.exec_command("sudo cp /tmp/xorg.conf.opt /etc/X11/xrdp/xorg.conf && sudo chmod 644 /etc/X11/xrdp/xorg.conf")

    # 2. 调优 /etc/xrdp/xrdp.ini (网络大缓冲与高帧率)
    logger.info(f"[{host}] 调优 /etc/xrdp/xrdp.ini (TCP 缓冲区增大至 4MB，启用 FastPath)...")
    tune_xrdp_cmd = """
    sudo sed -i 's/^#*tcp_send_buffer_bytes=.*/tcp_send_buffer_bytes=4194304/' /etc/xrdp/xrdp.ini
    sudo sed -i 's/^#*tcp_recv_buffer_bytes=.*/tcp_recv_buffer_bytes=4194304/' /etc/xrdp/xrdp.ini
    sudo sed -i 's/^#*use_fastpath=.*/use_fastpath=both/' /etc/xrdp/xrdp.ini
    sudo sed -i 's/^#*bulk_compression=.*/bulk_compression=true/' /etc/xrdp/xrdp.ini
    sudo sed -i 's/^#*bitmap_compression=.*/bitmap_compression=true/' /etc/xrdp/xrdp.ini
    sudo sed -i 's/^#*bitmap_cache=.*/bitmap_cache=true/' /etc/xrdp/xrdp.ini
    sudo sed -i 's/^#*max_bpp=.*/max_bpp=24/' /etc/xrdp/xrdp.ini
    """
    c.exec_command(tune_xrdp_cmd)

    # 3. 调优 /etc/xrdp/sesman.ini (会话稳定性)
    logger.info(f"[{host}] 调优 /etc/xrdp/sesman.ini (关闭瞬断杀会话，允许平滑重连)...")
    tune_sesman_cmd = """
    sudo sed -i 's/^KillDisconnected=.*/KillDisconnected=false/' /etc/xrdp/sesman.ini
    sudo sed -i 's/^DisconnectedTimeLimit=.*/DisconnectedTimeLimit=0/' /etc/xrdp/sesman.ini
    """
    c.exec_command(tune_sesman_cmd)

    # 4. 重启 xrdp 和 xrdp-sesman 服务
    logger.info(f"[{host}] 重启 xrdp 服务...")
    c.exec_command("sudo systemctl restart xrdp-sesman xrdp")

    c.close()
    logger.info(f"[{host}] 服务端调优完成！")


def main():
    for node in NODES:
        tune_server(node)
    logger.info("全部服务端调优完成！")

if __name__ == "__main__":
    main()
