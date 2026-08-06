"""Regulatory questions must be grounded, not answered from model memory.

Production testing produced this answer to "which SSM and LHDN deadlines affect
the timing":

    These bodies primarily concern compliance, which helps maintain good
    standing but doesn't directly improve cash flow...

No dates at all. Three separate defects combined to cause it:

  1. `needs_web_search` had no deadline/filing vocabulary, so the question
     never triggered a search.
  2. `is_simple_query` classified short regulatory questions as trivial, and
     server.py used that to skip knowledge retrieval entirely -- even though
     the knowledge base holds the LHDN material.
  3. With nothing retrieved, the prompt let the model substitute generic
     compliance advice for the figures it was asked for.

A wrong filing deadline costs an MSME a penalty, so "I could not verify this"
is the better failure mode. These tests pin all three fixes.
"""

import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "Model Networking"))

import text_utils
from web_search import needs_web_search


REGULATORY_QUESTIONS = [
    "When is the SST filing deadline in Malaysia?",
    "What are the LHDN filing deadlines for a sole proprietor?",
    "What is the penalty for late CP204 submission?",
    "How much is the SSM annual return fee?",
    "When must I file my annual return with SSM?",
    "Bila tarikh akhir hantar borang cukai?",
    "What is the SST registration threshold?",
    "Do I qualify for SST exemption?",
]

ORDINARY_QUESTIONS = [
    "I had a question about my business",
    "That's fine, thanks",
    "Can you explain what a balance sheet is?",
    "Help me write a marketing plan for my bakery",
    "How do I motivate my staff?",
    "What does gross margin mean?",
    "Give me three ideas for a new product",
    "Explain how taxes work in general",
]


class TestSearchTrigger:
    @pytest.mark.parametrize("q", REGULATORY_QUESTIONS)
    def test_compliance_timing_triggers_search(self, q):
        assert needs_web_search(q), f"{q!r} must reach live sources"

    @pytest.mark.parametrize("q", ORDINARY_QUESTIONS)
    def test_ordinary_conversation_does_not_search(self, q):
        assert not needs_web_search(q), f"{q!r} must not spend a web search"

    def test_ambiguous_everyday_words_excluded(self):
        """Malay 'had' and bare 'fine' are everyday English words."""
        assert not needs_web_search("I had a meeting yesterday")
        assert not needs_web_search("The margins are fine for now")


class TestRegulatoryDetection:
    @pytest.mark.parametrize("q", REGULATORY_QUESTIONS)
    def test_detected(self, q):
        assert text_utils.is_regulatory_query(q), f"{q!r} should be regulatory"

    @pytest.mark.parametrize("q", ORDINARY_QUESTIONS)
    def test_not_over_triggered(self, q):
        assert not text_utils.is_regulatory_query(q), f"{q!r} is not regulatory"

    def test_plural_forms_match(self):
        """`\\bdeadline\\b` does not match inside 'deadlines' -- the original miss."""
        assert text_utils.is_regulatory_query("What are the LHDN deadlines?")
        assert text_utils.is_regulatory_query("What are the SST rates?")
        assert text_utils.is_regulatory_query("What penalties apply under LHDN?")

    def test_authority_alone_is_not_enough(self):
        """Naming a body without asking for a figure is a concept question."""
        assert not text_utils.is_regulatory_query("What does LHDN stand for?")
        assert not text_utils.is_regulatory_query("Who runs SSM?")

    def test_specific_alone_is_not_enough(self):
        assert not text_utils.is_regulatory_query("What is the deadline for my project?")
        assert not text_utils.is_regulatory_query("How much should I charge?")


class TestRetrievalIsNotSkipped:
    """The heuristic must not switch retrieval off for regulatory questions."""

    def test_short_regulatory_question_looks_simple_but_must_retrieve(self):
        q = "When is the SST deadline?"
        assert text_utils.is_simple_query(q), (
            "precondition: this is short enough to look trivial"
        )
        assert text_utils.is_regulatory_query(q), (
            "so the regulatory check is what keeps retrieval on"
        )

    def test_server_skip_condition_exempts_regulatory(self):
        """Pin the actual condition in server.py, not a restatement of it."""
        source = open(os.path.join(_ROOT, "server.py"), encoding="utf-8").read()
        match = re.search(r"_skip_retrieval = \((.*?)\n    \)", source, re.S)
        assert match, "_skip_retrieval assignment not found"
        body = match.group(1)
        assert "_is_regulatory_query" in body, (
            "_skip_retrieval no longer exempts regulatory questions; short "
            "compliance questions would silently lose the knowledge base again"
        )
        assert "_is_simple_query and not _is_regulatory_query" in body


class TestRegulatoryAccuracyRule:
    """The prompt rule must apply to every regulatory turn, not only empty ones.

    An earlier version gated on `not knowledge_injection`. Retrieval almost
    always returns something -- just not always the answer -- so that version
    was dead code. Measured on this knowledge base: "When is the SST filing
    deadline" retrieves 5KB containing neither "SST" nor any date.
    """

    @pytest.fixture(scope="class")
    def source(self):
        return open(os.path.join(_ROOT, "server.py"), encoding="utf-8").read()

    def test_rule_exists(self, source):
        assert "REGULATORY ACCURACY RULE" in source

    def test_rule_is_not_gated_on_empty_retrieval(self, source):
        assert "if _is_regulatory_query and not knowledge_injection:" not in source, (
            "gating on empty retrieval makes this rule unreachable"
        )
        assert "if _is_regulatory_query:" in source

    def test_rule_binds_figures_to_provided_sources(self, source):
        rule = source[source.index("REGULATORY ACCURACY RULE"):][:1600]
        for requirement in [
            "ONLY if it appears in the sources",
            "say so plainly",
            "Never fill the gap with a remembered or estimated figure",
            "Never substitute general compliance advice",
        ]:
            assert requirement in rule, f"rule lost its {requirement!r} clause"


class TestRelevanceThreshold:
    """A threshold that admits everything defeats grounding entirely."""

    def test_threshold_is_calibrated_not_permissive(self):
        from config_loader import cfg
        value = cfg.knowledge_rag_score_threshold
        # Measured on this knowledge base with nomic-embed-text: on-topic
        # questions match at 0.21-0.31 cosine distance, off-topic at 0.44-0.49.
        assert 0.31 < value < 0.44, (
            f"threshold {value} sits outside the measured separation band; "
            f"above 0.44 every query retrieves something and grounding is fake"
        )
