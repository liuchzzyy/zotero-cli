"""跨平台路径辅助函数，包括 WSL/Windows 路径转换。"""

from __future__ import annotations

import re


def is_wsl_environment() -> bool:
    """若运行在 WSL2 中则返回 True。"""
    try:
        with open("/proc/version") as f:
            content = f.read().lower()
            return "microsoft" in content or "wsl" in content
    except OSError:
        return False


def windows_to_wsl_path(windows_path: str) -> str:
    """把 Windows 路径转换为 WSL 路径。

    示例：
        C:\\Users\\name\\file.pdf -> /mnt/c/Users/name/file.pdf
        \\\\server\\share\\file.pdf -> /mnt/server/share/file.pdf
        /mnt/c/already/wsl.pdf -> /mnt/c/already/wsl.pdf（原样返回）
    """
    if not windows_path:
        return ""

    # 已经是 WSL 路径
    if windows_path.startswith("/mnt/"):
        return windows_path

    # UNC 路径：\\server\share\...
    if windows_path.startswith("\\\\"):
        path = windows_path[2:]
        parts = path.split("\\")
        if len(parts) >= 2:
            return "/mnt/" + "/".join(parts)
        return windows_path

    # 盘符路径：C:\Users\...
    match = re.match(r"^([A-Za-z]):\\(.+)$", windows_path)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2)
        parts = rest.replace("\\", "/").split("/")
        return "/mnt/" + drive + "/" + "/".join(parts)

    return windows_path
