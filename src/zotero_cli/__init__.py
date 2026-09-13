"""zotero-cli: Zotero CLI for AI agents."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("zotero-cli")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+unknown"

