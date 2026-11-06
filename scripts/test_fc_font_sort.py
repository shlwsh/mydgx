#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 ctypes 直接调用 libfontconfig 的 FcFontSort，测试系统真实的字体回退链
"""

import paramiko
from env_helper import init_env

init_env()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.19.50.70", username="winbot", password="winbot")

script = """
python3 -c "
import ctypes
from ctypes import util

fc = ctypes.CDLL('libfontconfig.so.1')

# 初始化
fc.FcInit()

# 创建 pattern
pat = fc.FcNameParse(b'sans-serif')
fc.FcConfigSubstitute(None, pat, 0)
fc.FcDefaultSubstitute(pat)

res = ctypes.c_int()
fs = fc.FcFontSort(None, pat, 0, None, ctypes.byref(res))

class FcFontSet(ctypes.Structure):
    _fields_ = [
        ('nfont', ctypes.c_int),
        ('sfont', ctypes.c_int),
        ('fonts', ctypes.POINTER(ctypes.c_void_p))
    ]

fs_obj = FcFontSet.from_address(fs)
print(f'FcFontSort 返回字体总数: {fs_obj.nfont}')

# 查看前 20 个字体
for i in range(min(25, fs_obj.nfont)):
    font_ptr = fs_obj.fonts[i]
    val = ctypes.c_char_p()
    fc.FcPatternGetString(font_ptr, b'file', 0, ctypes.byref(val))
    file_path = val.value.decode() if val.value else 'unknown'
    fam = ctypes.c_char_p()
    fc.FcPatternGetString(font_ptr, b'family', 0, ctypes.byref(fam))
    fam_name = fam.value.decode() if fam.value else 'unknown'
    print(f'[{i}] {fam_name} -> {file_path}')
"
"""

stdin, stdout, stderr = c.exec_command(script)
print("STDOUT:\n", stdout.read().decode("utf-8", errors="replace"))
print("STDERR:\n", stderr.read().decode("utf-8", errors="replace"))
c.close()
