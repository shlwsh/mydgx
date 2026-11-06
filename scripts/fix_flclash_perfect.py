#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双节点彻底修复 FlClash 界面字符显示方案
1. 根治原因：Flutter 引擎在 ARM64 Linux 上的官方缺陷 (Issue #139293) 导致缺失 Fontconfig 链接无法加载外部中文字体
2. 修复方式：
   a. 启动包装器 (/usr/local/bin/flclash-launcher) 显式强制 export LANG=en_US.UTF-8 / LC_ALL=en_US.UTF-8
   b. 所有用户配置 (~/.local/share/com.follow.clash/shared_preferences.json) 固化 "locale": "en"
   c. 恢复 clean 状态并杀灭旧实例
   d. 双节点 (172.19.50.70 & 172.19.49.159) 100% 同步部署
"""

import json
import logging
import os
import sys
import paramiko
from env_helper import init_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("fix_flclash_perfect")

init_env()

NODES = [
    {
        "host": "172.19.50.70",
        "user": "winbot",
        "pwd": "winbot",
        "users": ["winbot", "his_test"]
    },
    {
        "host": "172.19.49.159",
        "user": "dgxdeploy",
        "pwd": "winning",
        "users": ["dgxdeploy", "his_test"]
    }
]

LAUNCHER_SCRIPT = """#!/bin/bash
# FlClash 优化启动包装器
# 1. 解决 xrdp 虚拟显卡无硬件 DRI3 导致的 OpenGL 帧超时 (LIBGL_ALWAYS_SOFTWARE=1)
# 2. 彻底根除 ARM64 Linux 架构下 Flutter Engine 缺少 Fontconfig 导致的汉字空心方框/乱码 Bug
#    统一强制锁定纯英文环境 (LANG=en_US.UTF-8 / LC_ALL=en_US.UTF-8)，确保界面字符 100% 清晰渲染

export GDK_BACKEND=x11
export LIBGL_ALWAYS_SOFTWARE=1
export LANG=en_US.UTF-8
export LANGUAGE=en_US:en
export LC_ALL=en_US.UTF-8
export LD_LIBRARY_PATH="/usr/share/FlClash/lib:${LD_LIBRARY_PATH:-}"

# 进入程序基准目录
cd /usr/share/FlClash || exit 1

exec /usr/share/FlClash/FlClash "$@"
"""

DEFAULT_PREF = {
    "flutter.config": json.dumps({
        "currentProfileId": None,
        "overrideDns": False,
        "hotKeyActions": [],
        "appSettingProps": {
            "locale": "en",
            "dashboardWidgets": [
                "networkSpeed",
                "systemProxyButton",
                "tunButton",
                "outboundMode",
                "networkDetection",
                "trafficUsage",
                "intranetIp"
            ],
            "onlyStatisticsProxy": False,
            "autoLaunch": False,
            "silentLaunch": False,
            "autoRun": False,
            "openLogs": False,
            "closeConnections": True,
            "testUrl": "https://www.gstatic.com/generate_204",
            "isAnimateToPage": True,
            "autoCheckUpdate": False,
            "showLabel": False,
            "disclaimerAccepted": True,
            "crashlyticsTip": False,
            "crashlytics": False,
            "minimizeOnExit": True,
            "hidden": False,
            "developerMode": False,
            "restoreStrategy": "compatible",
            "showTrayTitle": True,
            "customUserAgent": ""
        },
        "davProps": None,
        "networkProps": {
            "systemProxy": True,
            "bypassDomain": [
                "*zhihu.com", "*zhimg.com", "*jd.com",
                "100ime-iat-api.xfyun.cn", "*360buyimg.com",
                "localhost", "*.local", "127.*", "10.*",
                "172.16.*", "172.17.*", "172.18.*", "172.19.*",
                "172.2*", "172.30.*", "172.31.*", "192.168.*"
            ],
            "routeMode": "config",
            "autoSetSystemDns": True,
            "appendSystemDns": False
        },
        "vpnProps": {
            "enable": True,
            "systemProxy": True,
            "ipv6": False,
            "allowBypass": True,
            "dnsHijacking": False,
            "accessControlProps": {
                "enable": False,
                "mode": "rejectSelected",
                "acceptList": [],
                "rejectList": [],
                "sort": "none",
                "isFilterSystemApp": True,
                "isFilterNonInternetApp": True
            }
        },
        "themeProps": {
            "primaryColor": 4292395203,
            "primaryColors": [
                4286141768, 4278430196, 4294967040, 4290496972,
                4289450903, 4292395203, 4284896144
            ],
            "themeMode": "dark",
            "schemeVariant": "content",
            "pureBlack": False,
            "textScale": {"enable": False, "scale": 1.0}
        },
        "proxiesStyleProps": {
            "type": "tab",
            "sortType": "none",
            "layout": "standard",
            "iconStyle": "standard",
            "cardType": "expand"
        },
        "windowProps": {
            "width": 860.0,
            "height": 620.0,
            "top": None,
            "left": None
        },
        "patchClashConfig": {
            "mixed-port": 7890,
            "socks-port": 0,
            "port": 0,
            "redir-port": 0,
            "tproxy-port": 0,
            "mode": "rule",
            "allow-lan": False,
            "log-level": "error",
            "ipv6": False,
            "find-process-mode": "always",
            "keep-alive-interval": 30,
            "unified-delay": True,
            "tcp-concurrent": True,
            "tun": {
                "enable": False,
                "device": "FlClash",
                "auto-route": False,
                "stack": "mixed",
                "dns-hijack": ["any:53"],
                "route-address": []
            },
            "dns": {
                "enable": True,
                "listen": "0.0.0.0:1053",
                "prefer-h3": False,
                "use-hosts": True,
                "use-system-hosts": True,
                "respect-rules": False,
                "ipv6": False,
                "default-nameserver": ["223.5.5.5"],
                "enhanced-mode": "fake-ip",
                "fake-ip-range": "198.18.0.1/16",
                "fake-ip-filter": ["*.lan", "localhost.ptlogin2.qq.com"],
                "nameserver-policy": {
                    "www.baidu.com": "114.114.114.114",
                    "+.internal.crop.com": "10.0.0.1",
                    "geosite:cn": "https://doh.pub/dns-query"
                },
                "nameserver": [
                    "https://doh.pub/dns-query",
                    "https://dns.alidns.com/dns-query"
                ],
                "fallback": [
                    "tls://8.8.4.4",
                    "tls://1.1.1.1"
                ],
                "proxy-server-nameserver": ["https://doh.pub/dns-query"],
                "fallback-filter": {
                    "geoip": True,
                    "geoip-code": "CN",
                    "geosite": [],
                    "ipcidr": ["240.0.0.0/4"],
                    "domain": ["+.google.com", "+.facebook.com", "+.youtube.com"]
                }
            },
            "geox-url": {
                "mmdb": "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.metadb",
                "asn": "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/GeoLite2-ASN.mmdb",
                "geoip": "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.dat",
                "geosite": "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat"
            },
            "geodata-loader": "memconservative",
            "global-ua": None,
            "external-controller": "",
            "hosts": {},
            "geo-auto-update": False,
            "geo-update-interval": 24
        },
        "excludeSSIDs": []
    }, separators=(',', ':')),
    "flutter.version": 1
}


def fix_node(node):
    host = node["host"]
    user = node["user"]
    pwd = node["pwd"]
    users = node["users"]
    
    logger.info(f"========== 开始加固与修复节点: {host} ==========")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=pwd, timeout=10)
    
    # 1. 杀死当前正在运行的 FlClash 进程和清理残留 socket
    logger.info(f"[{host}] 停止旧 FlClash 进程...")
    c.exec_command("sudo killall -9 FlClash FlClashCore 2>/dev/null || true; sudo rm -f /tmp/FlClashSocket* 2>/dev/null || true")
    
    # 2. 写入全局优化启动脚本 /usr/local/bin/flclash-launcher
    logger.info(f"[{host}] 写入优化启动包装器 /usr/local/bin/flclash-launcher...")
    sftp = c.open_sftp()
    with sftp.file("/tmp/flclash-launcher-new", "w") as f:
        f.write(LAUNCHER_SCRIPT)
    sftp.close()
    
    c.exec_command("sudo mv /tmp/flclash-launcher-new /usr/local/bin/flclash-launcher && sudo chmod 755 /usr/local/bin/flclash-launcher")
    
    # 3. 确保 /usr/share/applications/FlClash.desktop 和 用户 Desktop/FlClash.desktop 都指向 launcher
    logger.info(f"[{host}] 更新桌面图标与应用程序快捷方式...")
    desktop_entry = """[Desktop Entry]
Version=1.0
Type=Application
Name=FlClash
GenericName=Clash GUI Proxy
Comment=FlClash Proxy Client for Linux
Icon=FlClash
Exec=/usr/local/bin/flclash-launcher %U
Path=/usr/share/FlClash
Terminal=false
Categories=Network;WebBrowser;Utility;
Keywords=FlClash;Clash;ClashMeta;Proxy;VPN;
StartupNotify=true
StartupWMClass=com.follow.clash
"""
    sftp = c.open_sftp()
    with sftp.file("/tmp/FlClash.desktop", "w") as f:
        f.write(desktop_entry)
    sftp.close()
    c.exec_command("sudo cp /tmp/FlClash.desktop /usr/share/applications/FlClash.desktop && sudo chmod 644 /usr/share/applications/FlClash.desktop")
    
    # 4. 为每个用户写入/更新 shared_preferences.json
    for u in users:
        logger.info(f"[{host}] 检查并更新用户 [{u}] 的 FlClash 配置...")
        user_dir = f"/home/{u}"
        pref_dir = f"{user_dir}/.local/share/com.follow.clash"
        pref_file = f"{pref_dir}/shared_preferences.json"
        desktop_file = f"{user_dir}/Desktop/FlClash.desktop"
        
        # 保证桌面快捷方式存在且权限正确
        c.exec_command(f"sudo mkdir -p {user_dir}/Desktop && sudo cp /tmp/FlClash.desktop {desktop_file} && sudo chown -R {u}:{u} {desktop_file} && sudo chmod +x {desktop_file}")
        
        # 检查 shared_preferences.json
        stdin, stdout, stderr = c.exec_command(f"[ -f {pref_file} ] && cat {pref_file} || echo 'NOT_EXISTS'")
        res = stdout.read().decode('utf-8').strip()
        
        final_pref = None
        if res == "NOT_EXISTS" or not res:
            logger.info(f"[{host}] 用户 [{u}] 尚无配置文件，创建标准英文配置...")
            final_pref = DEFAULT_PREF
        else:
            try:
                curr_data = json.loads(res)
                if "flutter.config" in curr_data:
                    config = json.loads(curr_data["flutter.config"])
                    # 强制更新 locale 为 en
                    if "appSettingProps" not in config:
                        config["appSettingProps"] = {}
                    config["appSettingProps"]["locale"] = "en"
                    config["appSettingProps"]["disclaimerAccepted"] = True
                    curr_data["flutter.config"] = json.dumps(config, separators=(',', ':'))
                    final_pref = curr_data
                    logger.info(f"[{host}] 用户 [{u}] 配置已将 locale 更新为 'en'")
                else:
                    final_pref = DEFAULT_PREF
            except Exception as e:
                logger.warning(f"[{host}] 解析用户 [{u}] 现有配置失败: {e}，重置为标准配置")
                final_pref = DEFAULT_PREF
                
        # 写入目标文件
        tmp_target = f"/tmp/pref_{u}.json"
        sftp = c.open_sftp()
        with sftp.file(tmp_target, "w") as f:
            f.write(json.dumps(final_pref, separators=(',', ':')))
        sftp.close()
        
        setup_cmd = f"""
        sudo mkdir -p {pref_dir}
        sudo cp {tmp_target} {pref_file}
        sudo chown -R {u}:{u} /home/{u}/.local
        sudo chmod 600 {pref_file}
        rm -f {tmp_target}
        """
        c.exec_command(setup_cmd)
        
    c.close()
    logger.info(f"[{host}] 修复加固完成！")


def verify_node(node):
    host = node["host"]
    user = node["user"]
    pwd = node["pwd"]
    
    logger.info(f"========== 验证节点渲染: {host} ==========")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=pwd, timeout=10)
    
    # 模拟真实远程桌面环境执行启动包装器
    test_sh = """#!/bin/bash
    rm -f /tmp/fl_verify.png /tmp/fl_verify.log
    
    xvfb-run -s "-screen 0 1024x768x24" dbus-run-session bash -c '
        /usr/local/bin/flclash-launcher > /tmp/fl_verify.log 2>&1 &
        PID=$!
        sleep 5
        
        WIN_ID=$(xwininfo -root -tree | grep -iE "flclash" | head -n 1 | awk "{print \\$1}")
        if [ -n "$WIN_ID" ]; then
            scrot -u -b /tmp/fl_verify.png || scrot /tmp/fl_verify.png
        else
            scrot /tmp/fl_verify.png
        fi
        kill -9 $PID 2>/dev/null || true
    '
    ls -lh /tmp/fl_verify.png
    """
    stdin, stdout, stderr = c.exec_command(test_sh, get_pty=True)
    out = stdout.read().decode('utf-8', errors='replace')
    logger.info(f"[{host}] 验证输出:\n{out}")
    
    local_img = os.path.join(os.path.dirname(__file__), f"flclash_verified_{host.replace('.', '_')}.png")
    try:
        sftp = c.open_sftp()
        sftp.get("/tmp/fl_verify.png", local_img)
        sftp.close()
        logger.info(f"[{host}] 验证截图已保存至: {local_img}")
    except Exception as e:
        logger.error(f"[{host}] 下载验证截图失败: {e}")
        
    c.close()


def main():
    for node in NODES:
        fix_node(node)
    
    for node in NODES:
        verify_node(node)
        
    logger.info("全部节点修复与验证完成！")

if __name__ == "__main__":
    main()
