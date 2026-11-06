#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在 Node0 上安装 python3-fonttools，从 TTC 解包提取纯 TTF 字体，并更新 fontconfig
"""

import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

remote_extractor = """
import os
from fontTools.ttLib import TTCollection

ttc_path = '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'
if os.path.exists(ttc_path):
    ttc = TTCollection(ttc_path)
    print(f'TTC 包含 {len(ttc)} 个字体')
    out_ttf = '/usr/share/fonts/truetype/wqy/wqy-microhei.ttf'
    ttc[0].save(out_ttf)
    print(f'成功保存 {out_ttf}')

# 同时将 NotoSansCJK-Regular.ttc 的简体中文也提取出来
noto_ttc = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
if os.path.exists(noto_ttc):
    ttc2 = TTCollection(noto_ttc)
    print(f'Noto TTC 包含 {len(ttc2)} 个字体')
    for i, f in enumerate(ttc2):
        name = f['name'].getDebugName(4)
        print(f'Face {i}: {name}')
        if 'SC' in str(name):
            out_noto = '/usr/share/fonts/opentype/noto/NotoSansCJK-SC-Regular.otf'
            f.save(out_noto)
            print(f'成功保存 {out_noto}')
            break
"""

sftp = c.open_sftp()
with sftp.file("/tmp/extract.py", "w") as f:
    f.write(remote_extractor)
sftp.close()

script = """
echo winbot | sudo -S apt-get install -y python3-fonttools
sudo python3 /tmp/extract.py
ls -lh /usr/share/fonts/truetype/wqy/*.ttf /usr/share/fonts/opentype/noto/*.otf 2>/dev/null
sudo fc-cache -f
"""

stdin, stdout, stderr = c.exec_command(script, get_pty=True)
print("OUT:\n", stdout.read().decode("utf-8", errors="replace"))
c.close()
