"""Interchange: OTIO in, AAF/FCPXML/EDL out.

Ported from spikes/aaf-roundtrip, where every line of it was tested against
four frame rates and confirmed importing into DaVinci Resolve. The spike found
three things this code exists to encode:

- **AAF needs an explicit MobID per clip.** Without one the writer refuses; with
  a generated one it produces a file that cannot be relinked. The MobID *is* the
  relink key. See mobid.py.
- **FCPXML cannot write NTSC rates** without patching its adapter, and reads
  them back ~4% wrong. See fcpx_patch.py.
- **Validate by independent parse, not by round trip.** Writing and reading with
  the same library cannot catch a symmetric bug. See fcpxml_check.py.
"""

from . import fcpx_patch, mobid

__all__ = ["fcpx_patch", "mobid"]
