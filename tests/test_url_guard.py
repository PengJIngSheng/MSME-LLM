"""SSRF and URL-validation tests for the web-fetch layer.

The assistant fetches URLs chosen by a search provider, so a poisoned or
crafted result must not be able to make the backend read internal services and
hand the response to the model. These tests pin the deny-by-default rules.
"""

import os
import sys
from unittest import mock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "Model Networking"))

from url_guard import (
    BLOCKED_PORTS,
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    check_url,
    is_safe_url,
)


class TestScheme:
    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "data:text/html,<script>alert(1)</script>",
        "javascript:alert(1)",
    ])
    def test_non_http_schemes_refused(self, url):
        verdict = check_url(url)
        assert not verdict
        assert "scheme" in verdict.reason or "hostname" in verdict.reason

    def test_https_allowed(self):
        assert is_safe_url("https://example.com/page")


class TestPrivateAddresses:
    @pytest.mark.parametrize("url,label", [
        ("http://127.0.0.1:8000/api/history", "loopback"),
        ("http://localhost/", "localhost name"),
        ("http://[::1]/", "ipv6 loopback"),
        ("http://10.0.0.5/", "private 10/8"),
        ("http://172.16.4.4/", "private 172.16/12"),
        ("http://192.168.1.1/", "private 192.168/16"),
        ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
        ("http://0.0.0.0/", "unspecified"),
    ])
    def test_internal_targets_refused(self, url, label):
        assert not check_url(url), f"{label} should be refused"

    def test_public_ip_allowed(self):
        assert is_safe_url("http://93.184.216.34/", resolve_dns=False)

    def test_hostname_resolving_to_private_ip_is_refused(self):
        """DNS rebinding: a public name pointing at an internal address."""
        with mock.patch("url_guard._resolve_all") as resolver:
            import ipaddress
            resolver.return_value = [ipaddress.ip_address("192.168.1.50")]
            verdict = check_url("https://evil.example.com/")
        assert not verdict
        assert "non-public" in verdict.reason

    def test_mixed_public_and_private_resolution_is_refused(self):
        """One private address among the answers is enough to refuse."""
        with mock.patch("url_guard._resolve_all") as resolver:
            import ipaddress
            resolver.return_value = [
                ipaddress.ip_address("93.184.216.34"),
                ipaddress.ip_address("10.1.2.3"),
            ]
            assert not check_url("https://mixed.example.com/")


class TestPorts:
    def test_ollama_port_is_blocked(self):
        """The model API must not be reachable through the fetcher."""
        assert 11434 in BLOCKED_PORTS
        assert not check_url("http://example.com:11434/api/chat", resolve_dns=False)

    @pytest.mark.parametrize("port", [22, 3306, 5432, 6379, 27017, 2375])
    def test_internal_service_ports_blocked(self, port):
        assert not check_url(f"http://example.com:{port}/", resolve_dns=False)

    def test_normal_web_ports_allowed(self):
        assert is_safe_url("https://example.com:8443/", resolve_dns=False)


class TestCredentialsAndParsing:
    def test_embedded_credentials_refused(self):
        verdict = check_url("https://user:pass@example.com/")
        assert not verdict
        assert "credential" in verdict.reason

    @pytest.mark.parametrize("url", ["", "   ", "not a url", "https://"])
    def test_malformed_input_refused(self, url):
        assert not check_url(url)

    def test_trailing_dot_hostname_normalised(self):
        """`example.com.` and `example.com` must be treated identically."""
        assert not check_url("https://localhost./", resolve_dns=False)


class TestAllowAndBlockLists:
    def test_blocklist_matches_subdomains(self):
        assert not check_url("https://ads.tracker.com/x",
                             blocklist=["tracker.com"], resolve_dns=False)

    def test_blocklist_does_not_match_unrelated_suffix(self):
        assert is_safe_url("https://nottracker.com/x",
                           blocklist=["tracker.com"], resolve_dns=False)

    def test_allowlist_permits_listed_domain(self):
        assert is_safe_url("https://www.gov.my/page",
                           allowlist=["gov.my"], resolve_dns=False)

    def test_allowlist_refuses_everything_else(self):
        assert not check_url("https://example.com/",
                             allowlist=["gov.my"], resolve_dns=False)


class TestLimits:
    def test_response_size_cap_is_bounded(self):
        assert 0 < MAX_RESPONSE_BYTES <= 20 * 1024 * 1024

    def test_redirect_cap_is_small(self):
        assert 0 < MAX_REDIRECTS <= 5
