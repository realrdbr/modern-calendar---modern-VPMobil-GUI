"""Small structured diagnostics without payloads, credentials or topic URLs."""
import json
import logging
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

logger = logging.getLogger("ntfy.diagnostics")


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    if not logger.handlers:
        logger.addHandler(handler)
    logger.propagate = False
    level = os.getenv("NTFY_LOG_LEVEL", "INFO").upper()
    logger.setLevel(level if level in {"DEBUG", "INFO", "WARNING", "ERROR"} else "INFO")


def emit(event, *, level=logging.INFO, **fields):
    if logger.isEnabledFor(level):
        logger.log(level, json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                                     "event": event, **fields}, ensure_ascii=True))


def endpoint(url):
    try:
        parsed = urlsplit(url)
        return f"{parsed.scheme}://{parsed.hostname or ''}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    except ValueError:
        return "invalid-url"


def error_fields(error):
    response = getattr(error, "response", None)
    status = response.status_code if response is not None else None
    reason = {401: "authentication", 403: "topic_permission", 429: "rate_limit"}.get(status)
    if reason is None:
        reason = "timeout" if isinstance(error, requests.Timeout) else (
            "connection" if isinstance(error, requests.ConnectionError) else "request_failed")
    fields = {"error_type": type(error).__name__, "status": status, "reason": reason}
    if response is not None and isinstance(response.content, bytes) and len(response.content) <= 4096:
        try:
            code = response.json().get("code")
            if isinstance(code, int):
                fields["ntfy_code"] = code
        except (ValueError, AttributeError):
            pass
    return fields


def retry_delay(response):
    """At most one short retry; never shorten a server's Retry-After."""
    code = error_fields(requests.HTTPError(response=response)).get("ntfy_code")
    if code is not None and code != 42901:
        return None  # Daily/topic/global limits do not recover after a short pause.
    value = response.headers.get("Retry-After")
    if value is None:
        return 5.0
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 5.0
    return max(0.1, delay) if 0 <= delay <= 10 else None


def request(method, url, *, operation, retry_rate_limit=False, **kwargs):
    started = time.monotonic()
    try:
        response = method(url, **kwargs)
        if retry_rate_limit and response.status_code == 429:
            delay = retry_delay(response)
            if delay is not None:
                emit("ntfy.rate_limit_retry", level=logging.WARNING, operation=operation,
                     endpoint=endpoint(url), delay_seconds=delay)
                response.close()
                time.sleep(delay)
                response = method(url, **kwargs)
        response.raise_for_status()
    except requests.RequestException as error:
        emit("ntfy.request_failed", level=logging.ERROR, operation=operation,
             endpoint=endpoint(url), duration_ms=round((time.monotonic() - started) * 1000),
             **error_fields(error))
        raise
    emit("ntfy.request_ok", operation=operation, endpoint=endpoint(url),
         status=response.status_code, duration_ms=round((time.monotonic() - started) * 1000))
    return response
