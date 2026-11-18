"""Endpoint checks: aider does not talk to services on the user's own machine.

Cloud endpoints are configurable as before (--openai-api-base, OPENAI_API_BASE,
Azure and gateway base URLs, api_base in model settings). An endpoint is refused
only when it points to this machine: a loopback, private, link-local or otherwise
non-public address, or a name that resolves to one.

The address classes follow RFC 6890 (special-purpose addresses) and RFC 1918
(private IPv4); the name rule follows RFC 6761 section 6.3 (localhost). This is a
configuration guard, not a security boundary: a DNS entry changed after the check
is not seen, and editing the hosts file can bypass it.
"""

import ipaddress
import os
import socket
import urllib.parse
from functools import lru_cache


class EndpointError(ValueError):
    """An endpoint points to this machine. The message is safe to show to the user."""


def _is_local_ip(ip):
    return not ip.is_global


@lru_cache(maxsize=256)
def is_local_host(host):
    host = host.strip("[]").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return _is_local_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False  # Unresolvable names fail later, when the request is made.
    for info in infos:
        address = info[4][0].split("%", 1)[0]
        if _is_local_ip(ipaddress.ip_address(address)):
            return True
    return False


def check_endpoint(url, source):
    """Raise EndpointError if url points to this machine. Empty values are ignored."""
    if not url:
        return
    host = urllib.parse.urlsplit(url).hostname
    if not host:
        raise EndpointError(f"{source} is not a valid URL: {url}")
    if is_local_host(host):
        raise EndpointError(
            f"{source} points to this machine ({host}). aider only supports cloud endpoints."
        )


def check_environment(environ=os.environ):
    """Check every base-URL variable that the libraries read (*_API_BASE, *_BASE_URL)."""
    for name, value in environ.items():
        if name.endswith("_API_BASE") or name.endswith("_BASE_URL"):
            check_endpoint(value, name)
