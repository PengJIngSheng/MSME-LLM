"""Safety checks for URLs the assistant is about to fetch.

The web-search layer follows links chosen by a search provider and, indirectly,
by whatever a user asked about. That makes it a server-side request forgery
(SSRF) surface: without validation, a crafted or poisoned result can make the
backend fetch `http://169.254.169.254/` (cloud instance metadata),
`http://127.0.0.1:11434/` (the local Ollama API), or a Docker-internal
database, and hand the response to the model.

These checks are deliberately deny-by-default: only http/https, only public
IP addresses, no credentials in the URL, and a bounded response size.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlparse

# Only these schemes are ever fetched. Blocks file://, gopher://, ftp://,
# data:, and the redirect tricks built on them.
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Ports that are almost never legitimate web content but are common
# internal-service targets.
BLOCKED_PORTS = frozenset({
    22, 23, 25, 445, 465, 587,      # ssh / telnet / smtp / smb
    3306, 5432, 6379, 27017, 9200,  # mysql / postgres / redis / mongo / elastic
    11434,                          # ollama — the model API itself
    2375, 2376,                     # docker daemon
})

# Hostnames that resolve to infrastructure rather than content.
BLOCKED_HOSTNAMES = frozenset({
    "localhost", "metadata.google.internal", "metadata",
    "instance-data", "169.254.169.254",
})

MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class UrlVerdict:
    allowed: bool
    url: str
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def _ip_is_public(ip: ipaddress._BaseAddress) -> bool:
    """Reject every address range that is not routable public internet."""
    return not (
        ip.is_private          # 10/8, 172.16/12, 192.168/16, fc00::/7
        or ip.is_loopback      # 127/8, ::1
        or ip.is_link_local    # 169.254/16 — cloud metadata lives here
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_all(hostname: str) -> list:
    """Every address a hostname resolves to, v4 and v6.

    All of them must be public: a name that returns one public and one private
    address could still be used to reach an internal host.
    """
    infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def check_url(
    raw_url: str,
    *,
    allowlist: Optional[Iterable[str]] = None,
    blocklist: Optional[Iterable[str]] = None,
    resolve_dns: bool = True,
) -> UrlVerdict:
    """Decide whether `raw_url` is safe for the server to fetch.

    allowlist: if given, the host must match one of these domains (suffix match).
    blocklist: hosts matching any of these are refused.
    resolve_dns: resolve the hostname and require every address to be public.
        Set False only when a caller has already pinned the address.
    """
    url = (raw_url or "").strip()
    if not url:
        return UrlVerdict(False, url, "empty URL")

    try:
        parsed = urlparse(url)
    except Exception as exc:
        return UrlVerdict(False, url, f"unparseable URL: {exc}")

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return UrlVerdict(False, url, f"scheme {parsed.scheme!r} is not allowed")

    # user:password@host in a fetched URL is either a credential leak or an
    # attempt to confuse host parsing.
    if parsed.username or parsed.password:
        return UrlVerdict(False, url, "credentials embedded in URL")

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return UrlVerdict(False, url, "no hostname")

    if hostname in BLOCKED_HOSTNAMES:
        return UrlVerdict(False, url, f"blocked hostname {hostname!r}")

    try:
        port = parsed.port
    except ValueError:
        return UrlVerdict(False, url, "invalid port")
    if port is not None and port in BLOCKED_PORTS:
        return UrlVerdict(False, url, f"blocked port {port}")

    if blocklist and any(hostname == d or hostname.endswith(f".{d}") for d in blocklist):
        return UrlVerdict(False, url, f"host {hostname!r} is blocklisted")

    if allowlist and not any(hostname == d or hostname.endswith(f".{d}") for d in allowlist):
        return UrlVerdict(False, url, f"host {hostname!r} is not on the allowlist")

    # A literal IP in the URL is checked directly; a name is resolved first.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if not _ip_is_public(literal_ip):
            return UrlVerdict(False, url, f"non-public address {literal_ip}")
        return UrlVerdict(True, url)

    if resolve_dns:
        try:
            addresses = _resolve_all(hostname)
        except Exception as exc:
            return UrlVerdict(False, url, f"DNS resolution failed: {exc}")
        if not addresses:
            return UrlVerdict(False, url, "hostname resolved to no addresses")
        for ip in addresses:
            if not _ip_is_public(ip):
                return UrlVerdict(False, url, f"{hostname} resolves to non-public {ip}")

    return UrlVerdict(True, url)


def is_safe_url(raw_url: str, **kwargs) -> bool:
    """Boolean convenience wrapper around check_url."""
    return bool(check_url(raw_url, **kwargs))
