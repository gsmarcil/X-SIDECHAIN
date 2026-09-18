from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class ProviderHTTPError(RuntimeError):
    pass


def post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ProviderHTTPError(f"provider returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ProviderHTTPError(f"provider connection failed: {exc.reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderHTTPError("provider returned non-JSON data") from exc
    if not isinstance(parsed, dict):
        raise ProviderHTTPError("provider returned an unexpected JSON shape")
    return parsed

