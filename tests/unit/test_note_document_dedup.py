"""同一篇论文的两份 PDF 不应被拼接进同一个提示词。"""

from __future__ import annotations

import pytest

from zotero_cli.ai_notes.documents import _drop_duplicate_parts

_LONG = "This is a sufficiently long line of body text that survives the length filter."


class TestDropDuplicateParts:
    def test_shorter_copy_is_dropped_in_favour_of_the_fuller_one(self):
        full = "\n".join([_LONG, "Second paragraph of the complete article.", _LONG + " 3"])
        partial = "\n".join([_LONG, "Second paragraph of the complete article."])
        assert _drop_duplicate_parts([partial, full]) == [full]

    def test_distinct_documents_are_kept(self):
        a = _LONG
        b = "A completely different sentence that shares no long line with the other one."
        assert len(_drop_duplicate_parts([a, b])) == 2

    def test_paper_and_its_supplementary_are_both_kept(self):
        main = "\n".join([_LONG, "Main results discussion."])
        si = "\n".join(["Supplementary figure captions and raw curves.", _LONG + " SI"])
        assert len(_drop_duplicate_parts([main, si])) == 2

    @pytest.mark.parametrize("parts", [[], [_LONG]])
    def test_trivial_input_untouched(self, parts):
        assert _drop_duplicate_parts(parts) == parts

    def test_identical_copies_collapse_to_one(self):
        assert _drop_duplicate_parts([_LONG, _LONG]) == [_LONG]

    def test_preserves_original_order_of_distinct_parts(self):
        # 顺序本身有含义（正文在前，附录在后）；去重不得
        # 为了便于匹配而按长度排序。
        short_doc = _LONG
        long_doc = "\n".join([_LONG + " alpha", "Another body line that is long enough to count here."])
        assert _drop_duplicate_parts([short_doc, long_doc]) == [short_doc, long_doc]

    def test_short_text_without_long_lines_is_not_destroyed(self):
        # 两段都缺少 60 字符以上的长行：无法判断，因此全部保留，
        # 而不是静默删除内容。
        assert len(_drop_duplicate_parts(["短文本", "另一段短文本"])) == 2
