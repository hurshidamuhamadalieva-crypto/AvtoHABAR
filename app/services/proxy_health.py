"""Proxy authentication and Telegram reachability checks; credentials never logged."""
import asyncio
import time
import socket
import socks
from collections import Counter
from config import config

_lock = asyncio.Lock()
_cached = None


def error_category(exc):
    cause = getattr(exc, "socket_err", None)
    if cause and cause is not exc:
        return error_category(cause)
    if isinstance(exc, socks.SOCKS5AuthError):
        return "auth_error"
    if isinstance(exc, socks.HTTPError) and any(code in str(exc) for code in ("407", "401")):
        return "auth_error"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, (socks.GeneralProxyError, socks.SOCKS5Error, socks.SOCKS4Error, socks.HTTPError)):
        return "protocol_error"
    return "connection_error"


def _tunnel(proxy, target):
    kind, host, port, rdns, username, password = proxy
    kinds = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
    with socks.socksocket() as sock:
        sock.set_proxy(kinds[kind], host, port, rdns, username, password)
        sock.settimeout(5)
        sock.connect(target)


def _probe(proxy):
    try:
        _tunnel(proxy, ("149.154.167.51", 443))
        return "working"
    except Exception as exc:
        category = error_category(exc)
    if category == "auth_error":
        return category
    try:
        _tunnel(proxy, ("149.154.175.50", 443))
        return "working"
    except Exception as exc:
        second = error_category(exc)
    return second if second == "auth_error" else category


def cached_status(proxy):
    if _cached and _cached[0] == tuple(config.PROXIES) and time.monotonic() - _cached[1] < 60:
        return _cached[3].get(proxy)
    return None


async def check_proxies():
    global _cached
    async with _lock:
        key = tuple(config.PROXIES)
        if _cached and _cached[0] == key and time.monotonic() - _cached[1] < 60:
            return _cached[2]
        semaphore = asyncio.Semaphore(8)
        async def check(proxy):
            async with semaphore:
                return await asyncio.to_thread(_probe, proxy)
        results = await asyncio.gather(*(check(p) for p in config.PROXIES))
        # Boolean values supported for older callers and mocks.
        results = ["working" if item is True else "connection_error" if item is False else item for item in results]
        counts = Counter(results)
        report = {"total": len(results), "working": counts["working"], "failed": len(results) - counts["working"],
                  "auth_error": counts["auth_error"], "timeout": counts["timeout"],
                  "protocol_error": counts["protocol_error"], "connection_error": counts["connection_error"],
                  "invalid": config.INVALID_PROXY_COUNT}
        _cached = (key, time.monotonic(), report, dict(zip(config.PROXIES, results)))
        return report
