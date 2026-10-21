"""终端文本清洗：移除 ANSI 转义序列与控制字符。

这是跨层能力：命令层渲染、元数据命令与输出契约都需要它，因此放在
`output/` 而不是 `cli/`，避免领域包反向依赖命令层。
"""

from __future__ import annotations

import re

# CSI/OSC/以 OSC 结束的转义序列，以及单字符 ESC 序列。
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-_])")
# 除制表符/换行外的 C0 控制符（因此孤立的 CR 也会被剥离）、DEL 与 C1 控制符。
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def sanitize_text(value: object) -> str:
    """返回可安全打印到终端的文本：不含 ANSI 转义序列/控制字符。

    保留制表符和换行，使多段笔记维持原有排版；回车符会被剥离（孤立的 CR
    会覆盖当前终端行）。仅用于人类可读输出；JSON 载荷保持不变。
    """
    if value is None:
        return ""
    return _CONTROL_CHAR_RE.sub("", _ANSI_ESCAPE_RE.sub("", str(value)))
