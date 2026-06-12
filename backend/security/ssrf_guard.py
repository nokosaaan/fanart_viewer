"""
SSRF (Server-Side Request Forgery) protection.

Call validate_url(url) before any server-side HTTP fetch that uses
attacker-controlled input. Raises SSRFError for:
  - non-http(s) schemes
  - hostnames that resolve to private / loopback / reserved IP ranges
"""

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {'http', 'https'}

# All RFC-reserved ranges that should never be reachable from a public server.
_BLOCKED_NETWORKS = [
    # IPv4
    ipaddress.ip_network('0.0.0.0/8'),        # "this" network
    ipaddress.ip_network('10.0.0.0/8'),        # private
    ipaddress.ip_network('100.64.0.0/10'),     # shared address space (CGN)
    ipaddress.ip_network('127.0.0.0/8'),       # loopback
    ipaddress.ip_network('169.254.0.0/16'),    # link-local / AWS metadata endpoint
    ipaddress.ip_network('172.16.0.0/12'),     # private
    ipaddress.ip_network('192.0.0.0/24'),      # IETF protocol assignments
    ipaddress.ip_network('192.168.0.0/16'),    # private
    ipaddress.ip_network('198.18.0.0/15'),     # benchmarking
    ipaddress.ip_network('198.51.100.0/24'),   # documentation (TEST-NET-2)
    ipaddress.ip_network('203.0.113.0/24'),    # documentation (TEST-NET-3)
    ipaddress.ip_network('240.0.0.0/4'),       # reserved (future use)
    ipaddress.ip_network('255.255.255.255/32'),# broadcast
    # IPv6
    ipaddress.ip_network('::1/128'),           # loopback
    ipaddress.ip_network('fc00::/7'),          # unique local (ULA)
    ipaddress.ip_network('fe80::/10'),         # link-local
    ipaddress.ip_network('::ffff:0:0/96'),     # IPv4-mapped IPv6
    ipaddress.ip_network('::/128'),            # unspecified
]


class SSRFError(ValueError):
    """Raised when a URL is rejected for SSRF-safety reasons."""


def _check_ip(addr):
    """Raise SSRFError if the IP address is private or reserved."""
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) before checking
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped

    for network in _BLOCKED_NETWORKS:
        if addr.version == network.version and addr in network:
            raise SSRFError(f'IP address {addr} is in a blocked range ({network})')


def validate_url(url: str) -> None:
    """
    Validate that *url* is safe to fetch from the server side.

    Raises SSRFError on any of:
      - empty / unparseable URL
      - non-http(s) scheme  (blocks file://, ftp://, gopher://, etc.)
      - missing hostname
      - hostname is a numeric IP in a private/reserved range
      - DNS resolution returns only private/reserved IPs

    Does NOT guarantee protection against DNS-rebinding; pair with a short
    DNS TTL policy or a socket-level hook for full coverage.
    """
    if not url or not isinstance(url, str):
        raise SSRFError('Empty or non-string URL')

    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise SSRFError(f'Unparseable URL: {exc}') from exc

    scheme = (parsed.scheme or '').lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFError(
            f'Scheme {scheme!r} is not allowed; only http/https are permitted'
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError('URL contains no hostname')

    # If the hostname is already a numeric IP address, check it directly
    # without a DNS round-trip.
    try:
        addr = ipaddress.ip_address(hostname)
        _check_ip(addr)
        return
    except SSRFError:
        raise
    except ValueError:
        pass  # not a numeric IP — proceed to DNS resolution

    # Resolve hostname and verify every returned address
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFError(f'DNS resolution failed for {hostname!r}: {exc}') from exc

    if not results:
        raise SSRFError(f'DNS returned no results for {hostname!r}')

    for _family, _type, _proto, _canon, sockaddr in results:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        _check_ip(addr)  # raises SSRFError if blocked
