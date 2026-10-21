from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from zotero_cli.zotero.attachments import AttachmentResolver


class TestAttachmentResolver:
    def test_resolve_storage_path(self, tmp_path):
        """`storage:file.pdf` 解析为 `<storage_dir>/<key>/file.pdf`。"""
        storage = tmp_path / "storage"
        storage.mkdir()
        (storage / "ABC123").mkdir()
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()

        resolver = AttachmentResolver(db_path)
        result = resolver.resolve("ABC123", "storage:paper.pdf")
        assert result == storage / "ABC123" / "paper.pdf"

    def test_resolve_storage_with_subdir(self, tmp_path):
        """`storage:key/subdir/file.pdf` 能正确解析。"""
        storage = tmp_path / "storage"
        storage.mkdir()
        (storage / "ABC123").mkdir()
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()

        resolver = AttachmentResolver(db_path)
        result = resolver.resolve("ABC123", "storage:subdir/paper.pdf")
        assert result == storage / "ABC123" / "subdir" / "paper.pdf"

    def test_resolve_file_url_unix(self, tmp_path):
        """`file:///path/to/file.pdf` 解析为 `/path/to/file.pdf`。"""
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()

        resolver = AttachmentResolver(db_path)
        result = resolver.resolve("ABC123", "file:///home/user/papers/file.pdf")
        assert result == Path("/home/user/papers/file.pdf")

    def test_resolve_file_url_encoded_spaces(self, tmp_path):
        """`file:///path/to/my%20file.pdf` 解析为含空格的路径。"""
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()

        resolver = AttachmentResolver(db_path)
        result = resolver.resolve("ABC123", "file:///home/user/my%20file.pdf")
        assert result == Path("/home/user/my file.pdf")

    def test_resolve_file_url_windows_drive(self, tmp_path):
        """`file:///C:/Users/test/file.pdf` 解析为 `C:/Users/test/file.pdf`。"""
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()

        resolver = AttachmentResolver(db_path)
        # 同时替换平台边界与 Path 工厂，使该用例也能在 Python 3.10 上运行
        # （在 POSIX 主机上把 os.name 改成 nt 时，3.10 的 pathlib 会抛错）。
        with (
            patch("zotero_cli.zotero.attachments.os.name", "nt"),
            patch("zotero_cli.zotero.attachments.Path", PureWindowsPath),
            patch("zotero_cli.zotero.attachments.is_wsl_environment", return_value=False),
        ):
            result = resolver.resolve("ABC123", "file:///C:/Users/test/file.pdf")
        assert result is not None
        resolved = str(result).replace("\\", "/")
        assert resolved == "C:/Users/test/file.pdf"
        # 盘符之前必须去掉文件 URL 的前导斜杠。
        assert not resolved.startswith("/C:")

        with (
            patch("zotero_cli.zotero.attachments.os.name", "nt"),
            patch("zotero_cli.zotero.attachments.Path", PureWindowsPath),
            patch("zotero_cli.zotero.attachments.is_wsl_environment", return_value=True),
        ):
            wsl_result = resolver.resolve("ABC123", "file:///C:/Users/test/file.pdf")
        assert wsl_result is not None
        # 在 WSL 下把盘符路径转换为 /mnt/<盘符> 挂载点。
        assert str(wsl_result).replace("\\", "/") == "/mnt/c/Users/test/file.pdf"

    def test_resolve_absolute_path_unix(self, tmp_path):
        """`/absolute/path/to/file.pdf` 按原样使用。"""
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()

        resolver = AttachmentResolver(db_path)
        result = resolver.resolve("ABC123", "/home/user/papers/file.pdf")
        assert result == Path("/home/user/papers/file.pdf")

    def test_resolve_attachments_with_prefs(self, tmp_path):
        """`attachments:relative/path.pdf` 相对 prefs.js 基准路径解析。"""
        storage = tmp_path / "storage"
        storage.mkdir()
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()
        attach_base = tmp_path / "attachments_base"
        attach_base.mkdir()
        prefs = tmp_path / "prefs.js"
        prefs.write_text(f'user_pref("extensions.zotero.baseAttachmentPath", "{attach_base}");')

        resolver = AttachmentResolver(db_path)
        with patch("zotero_cli.zotero.attachments.is_wsl_environment", return_value=False):
            result = resolver.resolve("ABC123", "attachments:papers/file.pdf")
        assert result == attach_base / "papers" / "file.pdf"

    def test_resolve_attachments_fallback_to_data_dir(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir()
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()

        resolver = AttachmentResolver(db_path)
        result = resolver.resolve("ABC123", "attachments:papers/file.pdf")
        assert result == tmp_path / "papers" / "file.pdf"

    def test_resolve_attachments_does_not_scan_user_profiles(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir()
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()
        home = tmp_path / "home"
        user_profile = home / ".zotero" / "zotero" / "profile.default"
        user_profile.mkdir(parents=True)
        user_attach_base = tmp_path / "user_attachments"
        user_attach_base.mkdir()
        (user_profile / "prefs.js").write_text(
            f'user_pref("extensions.zotero.baseAttachmentPath", "{user_attach_base}");'
        )

        resolver = AttachmentResolver(db_path)
        with patch("zotero_cli.zotero.attachments.Path.home", return_value=home):
            result = resolver.resolve("ABC123", "attachments:papers/file.pdf")

        assert result == tmp_path / "papers" / "file.pdf"

    def test_resolve_empty_path(self, tmp_path):
        """空路径返回 None。"""
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()

        resolver = AttachmentResolver(db_path)
        result = resolver.resolve("ABC123", "")
        assert result is None

    def test_resolve_unknown_prefix(self, tmp_path):
        """未知前缀返回 None。"""
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()

        resolver = AttachmentResolver(db_path)
        result = resolver.resolve("ABC123", "ftp://server/file.pdf")
        assert result is None

    def test_storage_dir_property(self, tmp_path):
        """`storage_dir` 等于 `db_path.parent / 'storage'`。"""
        db_path = tmp_path / "zotero.sqlite"
        db_path.touch()

        resolver = AttachmentResolver(db_path)
        assert resolver.storage_dir == tmp_path / "storage"


# --- Windows/WSL/UNC 路径形式 ---


def test_resolve_wsl_plain_windows_drive_path(tmp_path):
    """Windows Zotero 保存的原始 `C:\\...` 路径在 WSL 上可解析。"""
    db_path = tmp_path / "zotero.sqlite"
    db_path.touch()
    resolver = AttachmentResolver(db_path)
    with patch("zotero_cli.zotero.attachments.is_wsl_environment", return_value=True):
        result = resolver.resolve("ABC123", "C:\\Users\\me\\paper.pdf")  # noqa: W605
    assert result is not None
    assert str(result).replace("\\", "/") == "/mnt/c/Users/me/paper.pdf"


def test_resolve_wsl_file_url_drive_without_nt_patch(tmp_path):
    """在 POSIX WSL 主机上 `file:///C:/...` 转换为 `/mnt/c/...`。"""
    db_path = tmp_path / "zotero.sqlite"
    db_path.touch()
    resolver = AttachmentResolver(db_path)
    with patch("zotero_cli.zotero.attachments.is_wsl_environment", return_value=True):
        result = resolver.resolve("ABC123", "file:///C:/Users/me/paper.pdf")
    assert result is not None
    assert str(result).replace("\\", "/") == "/mnt/c/Users/me/paper.pdf"


def test_resolve_unc_file_url_keeps_server_component(tmp_path):
    db_path = tmp_path / "zotero.sqlite"
    db_path.touch()
    resolver = AttachmentResolver(db_path)
    with patch("zotero_cli.zotero.attachments.is_wsl_environment", return_value=False):
        result = resolver.resolve("ABC123", "file://server/share/paper.pdf")
    assert result is not None
    assert str(result).replace("\\", "/") == "//server/share/paper.pdf"


def test_resolve_windows_path_verbatim_when_not_wsl(tmp_path):
    db_path = tmp_path / "zotero.sqlite"
    db_path.touch()
    resolver = AttachmentResolver(db_path)
    with patch("zotero_cli.zotero.attachments.is_wsl_environment", return_value=False):
        result = resolver.resolve("ABC123", "C:\\Users\\me\\paper.pdf")
    assert result is not None
    assert str(result).replace("\\", "/") == "C:/Users/me/paper.pdf"
