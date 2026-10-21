"""为 Zotero 展平 AI note 正文。

两种规范化，按此顺序应用：

1. 不使用上下标。Unicode 上下标进入笔记后只是排版符号，反而破坏阅读流，
   因此一律写成普通字符。负指数保留脱字符（``g⁻¹`` → ``g^-1``），否则
   ``g-1`` 会被读作减法；下标与电荷符号直接展平（``MnO₂`` → ``MnO2``，
   ``Zn²⁺`` → ``Zn2+``）。
2. 中英文之间使用半角空格。MinerU 输出与模型常把中文与英文术语和数字粘连
   （"层状δ-MnO2"），在 Zotero 的比例字体中看起来像错别字。

prompt 会同时要求这两点；renderer 仍强制执行，因为指令本身只有概率性效果。
两个步骤都是幂等的。
"""

from __future__ import annotations

import re

_SUPERSCRIPT = {
    "\u2070": "0",
    "\u00b9": "1",
    "\u00b2": "2",
    "\u00b3": "3",
    "\u2074": "4",
    "\u2075": "5",
    "\u2076": "6",
    "\u2077": "7",
    "\u2078": "8",
    "\u2079": "9",
    "\u207a": "+",
    "\u207b": "-",
    "\u207c": "=",
    "\u207d": "(",
    "\u207e": ")",
    "\u207f": "n",
    "\u2071": "i",
}

_SUBSCRIPT = {
    "\u2080": "0",
    "\u2081": "1",
    "\u2082": "2",
    "\u2083": "3",
    "\u2084": "4",
    "\u2085": "5",
    "\u2086": "6",
    "\u2087": "7",
    "\u2088": "8",
    "\u2089": "9",
    "\u208a": "+",
    "\u208b": "-",
    "\u208c": "=",
    "\u208d": "(",
    "\u208e": ")",
    "\u2090": "a",
    "\u2091": "e",
    "\u2092": "o",
    "\u2093": "x",
    "\u2095": "h",
    "\u2096": "k",
    "\u2097": "l",
    "\u2098": "m",
    "\u2099": "n",
    "\u209a": "p",
    "\u209b": "s",
    "\u209c": "t",
}

_SCRIPT_RUN = re.compile(f"[{''.join(_SUPERSCRIPT)}]+|[{''.join(_SUBSCRIPT)}]+")

_CJK = (
    "\u3040-\u30ff"  # 平假名、片假名
    "\u3400-\u4dbf"  # CJK 扩展 A
    "\u4e00-\u9fff"  # CJK 统一表意文字
    "\uac00-\ud7af"  # 谚文音节
    "\uf900-\ufaff"  # CJK 兼容表意文字
)

_LATIN = (
    "0-9A-Za-z"
    "\u0370-\u03ff"  # 希腊字母：δ-MnO2、α-MnO2
    "\u1f00-\u1fff"  # 希腊字母扩展
)

# 电荷符号、千分号/百分号或度数符号结束一段拉丁字符，因此空格放在其后：
# "Zn2+" + "嵌入"。
_LATIN_TAIL = _LATIN + "%\u2030\u00b0+"

# 在每个边界处零宽匹配；re.sub 会把分隔符填入这些位置。
_BOUNDARY = re.compile(rf"(?<=[{_CJK}])(?=[{_LATIN}])|(?<=[{_LATIN_TAIL}])(?=[{_CJK}])")


# 只修复包含一个被拆散数字的完整数学片段。普通正文、表格行、引用列表以及
# 含多个小数点的片段属于有歧义的科学数据，必须保持原样。
_MATH_SPAN = re.compile(r"\$([^$\n]+)\$")
_SINGLE_SPLIT_NUMBER = re.compile(r"[+-]?\d(?: *\d)*(?: *\. *\d(?: *\d)*)?")


def join_spaced_digits(text: str) -> str:
    """在不改动数字序列的前提下，重连一个无歧义的数学数字。"""

    def repair(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        if not _SINGLE_SPLIT_NUMBER.fullmatch(body):
            return match.group(0)
        return "$" + body.replace(" ", "") + "$"

    return _MATH_SPAN.sub(repair, text)


def plain_super_subscripts(text: str) -> str:
    """把 Unicode 上下标写成普通字符。"""

    def flatten(match: re.Match[str]) -> str:
        run = match.group(0)
        if run[0] in _SUBSCRIPT:
            return "".join(_SUBSCRIPT.get(char, char) for char in run)
        body = "".join(_SUPERSCRIPT.get(char, char) for char in run)
        return f"^{body}" if body.startswith("-") else body

    return _SCRIPT_RUN.sub(flatten, text)


def pad_cjk_latin(text: str) -> str:
    """返回 ``text``，并在每个中英文边界处插入半角空格。"""
    return _BOUNDARY.sub(" ", text)


def prepare(text: str) -> str:
    """为 Zotero 展平笔记字符串：去掉上下标，并在中英文之间加空格。"""
    return pad_cjk_latin(plain_super_subscripts(text))
