"""Deterministic AAF MobIDs.

The OTIO AAF writer refuses to write a clip it cannot find a MobID for:

    AAFAdapterError: Cannot find mob ID for clip ...

It looks in `clip.metadata["AAF"]["MobID"]`, then
`clip.media_reference.metadata["AAF"]["MobID"]`, then inside the referenced
file if it is itself an AAF. `use_empty_mob_ids=True` makes it invent one —
which writes a file, and produces a timeline the editor cannot relink. That
option is a trap, not a fix.

**The MobID is the relink key.** Two rules follow:

1. When the source is an AAF exported from the customer's own project, inherit
   its MobID. The output then relinks silently in their bin.
2. When the source is a flat file, synthesize a MobID that is *stable for that
   file* — same file, same ID, every job, forever. A random ID per run means the
   editor relinks by hand every time you regenerate a cut.

Rule 2 is what this module does: a UUIDv5 over a stable source identity, dropped
into the material portion of a MobID that keeps a valid SMPTE label.
"""

from __future__ import annotations

import uuid

import aaf2.mobid

# Namespace for mishne.ai source identity. Fixed forever — changing it
# invalidates every MobID ever issued and breaks relink for existing cuts.
NAMESPACE = uuid.UUID("6f2c1e94-8b3d-5a17-9c4e-2d8f7a1b3c56")


def stable_mob_id(source_identity: str) -> aaf2.mobid.MobID:
    """A MobID that is always the same for the same source identity.

    `source_identity` should be something that identifies the *media*, not the
    path it happens to sit at today. A content hash is ideal. Filename plus size
    is a workable approximation. A bare path is not — the customer will move it.
    """
    mob_id = aaf2.mobid.MobID.new()  # gives us a valid SMPTE label prefix
    mob_id.material = uuid.uuid5(NAMESPACE, source_identity)
    return mob_id


def attach(media_reference, source_identity: str) -> aaf2.mobid.MobID:
    """Attach a stable MobID to an OTIO media reference, in place."""
    mob_id = stable_mob_id(source_identity)
    aaf_meta = media_reference.metadata.setdefault("AAF", {})
    aaf_meta["MobID"] = str(mob_id)
    aaf_meta["SourceID"] = str(mob_id)
    return mob_id
