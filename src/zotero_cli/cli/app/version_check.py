"""检查 PyPI 上是否有更新的 zotero-cli 版本。

加固规则（M-22/L-10 跟进）：
* 除非调用方主动选择，否则不发起网络请求也不写缓存（CLI 仅在交互式 TTY 模式下
  调用本模块，且 ZOT_NO_UPDATE_CHECK=1 可完全禁用它）；
* 缓存路径通过 config.state_dir() 惰性解析，从而遵循状态根目录的变更，
  并且测试绝不会写入代码检出目录；
* 更新提示仅限交互模式：管道/JSON/--no-interaction 运行会完全跳过检查，
  因此代理程序和脚本不会承担其延迟；
* 所有失败情形均不致命：版本检查绝不能导致命令失败。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from zotero_cli import config as _config

CACHE_FILENAME = "version-check.json"
_CHECK_INTERVAL = 86400  # 24 小时
_PYPI_URL = "https://pypi.org/pypi/zotero-cli/json"
_TIMEOUT = 3  # 秒


def update_check_disabled() -> bool:
    """当用户选择退出 PyPI 更新检查时返回 True。"""
    value = os.environ.get("ZOT_NO_UPDATE_CHECK", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _cache_file() -> Path:
    """惰性解析缓存文件路径，以便尊重可迁移的状态根目录。"""
    return _config.state_dir() / CACHE_FILENAME


def _parse_version(v: str) -> tuple[int, ...]:
    """将版本字符串解析为元组以便比较。"""
    return tuple(int(x) for x in v.strip().split(".") if x.isdigit())


def upgrade_command(executable: str | None = None) -> str:
    """返回与当前安装方式相匹配的升级命令。

    通过检查 sys.executable 识别 uv tool 与 pipx 安装；否则回退到
    普通的 pip install -U（适用于 pip/conda/系统安装）。
    """
    exe = (executable if executable is not None else sys.executable).replace("\\", "/")
    if "/uv/tools/" in exe:
        return "uv tool upgrade zotero-cli"
    if "/pipx/venvs/" in exe:
        return "pipx upgrade zotero-cli"
    return "pip install -U zotero-cli"


def check_for_update(current_version: str) -> str | None:
    """检查 PyPI 上是否有更新的版本。

    若有更新则返回最新版本字符串，否则返回 None。使用基于文件的缓存，
    避免每次调用都访问 PyPI。设置 ZOT_NO_UPDATE_CHECK 时完全禁用。绝不抛出异常。
    """
    if update_check_disabled():
        return None
    try:
        cache_file = _cache_file()
        cache_file.parent.mkdir(parents=True, exist_ok=True)

        # 先查缓存
        if cache_file.exists():
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - cache.get("checked_at", 0) < _CHECK_INTERVAL:
                latest = cache.get("latest_version", "")
                if latest and _parse_version(latest) > _parse_version(current_version):
                    return str(latest)
                return None

        # 从 PyPI 获取
        with urlopen(_PYPI_URL, timeout=_TIMEOUT) as resp:  # noqa: S310
            data = json.loads(resp.read())
        latest = data["info"]["version"]

        # 更新缓存（尽力而为；只读状态目录不得破坏 CLI）
        try:
            cache_file.write_text(
                json.dumps({"latest_version": latest, "checked_at": time.time()}),
                encoding="utf-8",
            )
        except OSError:
            pass

        if _parse_version(latest) > _parse_version(current_version):
            return str(latest)
        return None
    except Exception:
        return None
