"""The product ships English and Bahasa Melayu only.

Chinese was retired from the UI, the locale files, and the prompt templates.
These tests pin the two properties that removal has to guarantee:

  1. No supported surface can produce a Chinese reply language.
  2. A user whose browser has `pepperLang=zh` stored from before the change
     lands on English instead of requesting a locale file that no longer
     exists.

Language *detection* is deliberately left intact -- a user can still type
Chinese, and the sizing helpers must keep measuring CJK correctly. What is
clamped is the language the assistant answers in.
"""

import importlib.util
import json
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from text_utils import (
    DEFAULT_REPLY_LANGUAGE,
    SUPPORTED_REPLY_LANGUAGES,
    resolve_reply_lang_code,
    resolve_reply_language,
)


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TestReplyLanguageResolution:
    def test_supported_set_is_english_and_malay(self):
        assert set(SUPPORTED_REPLY_LANGUAGES) == {"English", "Malay"}
        assert DEFAULT_REPLY_LANGUAGE == "English"

    @pytest.mark.parametrize("detected", ["Chinese", "Japanese", "Korean", "French", "", None])
    def test_unsupported_languages_fall_back_to_english(self, detected):
        assert resolve_reply_language(detected) == "English"

    @pytest.mark.parametrize("detected", ["English", "Malay"])
    def test_supported_languages_pass_through(self, detected):
        assert resolve_reply_language(detected) == detected

    @pytest.mark.parametrize("code,expected", [
        ("zh", "en"), ("ja", "en"), ("ko", "en"), ("ar", "en"), ("th", "en"),
        ("es", "en"), ("fr", "en"), ("de", "en"), ("", "en"), (None, "en"),
        ("en", "en"), ("ms", "ms"), ("MS", "ms"),
    ])
    def test_code_resolution(self, code, expected):
        assert resolve_reply_lang_code(code) == expected


class TestPdfAgentLanguage:
    @pytest.fixture(scope="class")
    def pdf_agent(self):
        return _load("pdf_agent_lang_test", "AI agent/PDF Agent/pdf_agent.py")

    @pytest.mark.parametrize("text", [
        "马来西亚公司注册需要什么文件",
        "こんにちは、これを要約してください",
        "안녕하세요",
        "Bonjour, pouvez-vous resumer ce document",
    ])
    def test_unsupported_input_answers_in_english(self, pdf_agent, text):
        assert pdf_agent._detect_reply_lang(text) == "en"

    def test_malay_is_preserved(self, pdf_agent):
        assert pdf_agent._detect_reply_lang(
            "Boleh tolong saya buat laporan ini dengan segera"
        ) == "ms"

    def test_detection_itself_still_recognises_chinese(self, pdf_agent):
        """Detection is intact; only the reply language is clamped."""
        assert pdf_agent._detect_raw_lang("马来西亚公司注册") == "zh"


class TestGoogleAgentLanguage:
    @pytest.fixture(scope="class")
    def google_agent(self):
        return _load("google_agent_lang_test", "AI agent/google_agent.py")

    @pytest.mark.parametrize("text", ["把这个存到云盘", "save this to drive", "こんにちは"])
    def test_never_returns_zh(self, google_agent, text):
        assert google_agent._detect_lang(text) in ("en", "ms")

    def test_malay_detected(self, google_agent):
        assert google_agent._detect_lang("tolong hantar fail ini kepada saya") == "ms"


class TestFrontendSurface:
    def test_chinese_locale_file_is_gone(self):
        assert not os.path.exists(os.path.join(_ROOT, "static/locales/zh.json"))

    def test_english_and_malay_locales_remain_and_are_valid(self):
        for name in ("en", "ms"):
            path = os.path.join(_ROOT, f"static/locales/{name}.json")
            assert os.path.exists(path), f"{name}.json missing"
            with open(path, encoding="utf-8") as fh:
                assert json.load(fh), f"{name}.json is empty"

    def test_no_chinese_language_button_in_any_page(self):
        for page in ("index.html", "login.html", "verify.html", "register.html"):
            path = os.path.join(_ROOT, "static", page)
            if not os.path.exists(path):
                continue
            html = open(path, encoding="utf-8").read()
            assert 'data-lang="zh"' not in html, f"{page} still offers Chinese"

    def test_script_declares_only_english_and_malay(self):
        js = open(os.path.join(_ROOT, "static/script.js"), encoding="utf-8").read()
        assert "const SUPPORTED_LANGS = ['en', 'ms'];" in js
        assert "supportedLngs: SUPPORTED_LANGS," in js
        # The language cycle button must not be able to land on Chinese.
        assert "languageLabels = { en: 'EN', ms: 'BM' }" in js

    def test_stored_chinese_preference_is_migrated(self):
        """getPreferredLanguage must rewrite a stale 'zh' to the default."""
        js = open(os.path.join(_ROOT, "static/script.js"), encoding="utf-8").read()
        fn = re.search(r"function getPreferredLanguage\(\)\s*\{(.*?)\n\}", js, re.S)
        assert fn, "getPreferredLanguage not found"
        body = fn.group(1)
        assert "normalizeLang(stored)" in body, "stored value is not normalised"
        assert "localStorage.setItem('pepperLang', resolved)" in body, (
            "a stale 'zh' is never written back, so it would be re-read forever"
        )


class TestBackendPrompt:
    def test_prompt_does_not_offer_chinese(self):
        server = open(os.path.join(_ROOT, "server.py"), encoding="utf-8").read()
        assert "Only use Chinese" not in server
        assert server.count("Reply only in English or Malay (Bahasa Malaysia).") == 2
