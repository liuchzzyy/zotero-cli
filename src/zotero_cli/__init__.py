"""zotero-cli：面向 AI 代理的 Zotero CLI。"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("zotero-cli")
except PackageNotFoundError:  # 在未安装的源码树中运行
    __version__ = "0.0.0+unknown"
