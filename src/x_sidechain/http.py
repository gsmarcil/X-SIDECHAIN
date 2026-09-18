from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any


MAX_RESPONSE_BYTES = 32 * 1024 * 1024
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
DEFAULT_ATTEMPTS = 3


class ProviderHTTPError(RuntimeError):
    """A provider call failed. `retryable` marks transient transport conditions."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _retry_after_seconds(headers: Any, fallback: float) -> float:
    try:
        raw = headers.get("Retry-After") if headers is not None else None
    except AttributeError:
        raw = None
    if not raw:
        return fallback
    try:
        return max(0.0, min(60.0, float(str(raw).strip())))
    except ValueError:
        return fallback


def _attempt(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> str:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ProviderHTTPError(
            f"provider returned HTTP {exc.code}: {body}",
            status=exc.code,
            retryable=exc.code in RETRYABLE_STATUS,
            ) from exc
    except urllib.error.URLError as exc:
        raise ProviderHTTPError(f"provider connection failed: {exc.reason}", retryable=True) from exc
    except TimeoutError as exc:
        raise ProviderHTTPError(f"provider request timed out after {timeout}s", retryable=True) from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderHTTPError("provider response exceeded the 32 MiB safety limit")
    return raw.decode("utf-8", errors="replace")


def post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    attempts: int = DEFAULT_ATTEMPTS,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """POST JSON and decode a JSON object, retrying transient provider failures.

    Retries cost no model-call budget, but a provider may still bill an accepted
    request that failed on the way back, so the attempt count stays small.
    """
    attempts = max(1, attempts)
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            body = _attempt(url, headers, payload, timeout)
        except ProviderHTTPError as exc:
            cause = exc.__cause__
            wait = _retry_after_seconds(getattr(cause, "headers", None), delay)
            if not exc.retryable or attempt == attempts:
                raise
            sleep(wait + random.uniform(0.0, 0.25))
            delay = min(delay * 2, 30.0)
            continue
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderHTTPError("provider returned non-JSON data") from exc
        if not isinstance(parsed, dict):
            raise ProviderHTTPError("provider returned an unexpected JSON shape")
        return parsed
    raise ProviderHTTPError("provider call failed after all retry attempts")
