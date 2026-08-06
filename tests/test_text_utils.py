"""Regression tests for script-aware message sizing.

The chat pipeline uses these to decide whether to trim history, skip RAG, and
shrink the generation budget. The previous implementation measured with
`len(text) < 50 and text.count(" ") < 10`, which is always "simple" for
Chinese -- Chinese has no spaces and packs a full question into far fewer
characters. That silently skipped web search and truncated answers for the
majority of this product's users.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from text_utils import is_greeting, is_simple_query, message_units


class TestMessageUnits:
    def test_empty(self):
        assert message_units("") == 0
        assert message_units("   ") == 0
        assert message_units(None) == 0

    def test_latin_counts_words_not_characters(self):
        assert message_units("what is the SST rate") == 5

    def test_cjk_counts_characters_because_there_are_no_spaces(self):
        # Six ideographs, zero spaces. The old space-based heuristic scored 0.
        assert message_units("马来西亚税率") == 6

    def test_mixed_script(self):
        # 6 CJK chars (马来西亚 + 税率) + 2 latin tokens ("SST", "2026")
        assert message_units("马来西亚 SST 2026 税率") == 8

    def test_long_chinese_question_is_not_trivially_small(self):
        msg = "请帮我分析一下马来西亚中小企业在2026年申请政府数字化补助金的完整流程、所需文件和资格条件"
        assert message_units(msg) > 30


class TestIsGreeting:
    @pytest.mark.parametrize(
        "text",
        ["hi", "Hello", "hey!", "thanks", "Thank you.", "ok", "bye",
         "你好", "您好", "谢谢", "好的", "再见",
         "hai", "terima kasih", "selamat pagi"],
    )
    def test_pleasantries(self, text):
        assert is_greeting(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "什么是SST?",                       # 8 chars, but a real question
            "hello, what is the SST rate?",     # starts with a greeting
            "谢谢，那SST税率是多少？",            # starts with a greeting
            "how do I register a Sdn Bhd?",
            "马来西亚2026年SST税率是多少？",
        ],
    )
    def test_real_questions_are_not_greetings(self, text):
        assert is_greeting(text) is False


class TestIsSimpleQuery:
    @pytest.mark.parametrize(
        "text",
        ["hi", "谢谢你", "什么是SST?", "what is the current SST rate in Malaysia?"],
    )
    def test_short_questions_are_simple(self, text):
        assert is_simple_query(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            # The regression case: substantial Chinese question that the old
            # `len < 50 and count(" ") < 10` rule classified as simple, which
            # skipped web search entirely.
            "请帮我分析一下马来西亚中小企业在2026年申请政府数字化补助金的完整流程、所需文件和资格条件",
            "Please analyse the eligibility criteria, required documents and full "
            "application timeline for the Malaysian MSME digitalisation grant in 2026",
        ],
    )
    def test_substantial_questions_are_not_simple(self, text):
        assert is_simple_query(text) is False

    def test_old_heuristic_would_have_failed_the_chinese_case(self):
        """Pins the exact bug this module was written to fix."""
        msg = "请帮我分析一下马来西亚中小企业在2026年申请政府数字化补助金的完整流程、所需文件和资格条件"
        old_is_simple = len(msg.strip()) < 50 and msg.count(" ") < 10
        assert old_is_simple is True      # what the old rule said
        assert is_simple_query(msg) is False  # what it should say
