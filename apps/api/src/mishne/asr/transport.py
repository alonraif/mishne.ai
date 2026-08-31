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
"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .base import ASRError

#: Floor, for connection setup and a short clip.
TIMEOUT_S = 120

#: Plus this much per minute of audio. Generous on purpose: the cost of waiting
#: too long is a slow job, and the cost of not waiting long enough is paying
#: twice for the same hour.
TIMEOUT_S_PER_AUDIO_MINUTE = 30


def timeout_for(audio_seconds: float) -> int:
    return TIMEOUT_S + int(audio_seconds / 60.0 * TIMEOUT_S_PER_AUDIO_MINUTE)


def _error(exc: urllib.error.HTTPError) -> ASRError:
    detail = exc.read().decode(errors="replace")[:400]
    # Same rule as the LLM path: 4xx other than rate limiting is our bug and
    # fails identically at the other vendor. 413 is the audio being too big,
    # which is a fact about the file, not about the vendor.
    retryable = exc.code in (408, 409, 429) or exc.code >= 500
    return ASRError(f"HTTP {exc.code}: {detail}", retryable=retryable)


def _open(req: urllib.request.Request, timeout: int) -> tuple[dict, int]:
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
