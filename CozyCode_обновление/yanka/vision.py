"""Зрение Янки: снимок экрана (только по просьбе Яны) и подготовка картинок для модели.
Только стандартная библиотека: GDI через ctypes + свой маленький PNG-кодировщик (zlib)."""
import base64
import struct
import sys
import zlib
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
MAX_IMAGE_BYTES = 6 * 1024 * 1024


def png_bytes(width, height, rgb):
    """rgb - bytes длиной width*height*3 (строки сверху вниз)."""
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # фильтр «без фильтра»
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def bgra_to_rgb(buf, width, height):
    n = width * height
    rgb = bytearray(n * 3)
    rgb[0::3] = buf[2::4][:n]
    rgb[1::3] = buf[1::4][:n]
    rgb[2::3] = buf[0::4][:n]
    return bytes(rgb)


def capture_screen(max_width=1600):
    """Снимок основного монитора, уменьшенный до max_width. Возвращает PNG (bytes)."""
    if not IS_WINDOWS:
        raise RuntimeError("снимок экрана работает только в Windows")
    import ctypes
    from ctypes import wintypes
    u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
    try:
        u32.SetProcessDPIAware()
    except Exception:
        pass
    sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
    scale = min(1.0, max_width / float(sw))
    w, h = max(1, int(sw * scale)), max(1, int(sh * scale))
    # 64-битные дескрипторы: без argtypes ctypes режет их до int и падает (OverflowError)
    HDC, HBM, HGO = wintypes.HDC, wintypes.HBITMAP, wintypes.HGDIOBJ
    u32.GetDC.argtypes, u32.GetDC.restype = [wintypes.HWND], HDC
    u32.ReleaseDC.argtypes = [wintypes.HWND, HDC]
    g32.CreateCompatibleDC.argtypes, g32.CreateCompatibleDC.restype = [HDC], HDC
    g32.CreateCompatibleBitmap.argtypes, g32.CreateCompatibleBitmap.restype = [HDC, ctypes.c_int, ctypes.c_int], HBM
    g32.SelectObject.argtypes, g32.SelectObject.restype = [HDC, HGO], HGO
    g32.SetStretchBltMode.argtypes = [HDC, ctypes.c_int]
    g32.StretchBlt.argtypes = [HDC] + [ctypes.c_int] * 4 + [HDC] + [ctypes.c_int] * 4 + [wintypes.DWORD]
    g32.GetDIBits.argtypes = [HDC, HBM, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    g32.DeleteObject.argtypes = [HGO]
    g32.DeleteDC.argtypes = [HDC]
    screen = u32.GetDC(None)
    mem = g32.CreateCompatibleDC(screen)
    bmp = g32.CreateCompatibleBitmap(screen, w, h)
    old = g32.SelectObject(mem, bmp)
    try:
        g32.SetStretchBltMode(mem, 4)  # HALFTONE - аккуратное уменьшение
        if not g32.StretchBlt(mem, 0, 0, w, h, screen, 0, 0, sw, sh, 0x00CC0020):
            raise RuntimeError("не получилось снять экран")

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]
        bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # -h: строки сверху вниз
        buf = ctypes.create_string_buffer(w * h * 4)
        if not g32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0):
            raise RuntimeError("не получилось прочитать снимок")
        return png_bytes(w, h, bgra_to_rgb(buf.raw, w, h))
    finally:
        g32.SelectObject(mem, old)
        g32.DeleteObject(bmp)
        g32.DeleteDC(mem)
        u32.ReleaseDC(None, screen)


def is_image(path):
    return Path(path).suffix.lower() in IMAGE_EXT


def data_url(path):
    p = Path(path)
    data = p.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"картинка {p.name} слишком большая (больше 6 МБ)")
    return f"data:{IMAGE_EXT.get(p.suffix.lower(), 'image/png')};base64," + base64.b64encode(data).decode("ascii")
