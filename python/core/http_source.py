"""
core/http_source.py
───────────────────
Shared async HTTP fetch adapter used by all extractors that pull from
public REST/JSON endpoints.

Returns a structured result dict — never raises on HTTP or network errors.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("vision_i.core.http_source")

_DEFAULT_HEADERS = {
    "User-Agent": "VisionI/1.0 OSINT",
    "Accept": "application/json, */*;q=0.8",
}


async def fetch_source(
    url: str,
    *,
    timeout: int = 15,
    retries: int = 2,
    source_tag: str = "unknown",
    raw: bool = False,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Fetch a URL asynchronously with retry logic.

    Parameters
    ----------
    url         : Full URL to fetch.
    timeout     : Per-attempt timeout in seconds.
    retries     : Number of additional attempts after the first failure.
    source_tag  : Label attached to log messages and the result dict.
    raw         : If True, return raw bytes in ``data`` instead of parsed JSON.
    headers     : Extra request headers (merged with defaults).

    Returns
    -------
    dict with keys:
        source      – source_tag
        timestamp   – UTC ISO-8601 string of when the fetch completed
        status      – "ok" | "error"
        data        – parsed JSON (dict/list) or raw bytes if raw=True; None on error
        error       – None on success, error message string on failure
        latency_ms  – round-trip time in milliseconds (int)
        meta        – dict with url, http_status, attempt_count
    """
    try:
        import aiohttp
    except ImportError:
        logger.error("aiohttp is not installed — cannot fetch %s", url)
        return _error_result(source_tag, url, "aiohttp not installed", latency_ms=0)

    from core.utils import utcnow_iso

    merged_headers = {**_DEFAULT_HEADERS, **(headers or {})}
    last_exc: Optional[Exception] = None
    attempt = 0
    t0 = time.monotonic()

    for attempt in range(retries + 1):
        try:
            connector = aiohttp.TCPConnector(ssl=True)
            async with aiohttp.ClientSession(
                connector=connector,
                headers=merged_headers,
            ) as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    allow_redirects=True,
                ) as resp:
                    latency_ms = int((time.monotonic() - t0) * 1000)

                    if resp.status >= 400:
                        body_preview = (await resp.text())[:200]
                        err = f"HTTP {resp.status}: {body_preview}"
                        logger.warning(
                            "[%s] fetch error attempt=%d/%d url=%s err=%s",
                            source_tag, attempt + 1, retries + 1, url, err,
                        )
                        last_exc = Exception(err)
                        # 4xx errors are not retryable
                        if resp.status < 500:
                            return _error_result(
                                source_tag, url, err,
                                latency_ms=latency_ms,
                                http_status=resp.status,
                                attempt_count=attempt + 1,
                            )
                        continue  # 5xx — retry

                    if raw:
                        data = await resp.read()
                    else:
                        try:
                            data = await resp.json(content_type=None)
                        except Exception as json_err:
                            text = await resp.text()
                            logger.warning(
                                "[%s] JSON parse failed: %s — returning raw text",
                                source_tag, json_err,
                            )
                            data = text

                    return {
                        "source": source_tag,
                        "timestamp": utcnow_iso(),
                        "status": "ok",
                        "data": data,
                        "error": None,
                        "latency_ms": latency_ms,
                        "meta": {
                            "url": url,
                            "http_status": resp.status,
                            "attempt_count": attempt + 1,
                        },
                    }

        except asyncio.TimeoutError as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            last_exc = exc
            logger.warning(
                "[%s] timeout attempt=%d/%d url=%s",
                source_tag, attempt + 1, retries + 1, url,
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            last_exc = exc
            logger.warning(
                "[%s] fetch exception attempt=%d/%d url=%s exc=%s",
                source_tag, attempt + 1, retries + 1, url, exc,
            )

    latency_ms = int((time.monotonic() - t0) * 1000)
    err_msg = str(last_exc) if last_exc else "unknown error"
    logger.error(
        "[%s] all %d attempt(s) failed url=%s last_err=%s",
        source_tag, attempt + 1, url, err_msg,
    )
    return _error_result(
        source_tag, url, err_msg,
        latency_ms=latency_ms,
        attempt_count=attempt + 1,
    )


def _error_result(
    source_tag: str,
    url: str,
    error: str,
    *,
    latency_ms: int = 0,
    http_status: Optional[int] = None,
    attempt_count: int = 1,
) -> Dict[str, Any]:
    try:
        from core.utils import utcnow_iso
        ts = utcnow_iso()
    except Exception:
        from datetime import datetime
        ts = datetime.utcnow().isoformat() + "Z"

    return {
        "source": source_tag,
        "timestamp": ts,
        "status": "error",
        "data": None,
        "error": error,
        "latency_ms": latency_ms,
        "meta": {
            "url": url,
            "http_status": http_status,
            "attempt_count": attempt_count,
        },
    }
