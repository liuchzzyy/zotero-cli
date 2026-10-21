"""本地附件定位与跨平台路径转换。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


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


_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


_WINDOWS_UNC_PATH_RE = re.compile(r"^\\\\[^\\/]+[\\/]")


_WSL_DRIVE_URL_RE = re.compile(r"^/[A-Za-z]:/")


class AttachmentResolver:
    """把 Zotero 附件路径解析为文件系统路径。

    支持 4 种格式：
    - 'storage:filename.pdf' —— Zotero 受管存储
    - 'file:///path/to/file.pdf' —— 以 URL 表示的链接文件
    - '/absolute/path/to/file.pdf' —— 以绝对路径表示的链接文件
    - 'attachments:relative/path.pdf' —— Zotero 链接附件基准目录
    """

    def __init__(self, db_path: Path, prefs_js_path: Path | None = None) -> None:
        self._db_path = db_path
        self._prefs_js_path = prefs_js_path
        self._base_attachment_path: Path | None = None
        self._base_attachment_path_loaded = False

    @property
    def storage_dir(self) -> Path:
        """根据数据库位置返回 Zotero storage 目录路径。"""
        return self._db_path.parent / "storage"

    def _read_base_attachment_path(self) -> Path:
        if self._base_attachment_path_loaded and self._base_attachment_path is not None:
            return self._base_attachment_path

        prefs_locations: list[Path] = []
        if self._prefs_js_path:
            prefs_locations.append(self._prefs_js_path)
        prefs_locations.extend(
            [
                self._db_path.parent / "prefs.js",
            ]
        )

        for prefs_path in prefs_locations:
            if prefs_path.exists():
                try:
                    text = prefs_path.read_text(encoding="utf-8", errors="replace")
                    m = re.search(
                        r'user_pref\("extensions\.zotero\.baseAttachmentPath",\s*"([^"]+)"\)',
                        text,
                    )
                    if m:
                        base = Path(m.group(1))
                        if is_wsl_environment():
                            self._base_attachment_path = Path(windows_to_wsl_path(str(base)))
                        else:
                            self._base_attachment_path = base
                        self._base_attachment_path_loaded = True
                        return self._base_attachment_path
                except Exception:
                    pass

        self._base_attachment_path = self._db_path.parent
        self._base_attachment_path_loaded = True
        return self._base_attachment_path

    def resolve(self, attachment_key: str, zotero_path: str) -> Path | None:
        """把 Zotero 附件路径解析为文件系统路径。

        参数：
            attachment_key：Zotero 数据库中的附件条目键。
            zotero_path：itemAttachments 表中的 path 字段。

        返回：
            解析后的文件系统 Path；无法解析时返回 None。
        """
        if not zotero_path:
            return None

        # Zotero 受管存储：'storage:filename.pdf'
        if zotero_path.startswith("storage:"):
            rel = zotero_path.split(":", 1)[1]
            parts = [p for p in rel.split("/") if p]
            return self.storage_dir / attachment_key / Path(*parts)

        # 以 URL 表示的链接文件：'file:///path/to/file.pdf'（或 file://server/share）
        if zotero_path.startswith("file://"):
            parsed = urlparse(zotero_path)
            decoded_path = unquote(parsed.path or "")
            netloc = parsed.netloc
            if not decoded_path:
                return None
            if netloc and netloc.lower() != "localhost":
                # 网络共享（UNC）：保留服务端部分。
                decoded_path = f"//{netloc}{decoded_path}"
            # Windows 下的 file:///C:/...
            if os.name == "nt" and decoded_path.startswith("/") and len(decoded_path) > 2 and decoded_path[2] == ":":
                decoded_path = decoded_path[1:]
            if is_wsl_environment():
                if netloc and netloc.lower() != "localhost":
                    return Path(windows_to_wsl_path("\\\\" + netloc + parsed.path.replace("/", "\\")))
                windows_candidate = decoded_path
                if _WSL_DRIVE_URL_RE.match(windows_candidate):
                    windows_candidate = windows_candidate[1:]
                return Path(windows_to_wsl_path(windows_candidate.replace("/", "\\")))
            return Path(decoded_path)

        # 原样保存的 Windows 盘符或 UNC 路径（Windows 上的 Zotero 被 WSL 或
        # 其他 POSIX 主机读取）在 POSIX 下不会被 os.path.isabs 判定为绝对路径，
        # 因此需要显式识别，并在适用时进行 WSL 转换。
        if _WINDOWS_DRIVE_PATH_RE.match(zotero_path) or _WINDOWS_UNC_PATH_RE.match(zotero_path):
            if is_wsl_environment():
                return Path(windows_to_wsl_path(zotero_path))
            return Path(zotero_path)

        # 以绝对路径表示的链接文件：'/Users/me/papers/file.pdf'
        if os.path.isabs(zotero_path):
            result = Path(zotero_path)
            if is_wsl_environment():
                result = Path(windows_to_wsl_path(str(result)))
            return result

        # Zotero 的 'attachments:' 相对路径——相对于 Zotero 偏好设置中配置的
        # 链接附件基准目录解析。
        if zotero_path.startswith("attachments:"):
            rel = zotero_path.split(":", 1)[1]
            parts = [p for p in rel.split("/") if p]
            base = self._read_base_attachment_path()
            if base and base.exists():
                return base / Path(*parts)
            return None

        return None
