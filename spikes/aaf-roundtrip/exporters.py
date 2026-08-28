"""Write the timeline out in every interchange format.

Every writer is tried independently and failures are captured rather than
raised. A spike that dies on the first broken adapter tells you one thing; this
tells you which of four things work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import opentimelineio as otio


@dataclass
class ExportResult:
    fmt: str
    path: Path | None
    ok: bool
    bytes: int = 0
    error: str = ""


# (label, adapter, extension, target NLEs, flattens audio into channel notation)
FORMATS = [
    ("AAF", "AAF", "aaf", "Avid Media Composer", False),
    ("FCPXML", "fcpx_xml", "fcpxml", "Premiere Pro · Resolve · Final Cut Pro", False),
    ("EDL", "cmx_3600", "edl", "Universal fallback", True),
    ("OTIO", "otio_json", "otio", "Canonical — mishne.ai internal", False),
]


def export_all(timeline: otio.schema.Timeline, out_dir: Path,
               stem: str) -> list[ExportResult]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[ExportResult] = []

    for label, adapter, ext, _nle, _flat in FORMATS:
        path = out_dir / f"{stem}.{ext}"
        try:
            otio.adapters.write_to_file(timeline, str(path), adapter_name=adapter)
            results.append(
                ExportResult(label, path, True, bytes=path.stat().st_size)
            )
        except Exception as exc:  # noqa: BLE001 — reporting, not handling
            results.append(
                ExportResult(label, None, False,
                             error=f"{type(exc).__name__}: {exc}")
            )
    return results
