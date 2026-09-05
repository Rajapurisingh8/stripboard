"""Shared Vertex AI client and retry policy.

One place that knows how to authenticate, so the tagger and the continuity
checker cannot drift apart. Google AI only — nothing else may appear here.
"""

from __future__ import annotations

import json
import logging
import os
import random
import tempfile
import threading
import time
from typing import Any, Callable, TypeVar

from google import genai

log = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_LOCATION = "us-central1"

_client: genai.Client | None = None
_lock = threading.Lock()


def _materialise_credentials() -> None:
    """Write the service-account JSON from env to a file for google-auth.

    Replit Secrets hold the key as a single-line JSON string, but the auth
    libraries want a path. Done once; the temp file lives for the process.
    """
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return

    raw = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not raw:
        log.warning(
            "No GOOGLE_APPLICATION_CREDENTIALS_JSON set; relying on ambient "
            "credentials. This works in Cloud Shell and fails in Replit."
        )
        return

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON is not valid JSON. The most "
            "common cause is a truncated paste — the private_key block is long."
        ) from exc

    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(parsed, handle)
    handle.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = handle.name
    log.info("Vertex credentials loaded for %s", parsed.get("client_email", "?"))


def get_client() -> genai.Client:
    """Return the process-wide Vertex client, creating it on first use."""
    global _client
    if _client is not None:
        return _client

    with _lock:
        if _client is not None:
            return _client

        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT is not set. Add it to Replit Secrets."
            )

        _materialise_credentials()
        _client = genai.Client(
            vertexai=True,
            project=project,
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION),
        )
        return _client


def get_model() -> str:
    """Model name, overridable per-deployment.

    Beware: a stale account-level GEMINI_MODEL secret silently wins over the
    per-repl one and produces a 404 that looks like an outage.
    """
    return os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    label: str = "call",
) -> T:
    """Run `fn`, retrying transient Vertex failures with jittered backoff.

    Retries rate limits and server errors. Does not retry auth or bad-request
    failures — those will not fix themselves, and retrying them just makes a
    demo hang before it fails.
    """
    transient = ("429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE")
    last: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - SDK raises varied types
            last = exc
            text = f"{type(exc).__name__}: {exc}"
            if not any(token in text for token in transient):
                raise
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.4)
            log.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                label,
                attempt,
                attempts,
                text,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError(f"{label} failed after {attempts} attempts: {last}") from last


def parse_json_response(response: Any, label: str = "response") -> Any:
    """Parse a structured-output response, with a readable failure message."""
    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError(f"{label} returned no text (possibly a safety block)")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} returned non-JSON: {text[:300]!r}") from exc
