# ADR-0001 — OpenTimelineIO as the canonical timeline format

**Status:** Accepted · **Date:** 2026-08-28

## Context

mishne.ai must emit AAF, FCPXML, and EDL, targeting Avid Media Composer, Premiere
Pro, DaVinci Resolve, and Final Cut Pro. The naive approach — generate each format
independently from internal structures, or convert between formats — produces N×M
conversion paths, inconsistent output, and no single artifact to inspect when a
customer reports a bad export.

## Decision

Every job converges on a single **OpenTimelineIO** document. Every output format is a
projection of it. No format is ever generated from another.

OTIO is persisted as a job artifact and treated as the record of truth for what the
edit actually was.

## Rationale

- Industry-standard interchange format, maintained under the Academy Software
  Foundation. It is what this problem was designed for.
- Adapter ecosystem covers all four target NLEs.
- `opentime.RationalTime` provides correct rational frame-rate arithmetic, which is
  where hand-rolled timeline code reliably goes wrong.
- Gives a single object to validate against, enabling the round-trip validation gate
  in [02 — Media & Interchange](../architecture/02-media-and-interchange.md).
- Supporting a new NLE later means writing or adopting one adapter.

## Consequences

**Positive** — one representation to test; validation gate becomes possible;
debugging a bad export starts from a readable canonical file.

**Negative** — OTIO's model is a constraint. Anything it cannot express cannot be
represented, and adapter quality varies considerably. The AAF adapter in particular
has known limitations; see
[02 — Media & Interchange](../architecture/02-media-and-interchange.md#on-the-aaf-writer).

**Mitigation** — where the OTIO AAF adapter proves insufficient, write AAF with
`pyaaf2` directly *from the OTIO document*. The canonical representation is
unaffected; only the projection changes.
