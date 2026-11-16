"""Window-scoped Windows automation using the standard-library ctypes FFI."""

import base64
import ctypes
import os
import struct
import zlib
from ctypes import wintypes


class ComputerError(Exception):
    pass


def windows():
    if os.name != "nt":
        return []
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    result = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(handle, _):
        if user.IsWindowVisible(handle):
            title = ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(handle, title, 512)
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            if title.value:
                result.append({"handle": int(handle), "title": title.value, "pid": pid.value})
        return True

    user.EnumWindows(visit, 0)
    return result


class ComputerSession:
    def __init__(self, handle):
        if os.name != "nt":
            raise ComputerError("Computer tools require Windows.")
        self.handle = handle
        self.human_control = False
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.user.IsWindow.argtypes = [wintypes.HWND]
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
        self.user.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        self.user.GetDC.argtypes = [wintypes.HWND]
        self.user.GetDC.restype = wintypes.HDC
        self.user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        self.gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        self.gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        self.gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self.gdi.SelectObject.restype = wintypes.HGDIOBJ
        self.gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                      ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        self.pid = pid.value
        self.check()

    def check(self):
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(self.handle, ctypes.byref(pid))
        if not self.user.IsWindow(self.handle) or pid.value != self.pid:
            raise ComputerError("Selected window is no longer available.")
        if self.human_control:
            raise ComputerError("Computer control belongs to the user.")

    def rect(self):
        self.check()
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(self.handle, ctypes.byref(rect)):
            raise ComputerError("Could not read selected window bounds.")
        return rect

    def screenshot(self):
        rect = self.rect()
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 1 or height < 1 or width * height > 4_000_000:
            raise ComputerError("Selected window size is unsupported.")
        source = self.user.GetDC(self.handle)
        memory = self.gdi.CreateCompatibleDC(source)
        bitmap = self.gdi.CreateCompatibleBitmap(source, width, height)
        previous = self.gdi.SelectObject(memory, bitmap)
        try:
            if not (self.user.PrintWindow(self.handle, memory, 2)
                    or self.user.PrintWindow(self.handle, memory, 0)):
                raise ComputerError("Selected application could not render a screenshot.")
            self.gdi.SelectObject(memory, previous)
            info = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32,
                                                          0, width * height * 4, 0, 0, 0, 0))
            pixels = ctypes.create_string_buffer(width * height * 4)
            if not self.gdi.GetDIBits(memory, bitmap, 0, height, pixels, info, 0):
                raise ComputerError("Screenshot pixels unavailable.")
            # Bound the image while retaining the original coordinate dimensions.
            scale = max(1, (max(width, height) + 639) // 640)
            output_width, output_height = (width + scale - 1) // scale, (height + scale - 1) // scale
            raw = pixels.raw
            rows = bytearray()
            for y in range(0, height, scale):
                rows.append(0)
                for x in range(0, width, scale):
                    index = (y * width + x) * 4
                    rows.extend((raw[index+2], raw[index+1], raw[index]))

            def chunk(kind, data):
                return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

            png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", output_width, output_height, 8, 2, 0, 0, 0))
            png += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
            return {"window": self.handle, "width": width, "height": height, "image_scale": scale,
                    "image": base64.b64encode(png).decode(), "mimeType": "image/png"}
        finally:
            self.gdi.SelectObject(memory, previous)
            self.gdi.DeleteObject(bitmap)
            self.gdi.DeleteDC(memory)
            self.user.ReleaseDC(self.handle, source)

    def call(self, name, args):
        self.check()
        if name == "computer_focus":
            self.user.SetForegroundWindow(self.handle)
        elif name in ("computer_click", "computer_type"):
            if self.user.GetForegroundWindow() != self.handle:
                raise ComputerError("Focus the selected window before sending input.")
            if name == "computer_click":
                x, y = args.get("x"), args.get("y")
                rect = self.rect()
                if type(x) is not int or type(y) is not int or not 0 <= x < rect.right-rect.left or not 0 <= y < rect.bottom-rect.top:
                    raise ComputerError("Click coordinates must be inside the selected window.")
                point = wintypes.POINT(rect.left + x, rect.top + y)
                target = self.user.WindowFromPoint(point)
                if not target or self.user.GetAncestor(target, 2) != self.handle:
                    raise ComputerError("Click point is obscured or outside the visible selected window.")
                self.user.SetCursorPos(point.x, point.y)
                self.check()
                actual = wintypes.POINT()
                self.user.GetCursorPos(ctypes.byref(actual))
                if (actual.x, actual.y) != (point.x, point.y) or self.user.GetForegroundWindow() != self.handle:
                    raise ComputerError("Pointer or foreground window changed before the click.")
                # Deliver to the checked window rather than the global input stream.
                client = wintypes.POINT(point.x, point.y)
                self.user.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
                if not self.user.ScreenToClient(target, ctypes.byref(client)):
                    raise ComputerError("Could not map click to the selected window.")
                client_rect = wintypes.RECT()
                if (not self.user.GetClientRect(target, ctypes.byref(client_rect))
                        or not (0 <= client.x < client_rect.right and 0 <= client.y < client_rect.bottom)
                        or client.x > 32767 or client.y > 32767):
                    raise ComputerError("Click point is outside the target client area.")
                if self.user.GetForegroundWindow() != self.handle:
                    raise ComputerError("Foreground window changed before the click.")
                self.user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                                         wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                                         ctypes.POINTER(ctypes.c_size_t)]
                position = (client.x & 0xffff) | ((client.y & 0xffff) << 16)
                sent = ctypes.c_size_t()
                for message, buttons in ((0x201, 1), (0x202, 0)):
                    if self.user.GetForegroundWindow() != self.handle:
                        raise ComputerError("Foreground window changed during the click.")
                    if not self.user.SendMessageTimeoutW(target, message, buttons, position, 2, 500, ctypes.byref(sent)):
                        raise ComputerError("Selected application did not accept the click.")
            else:
                text = args.get("text")
                if not isinstance(text, str) or len(text) > 2000:
                    raise ComputerError("Provide at most 2000 characters.")
                # WM_CHAR is scoped to this foreground window's focused child.
                class GuiInfo(ctypes.Structure):
                    _fields_ = [("size", wintypes.DWORD), ("flags", wintypes.DWORD),
                                ("active", wintypes.HWND), ("focus", wintypes.HWND),
                                ("capture", wintypes.HWND), ("menu", wintypes.HWND),
                                ("move", wintypes.HWND), ("caret", wintypes.HWND),
                                ("rect", wintypes.RECT)]
                info = GuiInfo()
                info.size = ctypes.sizeof(info)
                thread = self.user.GetWindowThreadProcessId(self.handle, None)
                if not self.user.GetGUIThreadInfo(thread, ctypes.byref(info)) or not info.focus:
                    raise ComputerError("Selected window has no focused input.")
                if info.focus != self.handle and not self.user.IsChild(self.handle, info.focus):
                    raise ComputerError("Focused input is outside the selected window.")
                self.user.SendMessageTimeoutW.argtypes = [
                    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
                encoded = text.encode("utf-16-le")
                for index in range(0, len(encoded), 2):
                    self.check()
                    if self.user.GetForegroundWindow() != self.handle:
                        raise ComputerError("Foreground window changed during input.")
                    unit = int.from_bytes(encoded[index:index+2], "little")
                    sent = ctypes.c_size_t()
                    if not self.user.SendMessageTimeoutW(info.focus, 0x102, unit, 0, 2, 500, ctypes.byref(sent)):
                        raise ComputerError("Selected application did not accept text input.")
        elif name != "computer_screenshot":
            raise ComputerError("Unknown computer tool.")
        return self.screenshot()
