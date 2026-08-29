"""Stage 11 — generate the deliverables.

Every writer is attempted independently and failures are captured rather than
raised: a job that produces three of four formats is worth delivering, and
knowing which one failed is worth more than a stack trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import opentimelineio as otio

from ...interchange import fcpx_patch

# (label, adapter, extension, target NLEs, flattens audio into channel notation)
FORMATS = [
    ("AAF", "AAF", "aaf", "Avid Media Composer", False),
    ("FCPXML", "fcpx_xml", "fcpxml", "Premiere Pro · Resolve · Final Cut", False),
    ("EDL", "cmx_3600", "edl", "Universal fallback", True),
    ("OTIO", "otio_json", "otio", "Canonical", False),
]


@dataclass
class Artifact:
    fmt: str
    path: Path | None
    ok: bool
    bytes: int = 0
    target_nle: str = ""
    error: str = ""


def emit(timeline: otio.schema.Timeline, out_dir: Path,
         stem: str) -> list[Artifact]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # NTSC rates cannot be written without this. See interchange/fcpx_patch.py.
    fcpx_patch.apply()

    results: list[Artifact] = []
    for label, adapter, ext, nle, _flat in FORMATS:
        path = out_dir / f"{stem}.{ext}"
        try:
            otio.adapters.write_to_file(timeline, str(path), adapter_name=adapter)
            results.append(Artifact(label, path, True, path.stat().st_size, nle))
        except Exception as exc:  # noqa: BLE001
            results.append(Artifact(label, None, False, target_nle=nle,
                                    error=f"{type(exc).__name__}: {exc}"))
    return results
