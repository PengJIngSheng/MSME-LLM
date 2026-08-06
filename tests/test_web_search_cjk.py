"""Regression tests for Chinese handling and provider adaptation in web search.

Two defect classes are pinned here:

1. CJK word boundaries. Python's `\\w` includes CJK characters, so a pattern
   like r"\\b多少\\b" never matches inside 价格多少 -- there is no boundary
   between two ideographs. Every Chinese term in `_secondary_query` used to sit
   inside a \\b(...)\\b group and was therefore dead code, silently collapsing
   Chinese questions to the generic "+ Malaysia" fallback.

2. Search-operator support. Brave parses `site:` / `OR`; Tavily treats them as
   literal text. Planning operator queries for a Tavily-only deployment wastes
   an API call and pollutes the results.
"""

import os
import re
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "Model Networking"))

from web_search import _ANGLE_PATTERNS, _query_plan, _secondary_query


class TestCJKWordBoundaries:
    def test_python_treats_cjk_as_word_characters(self):
        """The property that made the original patterns dead."""
        assert re.search(r"\b多少\b", "价格多少") is None
        assert re.search(r"(?:多少)", "价格多少") is not None

    def test_no_cjk_term_is_wrapped_in_a_word_boundary(self):
        for latin_pat, cjk_pat, _ in _ANGLE_PATTERNS:
            assert "\\b" not in cjk_pat, f"CJK pattern {cjk_pat!r} uses \\b and cannot match"
            assert not re.search(r"[一-鿿]", latin_pat), (
                f"Latin pattern {latin_pat!r} contains CJK, which \\b would disable"
            )

    @pytest.mark.parametrize(
        "question,expected_fragment",
        [
            ("马来西亚公司注册需要什么文件", "requirements documents"),
            ("最新的中小企业补助政策是什么", "official announcement"),
            ("请问贷款的资助条件有哪些", "eligibility criteria apply"),
        ],
    )
    def test_chinese_questions_get_a_topical_angle(self, question, expected_fragment):
        assert expected_fragment in _secondary_query(question)

    def test_chinese_price_question_gets_the_comparison_angle(self):
        result = _secondary_query("ron95 汽油价格多少钱")
        assert "comparison" in result
        # Previously fell through to the generic fallback instead.
        assert result != "ron95 汽油价格多少钱 Malaysia"

    def test_english_equivalents_still_work(self):
        assert "requirements documents" in _secondary_query("how to register a company in Malaysia")
        assert "official announcement" in _secondary_query("latest MSME grant news")


class TestYearIsComputed:
    def test_comparison_angle_uses_the_current_year(self):
        year = datetime.now(timezone.utc).year
        result = _secondary_query("what is the current exchange rate")
        assert str(year) in result

    def test_no_hardcoded_year_in_source(self):
        """A literal year silently rots and steers search at stale data."""
        source = open(os.path.join(_ROOT, "Model Networking", "web_search.py"), encoding="utf-8").read()
        assert 'comparison 2025' not in source


class TestProviderOperatorSupport:
    _POLICY_Q = "malaysia company registration requirements"

    def test_brave_plan_may_use_site_operators(self):
        plan = _query_plan(self._POLICY_Q, "Malaysia", supports_site_operator=True, max_queries=5)
        assert any("site:" in q for q in plan)

    def test_tavily_plan_never_uses_site_operators(self):
        plan = _query_plan(self._POLICY_Q, "Malaysia", supports_site_operator=False, max_queries=5)
        assert not any("site:" in q for q in plan)
        assert not any(" OR " in q for q in plan)

    def test_tavily_plan_keeps_an_official_source_angle(self):
        plan = _query_plan(self._POLICY_Q, "Malaysia", supports_site_operator=False, max_queries=5)
        assert any("gov.my" in q for q in plan), "should still steer at official sources"

    def test_max_queries_is_honoured(self):
        for limit in (1, 2, 3):
            assert len(_query_plan(self._POLICY_Q, "Malaysia", max_queries=limit)) <= limit

    def test_empty_question_yields_no_queries(self):
        assert _query_plan("", "Malaysia") == []
