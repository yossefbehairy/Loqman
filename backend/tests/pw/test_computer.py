import ctypes
import os
from ctypes import wintypes

import pytest

from backend.pyworker.core.computer import ComputerSession, ComputerError


@pytest.mark.skipif(os.name != "nt", reason="Windows native window test")
def test_native_window_capture_and_user_control():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user.CreateWindowExW.restype = wintypes.HWND
    user.DestroyWindow.argtypes = [wintypes.HWND]
    handle = user.CreateWindowExW(0x08000000, "STATIC", "AgentOS isolated native test", 0x90000000,
                                  -10000, -10000, 240, 120, None, None, None, None)
    assert handle
    try:
        computer = ComputerSession(handle)
        image = computer.call("computer_screenshot", {})
        import base64
        assert base64.b64decode(image["image"]).startswith(b"\x89PNG\r\n\x1a\n")
        assert image["width"] == 240 and image["height"] == 120
        computer.human_control = True
        with pytest.raises(ComputerError, match="belongs to the user"):
            computer.call("computer_click", {"x": 10, "y": 10})
    finally:
        user.DestroyWindow(handle)
    computer.human_control = False
    with pytest.raises(ComputerError, match="no longer available"):
        computer.check()
