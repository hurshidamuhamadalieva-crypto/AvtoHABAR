"""Bounded SOCKS/HTTP proxy checks; no Telegram account login required."""
import asyncio
import time
import socks
from config import config

_lock = asyncio.Lock()
_cached = None


def _probe(proxy):
    kind, host, port, rdns, username, password = proxy
    kinds = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
    try:
        with socks.socksocket() as sock:
            sock.set_proxy(kinds[kind.lower()], host, port, rdns, username, password)
            sock.settimeout(5)
            # Verify authenticated tunnelling to Telegram, not merely an open port.
            sock.connect(("149.154.167.51", 443))
        return True
    except Exception:
        return False


async def check_proxies():
    global _cached
    async with _lock:
        if _cached and time.monotonic() - _cached[0] < 60:
            return _cached[1]
        semaphore = asyncio.Semaphore(8)
        async def check(proxy):
            async with semaphore:
                return await asyncio.to_thread(_probe, proxy)
        results = await asyncio.gather(*(check(p) for p in config.PROXIES))
        report = {"total": len(results), "working": sum(results), "failed": len(results) - sum(results)}
        _cached = (time.monotonic(), report)
        return report
