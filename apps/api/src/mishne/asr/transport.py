"""HTTP for the managed ASR providers. `urllib`, for the reason in llm/providers.

Two request shapes rather than one: xAI takes multipart/form-data with the audio
as a file part, Google takes a JSON body referencing a file it has already been
given. Both are built here so the providers stay about the vendor's semantics
rather than about MIME.

## Timeouts are sized by audio, not by a constant

A transcription request holds the connection open for as long as the vendor
takes to process the audio, and that scales with the audio. A flat timeout that
suits a four-minute clip cuts off a forty-minute one — and `_post` treats a
timeout as retryable, so the router would fail over to the other vendor and
quietly transcribe the whole thing twice, paying both. That exact shape already
bit the LLM path (see the TIMEOUT_S note in llm/providers.py); it is worse here,
because here the retry is a fresh hour of audio rather than a fresh prompt.

## A rate limit is retried here, and nowhere else could

There are three retries in this system and they answer different questions.
The router (`asr/routing`) asks *which vendor*; the runner
(`orchestration/runner`) asks *is this stage worth another attempt*. Neither
can help a request the vendor merely refused to take right now: for a language
with one engine — Hebrew is one, see `routing.plan` — the router has nobody to
fail over to, and the runner's answer is to re-run the whole stage, re-paying
for every chunk that already succeeded.

So a vendor that says "not now" is asked again here, in place, with its own
`Retry-After` honoured. Narrowly: only the statuses that mean the audio was
NOT processed. A timeout or a reset stays a failure, because those may mean the
hour was transcribed and only the response was lost, and retrying them is the
"paying twice" trap above.
"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from ..logging import get_logger
from .base import ASRError

log = get_logger(__name__)

#: Floor, for connection setup and a short clip.
TIMEOUT_S = 120

#: Plus this much per minute of audio. Generous on purpose: the cost of waiting
#: too long is a slow job, and the cost of not waiting long enough is paying
#: twice for the same hour.
TIMEOUT_S_PER_AUDIO_MINUTE = 30


def timeout_for(audio_seconds: float) -> int:
    return TIMEOUT_S + int(audio_seconds / 60.0 * TIMEOUT_S_PER_AUDIO_MINUTE)


#: Statuses that mean the vendor did not do the work and would take the same
#: request again: a rate limit, a request timeout, an overloaded backend.
#: Deliberately narrower than `ASRError.retryable`, which the router also sets
#: for resets and timeouts — see the module docstring for why those must not be
#: retried in place.
RETRY_STATUS = frozenset({408, 429, 503})

#: How long to wait before asking again, when the vendor did not say. Minutes
#: rather than seconds: a rate limit measured in requests per minute is not
#: over in two, and the stage this sits under is already the slow one, so a
#: retry that waits is cheaper than a job that fails. A vendor's own
#: `Retry-After` always wins over these.
RETRY_S = (5.0, 20.0, 60.0)

#: Never wait longer than this on a vendor's say-so. `Retry-After: 86400` is a
#: quota that is gone for the day, not a wait — fail and let the runner and the
#: operator decide, rather than holding a worker for an hour.
RETRY_AFTER_MAX_S = 120.0


def _retry_after(headers) -> float:
    """`Retry-After`, in seconds. Only the delta-seconds form, which is what
    both vendors send; an HTTP-date is treated as absent rather than guessed."""
    raw = (headers.get("retry-after") or "").strip() if headers else ""
    try:
        return max(0.0, min(float(raw), RETRY_AFTER_MAX_S))
    except ValueError:
        return 0.0


def _send(attempt, *, retries: tuple[float, ...] = RETRY_S, sleep=time.sleep):
    """Call `attempt()`, asking again while the vendor is only saying "not now".

    Returns what `attempt` returned, or re-raises its last failure. A callable
    rather than a request so that the retry sits above whatever a caller counts
    as one exchange — for the resumable upload that is two round trips.
    """
    for wait in (*retries, None):
        try:
            return attempt()
        except ASRError as exc:
            if wait is None or exc.status not in RETRY_STATUS:
                raise
            delay = exc.retry_after or wait
            # Status and delay only. The vendor's message is in the exception
            # and stays there: it is the one thing that may quote a filename.
            log.info("asr.retrying", status=exc.status,
                     delay_s=round(delay, 1), vendor_said=bool(exc.retry_after))
            sleep(delay)


def _error(exc: urllib.error.HTTPError) -> ASRError:
    detail = exc.read().decode(errors="replace")[:400]
    # Same rule as the LLM path: 4xx other than rate limiting is our bug and
    # fails identically at the other vendor. 413 is the audio being too big,
    # which is a fact about the file, not about the vendor.
    retryable = exc.code in (408, 409, 429) or exc.code >= 500
    return ASRError(f"HTTP {exc.code}: {detail}", retryable=retryable,
                    status=exc.code, retry_after=_retry_after(exc.headers))


def _open(req: urllib.request.Request, timeout: int) -> tuple[dict, int]:
    return _send(lambda: _open_once(req, timeout))


def _open_once(req: urllib.request.Request, timeout: int) -> tuple[dict, int]:
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        raise _error(exc)
    except Exception as exc:  # noqa: BLE001 — timeouts, DNS, resets
        raise ASRError(f"{type(exc).__name__}: {exc}", retryable=True)
    ms = int((time.time() - t0) * 1000)
    try:
        return json.loads(body), ms
    except json.JSONDecodeError as exc:
        raise ASRError(f"response was not JSON: {exc}", retryable=False)


def post_json(url: str, headers: dict, body: dict, *,
              timeout: int = TIMEOUT_S) -> tuple[dict, int]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", **headers})
    return _open(req, timeout)


def post_multipart(url: str, headers: dict, fields: dict,
                   file_field: str, file_path: Path, *,
                   timeout: int = TIMEOUT_S) -> tuple[dict, int]:
    """One file plus scalar fields, built by hand.

    `requests` would be one line and one more dependency in the image that runs
    every job; the encoding below is a dozen lines of stable format. The file is
    read into memory whole, which is bounded by the same thing the worker's disk
    already is: chunking (`asr/chunking.py`) keeps a request to half an hour of
    16 kHz mono, about 29 MB.
    """
    boundary = f"----mishne{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\""
            f"\r\n\r\n{value}\r\n".encode()
        )
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; "
        f"name=\"{file_field}\"; filename=\"{file_path.name}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n".encode()
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    data = b"".join(parts)

    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"content-type": f"multipart/form-data; boundary={boundary}",
                 "content-length": str(len(data)), **headers})
    return _open(req, timeout)


def post_capture_headers(url: str, headers: dict, body: dict | bytes, *,
                         timeout: int = TIMEOUT_S) -> tuple[dict, dict, int]:
    """As `post_json`, but hands back the response headers too.

    Google's resumable upload returns the URL to upload to in a header rather
    than in the body, so one call in this package needs them. Kept as its own
    function so the common path stays a two-value return.
    """
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    return _send(lambda: _capture_once(req, timeout))


def _capture_once(req: urllib.request.Request,
                  timeout: int) -> tuple[dict, dict, int]:
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            got = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        raise _error(exc)
    except Exception as exc:  # noqa: BLE001
        raise ASRError(f"{type(exc).__name__}: {exc}", retryable=True)
    ms = int((time.time() - t0) * 1000)
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    return payload, got, ms


def delete(url: str, headers: dict, *, timeout: int = 30) -> None:
    """Best effort. A file we failed to delete is not a reason to fail a job."""
    req = urllib.request.Request(url, method="DELETE", headers=headers)
    try:
        urllib.request.urlopen(req, timeout=timeout).close()
    except Exception:  # noqa: BLE001
        pass


def post_bytes(url: str, headers: dict, data: bytes, *,
               timeout: int = TIMEOUT_S) -> tuple[dict, int]:
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    return _open(req, timeout)
