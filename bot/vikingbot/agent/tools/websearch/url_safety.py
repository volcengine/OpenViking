"""Validation for untrusted URLs returned by web-search providers."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

_LOCAL_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
    }
)
_IDNA_DOTS = str.maketrans("\u3002\uff0e\uff61", "...")
_NAT64_WELL_KNOWN_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


def _parse_legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse non-canonical IPv4 forms accepted by common URL clients."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None

    values: list[int] = []
    for part in parts:
        if not part:
            return None
        base = 10
        digits = part
        if part.lower().startswith("0x"):
            base = 16
            digits = part[2:]
        elif len(part) > 1 and part.startswith("0"):
            base = 8
            digits = part[1:]
        if not digits:
            return None
        try:
            values.append(int(digits, base))
        except ValueError:
            return None

    leading = values[:-1]
    final_width = 8 * (5 - len(values))
    if any(value > 0xFF for value in leading) or values[-1] >= 1 << final_width:
        return None

    packed = 0
    for value in leading:
        packed = (packed << 8) | value
    packed = (packed << final_width) | values[-1]
    return ipaddress.IPv4Address(packed)


def _embedded_ipv4(address: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """Return IPv4 addresses carried by common IPv6 transition formats."""
    embedded = [value for value in (address.ipv4_mapped, address.sixtofour) if value]
    if address in _NAT64_WELL_KNOWN_PREFIX:
        embedded.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    if 1 < int(address) < 1 << 32:
        embedded.append(ipaddress.IPv4Address(int(address)))
    if address.teredo:
        embedded.extend(address.teredo)
    return embedded


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def safe_public_result_url(value: object) -> str:
    """Return a public HTTP(S) result URL, or an empty string when unsafe.

    Search results are data, not navigation instructions. This check is
    intentionally DNS-free: it removes local names and non-public IP literals
    without adding DNS latency or claiming to secure a later network request.
    """
    if not isinstance(value, str):
        return ""
    url = value.strip()
    if not url or "\\" in url or any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        return ""

    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not host:
        return ""

    normalized_host = host.translate(_IDNA_DOTS).lower().rstrip(".")
    if (
        not normalized_host
        or "%" in normalized_host
        or normalized_host in _LOCAL_NAMES
        or normalized_host.endswith(".localhost")
        or normalized_host.endswith(".local")
    ):
        return ""

    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = _parse_legacy_ipv4(normalized_host)
    if address is None:
        return url
    if not _is_public_address(address):
        return ""
    if isinstance(address, ipaddress.IPv6Address):
        if any(not _is_public_address(embedded) for embedded in _embedded_ipv4(address)):
            return ""
    return url
