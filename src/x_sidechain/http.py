from __future__ import annotations

import http.client
import json
import random
import re
import time
import urllib.error
import urllib.request
from typing import Any


MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_ERROR_BYTES = 8192
REDACTED = "[REDACTED]"
# Inverted on purpose. A configuration may name any header as the one carrying
# its key, so a list of credential names will always miss the next provider's.
# These are the headers known to carry nothing secret; every other value that
# goes out is treated as a secret coming back.
PUBLIC_HEADERS = {
    "accept",
    "accept-charset",
    "accept-encoding",
    "accept-language",
    "anthropic-version",
    "cache-control",
    "connection",
    "content-length",
    "content-type",
    "host",
    "openai-beta",
    "user-agent",
}
SECRET_SHAPES = (
    # "Bearer <token>" / "token <token>"
    (re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._\-]{12,}"), r"\1 " + REDACTED),
    # a bare vendor-prefixed key
    (re.compile(r"\b(?:sk|rk|pk|xai|gsk)-[A-Za-z0-9._\-]{12,}"), REDACTED),
    # a JSON field whose name says it holds a secret
    (
        re.compile(
            r'(?i)"(access_token|refresh_token|id_token|api_key|apikey|client_secret'
            r'|password|secret|token)"(\s*:\s*)"[^"]*"'
        ),
        r'"\1"\2"' + REDACTED + '"',
    ),
)


def _credentials(headers: dict[str, str]) -> list[str]:
    """The secret parts of the outgoing headers, longest first."""
    found: list[str] = []
    for name, value in headers.items():
        if name.lower() in PUBLIC_HEADERS or not value:
            continue
        found.append(value)
        # "Bearer sk-live-..." also leaks as just the token.
        parts = value.split(" ", 1)
        if len(parts) == 2 and parts[1]:
            found.append(parts[1])
    return sorted({item for item in found if len(item) >= 8}, key=len, reverse=True)


def scrub(text: str, headers: dict[str, str] | None = None) -> str:
    """Remove credentials from provider text before it is written anywhere.

    A gateway that echoes the request can hand back the very key used to call it,
    so the exact outgoing values are replaced first, then common token shapes
    that would belong to somebody else.
    """
    for secret in _credentials(headers or {}):
        text = text.replace(secret, REDACTED)
    for pattern, replacement in SECRET_SHAPES:
        text = pattern.sub(replacement, text)
    return text
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
DEFAULT_ATTEMPTS = 3


class ProviderHTTPError(RuntimeError):
    """A provider call failed.

    The message is safe to show to other agents: it never carries the provider's
    response body, which can echo request headers or another tenant's data. The
    body is kept in `detail` for the local operator only.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        retryable: bool = False,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.detail = detail


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


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect.

    urllib follows 301, 302 and 303 by turning the POST into a GET and
    re-sending the request headers to whatever Location names — including the
    Authorization header, to a host that may not be the provider at all. A
    provider that wants to move an endpoint can say so in its configuration;
    it cannot be allowed to say so mid-call.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url, code, "redirect refused: credentials are not re-sent", headers, fp
        )


_OPENER = urllib.request.build_opener(_NoRedirects)


def _urlopen(request: urllib.request.Request, timeout: int):  # noqa: ANN202
    """The one place a request leaves this process, so one place refuses redirects."""
    return _OPENER.open(request, timeout=timeout)


def _attempt(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> str:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with _urlopen(request, timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # Bounded twice: ask for a capped read, then cut what came back. The cap
        # exists precisely because the other side may not be well behaved.
        body = scrub(
            exc.read(MAX_ERROR_BYTES)[:MAX_ERROR_BYTES].decode("utf-8", errors="replace"),
            headers,
        )
        message = (
            f"provider redirected to another location (HTTP {exc.code}); refused so the "
            "credential is not re-sent. Point base_url at the new endpoint instead"
            if 300 <= exc.code < 400
            else f"provider returned HTTP {exc.code}"
        )
        raise ProviderHTTPError(
            message,
            status=exc.code,
            retryable=exc.code in RETRYABLE_STATUS,
            detail=body,
        ) from exc
    except TimeoutError as exc:
        raise ProviderHTTPError(
            f"provider request timed out after {timeout}s", retryable=True
        ) from exc
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        # urlopen only wraps failures it sees while connecting. A connection reset
        # or an early disconnect during getresponse() arrives raw, and those are
        # exactly the transient cases worth retrying.
        reason = getattr(exc, "reason", None) or type(exc).__name__
        raise ProviderHTTPError(f"provider connection failed: {reason}", retryable=True) from exc
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
