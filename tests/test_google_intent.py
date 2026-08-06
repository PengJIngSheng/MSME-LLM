"""Regression tests for Google Workspace intent detection.

`is_google_request` decides whether to hijack a chat turn away from the
conversational model and into the Workspace tool pipeline. False positives are
what this suite guards: the previous substring-based matcher fired on ordinary
business questions, most severely in Chinese, where the single-character verb
"发" matches inside 开发/发展/发现 and "内容" was a target noun.

The module is loaded via importlib because its parent directory is named
"AI agent" -- a space makes it an invalid Python package name.
"""

import importlib.util
import os
import sys
from unittest import mock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


@pytest.fixture(scope="module")
def google_agent():
    """Load google_agent, then replace its Workspace tools handle with a stub.

    google_agent.py loads google_workspace_tools by file path rather than by
    import, so it cannot be intercepted through sys.modules. Executing it is
    harmless here (pymongo connects lazily), and swapping `_gwt` afterwards
    keeps these tests off the network and off the database.
    """
    path = os.path.join(_ROOT, "AI agent", "google_agent.py")
    spec = importlib.util.spec_from_file_location("google_agent_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    stub = mock.MagicMock()
    stub.users_col.find_one.return_value = None
    module._gwt = stub
    return module


# ── Must still be intercepted ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "msg",
    [
        "save this to google drive",
        "email this report via Gmail",
        "create a Google Doc from this",
        "把这个存到谷歌云盘",
        "send the summary to my email",
        "upload the spreadsheet",
        "schedule a meeting invite for Tuesday",
        "帮我发送邮件给客户",
        "把分析结果上传到云端硬盘",
        "创建一个电子表格",
        "安排一个视频会议",
        "列出我的日历",
    ],
)
def test_genuine_workspace_requests_are_intercepted(google_agent, msg):
    assert google_agent.is_google_request(msg) is True


# ── Must NOT be intercepted ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "msg",
    [
        # The headline regression: 开发 contains the old single-char verb 发,
        # 内容 was a target noun.
        "帮我开发一个内容营销方案",
        "分析一下公司的发展趋势",
        "这个季度的营收发生了什么变化",
        "请解释一下什么是资产负债表",
        "帮我写一份商业计划书的正文内容",
        # "balance sheet" tripped the bare "sheet" target.
        "explain how to read a balance sheet",
        "write content for my marketing plan",
        "create a framework for evaluating suppliers",
        "add more detail to that analysis",
        "check the numbers in the previous message",
        "what is the current SST rate",
        "list the steps to register an Sdn Bhd",
    ],
)
def test_ordinary_business_questions_are_not_intercepted(google_agent, msg):
    assert google_agent.is_google_request(msg) is False


# ── Structural guarantees ────────────────────────────────────────────────────

def test_no_single_character_chinese_verbs(google_agent):
    """Single CJK characters are substrings of common unrelated words."""
    for verb in google_agent._VERBS_CJK:
        assert len(verb) >= 2, f"{verb!r} is too short to disambiguate"


def test_no_single_character_chinese_targets(google_agent):
    for target in google_agent._TARGETS_CJK:
        assert len(target) >= 2, f"{target!r} is too short to disambiguate"


def test_latin_terms_match_on_word_boundaries(google_agent):
    """'address' must not satisfy the 'add' verb, nor 'assheet' the targets."""
    assert google_agent._has_verb("what is the address", "what is the address") is False
    assert google_agent._has_target("balance sheet", "balance sheet") is False


def test_empty_and_blank_input(google_agent):
    assert google_agent.is_google_request("") is False
    assert google_agent.is_google_request("   ") is False
    assert google_agent.is_google_request(None) is False


def test_gmail_confirmation_markers_always_intercept(google_agent):
    assert google_agent.is_google_request("[CONFIRM_GMAIL_SEND]") is True
    assert google_agent.is_google_request("[CANCEL_GMAIL_SEND]") is True


def test_pending_gmail_draft_locks_the_user_into_the_flow(google_agent):
    google_agent._gwt.users_col.find_one.return_value = {"pending_gmail": {"to": "x@y.z"}}
    try:
        assert google_agent.is_google_request("make it shorter", user_id="u1") is True
    finally:
        google_agent._gwt.users_col.find_one.return_value = None
