import ctypes

fc = ctypes.CDLL('libfontconfig.so.1')
fc.FcInit()

fc.FcNameParse.restype = ctypes.c_void_p
fc.FcNameParse.argtypes = [ctypes.c_char_p]
fc.FcFontSort.restype = ctypes.c_void_p
fc.FcFontSort.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
fc.FcPatternGetString.restype = ctypes.c_int
fc.FcPatternGetString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]

class FcFontSet(ctypes.Structure):
    _fields_ = [
        ('nfont', ctypes.c_int),
        ('sfont', ctypes.c_int),
        ('fonts', ctypes.POINTER(ctypes.c_void_p))
    ]

def test_pattern(pattern_str):
    print(f"=== 测试 Pattern: {pattern_str.decode()} ===")
    pat = fc.FcNameParse(pattern_str)
    fc.FcConfigSubstitute(None, pat, 0)
    fc.FcDefaultSubstitute(pat)

    res = ctypes.c_int()
    fs = fc.FcFontSort(None, pat, 0, None, ctypes.byref(res))
    fs_obj = FcFontSet.from_address(fs)
    for i in range(min(12, fs_obj.nfont)):
        font_ptr = fs_obj.fonts[i]
        fam = ctypes.c_char_p()
        fc.FcPatternGetString(font_ptr, b'family', 0, ctypes.byref(fam))
        fam_name = fam.value.decode('utf-8', 'ignore') if fam.value else 'unknown'
        val = ctypes.c_char_p()
        fc.FcPatternGetString(font_ptr, b'file', 0, ctypes.byref(val))
        file_path = val.value.decode('utf-8', 'ignore') if val.value else 'unknown'
        print(f"[{i:2d}] {fam_name} -> {file_path}")

test_pattern(b"Cantarell")
test_pattern(b"")
test_pattern(b"JetBrainsMono")
test_pattern(b"sans")
