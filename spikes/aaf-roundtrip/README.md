# Spike A — AAF round-trip

Answers one question: **can mishne.ai hand an editor a timeline their NLE opens,
relinks, and plays at the right frame?**

This is the highest technical risk in the project and it has nothing to do with
AI. See [05 — Roadmap & Risks](../../docs/architecture/05-roadmap-and-risks.md).

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python spike.py all                    # everything, all four rates
.venv/bin/python spike.py all --rates 25         # one rate
.venv/bin/python spike.py all --codec h264       # fast, small, structure only
```

Outputs land in `out/<rate>/` — one AAF, FCPXML, EDL and OTIO per rate, plus a
`CHECKLIST-<rate>.md` with the exact expected values.

## What it does

1. **Renders test media** (`testmedia.py`) — five minutes per rate, with the
   timecode burned into every frame and a 1 kHz blip on every second boundary.
   This is what makes verification possible: park on a clip's first frame, read
   the number off the picture. "Check the timecode is right" is otherwise an
   instruction nobody can follow.
2. **Builds an OTIO timeline** (`timeline.py`) from a 20-cut plan, with 6-frame
   handles. This is the shape stage 10 will produce.
3. **Exports** AAF, FCPXML, EDL, OTIO (`exporters.py`), capturing failures per
   format rather than dying on the first one.
4. **Validates** each file against the source timeline (`validate.py`,
   `fcpxml_check.py`).
5. **Writes a manual checklist** (`checklist.py`) with per-clip expected
   timecode, for the part only a real NLE can answer.

The cut plan (`rates.py`) is not twenty arbitrary cuts. Each one targets a
specific failure class — first frame of media, sub-second clip, contiguous cuts
that must not merge, duplicate source ranges, out-of-order selection,
single-frame precision, the tail boundary, and three different drop-frame minute
boundaries — and its reason is printed next to it in the checklist, so a failure
points at a cause.

## Status

**Automated checks pass: 4 formats × 4 rates.** Frame-exact source ranges,
correct clip counts, correct edit rates.

| | 23.976 | 25 | 29.97 NDF | 29.97 DF |
|---|---|---|---|---|
| AAF | pass | pass | pass | pass |
| FCPXML | pass¹ | pass | pass¹ | pass¹ |
| EDL | pass² | pass² | pass² | pass² |
| OTIO | pass | pass | pass | pass |

¹ requires the patch in `fcpx_patch.py` — see below
² audio verified as channel notation, not as clips — see below

### NLE verification — in progress

| NLE | Format | Result | Date |
|---|---|---|---|
| DaVinci Resolve | AAF | **confirmed** | 2026-08-28 |
| Avid Media Composer | AAF | not tested | |
| Premiere Pro | FCPXML | not tested | |
| Final Cut Pro | FCPXML | not tested | |

**Resolve is the permissive one.** It imports almost anything, so a pass there
is real but weak evidence. Avid is strict, and Avid is the customer who actually
demands AAF — broadcast shops asking for AAF are overwhelmingly Avid shops. The
central question of this spike is therefore still open, and the project is
carrying that risk knowingly rather than having retired it.

**This does not mean the spike passed.** It means the files are internally
consistent. Whether Media Composer opens them is what the spike is actually
asking, and only Media Composer can answer it. Work through
`out/*/CHECKLIST-*.md` in each NLE and fill in the result table.

## Findings

### 1. AAF write works — but only with an explicit MobID

The OTIO AAF writer refuses any clip it cannot find a MobID for:

```
AAFAdapterError: Cannot find mob ID for clip ...
```

It looks in `clip.metadata["AAF"]["MobID"]`, then the media reference's
metadata, then inside the referenced file if that file is itself an AAF.

There is a `use_empty_mob_ids=True` option that invents one. **It is a trap.**
It produces a file that writes cleanly and cannot be relinked, because the MobID
*is* the relink key.

Two rules follow, and `mobid.py` implements the second:

- Source is an AAF from the customer's project → **inherit its MobID**. The
  output relinks silently in their bin. This is the strongest argument for
  AAF-in / AAF-out.
- Source is a flat file → **synthesize a MobID that is stable for that file**.
  Same media, same ID, every job, forever. A random ID per run means the editor
  relinks by hand every time a cut is regenerated.

`mobid.py` uses a UUIDv5 over a source identity. Note the caveat there: identity
should be a content hash, not a path — the customer will move the file.

This finding contradicts the assumption in
[02 — Media & Interchange](../../docs/architecture/02-media-and-interchange.md)
that AAF would be the fragile format. With correct metadata it was the most
reliable of the three.

### 2. FCPXML cannot write NTSC rates without a patch

`otio-fcpx-xml-adapter` looks frame duration up in a table keyed by *rounded*
rates (`23.98`, `29.97`) using exact float equality. OTIO supplies the true
rational — `23.976023976023978` — so every NTSC rate misses, returns `""`, and
fails later and elsewhere with:

```
ValueError: not enough values to unpack (expected 2, got 1)
```

Integer rates work, so the adapter looks fine at 25 or 30 and breaks on exactly
the rates most North American broadcast material is shot at. FCPXML is the
delivery path for Premiere, Resolve and Final Cut — three of four target NLEs.

`fcpx_patch.py` rounds before the lookup. **This belongs upstream**, not carried
as a patch, if mishne.ai ships FCPXML.

A second, deeper bug affects only reading: the adapter truncates the rate to an
integer (`int(23.976) == 23`), so it reads NTSC files back roughly 4% wrong. The
*written file is correct* — verified independently — so the deliverable is fine
and only the round trip is broken.

**Gotcha worth remembering:** OTIO loads adapter modules itself, by path. The
module you get from `import otio_fcpx_xml_adapter.fcpx_xml` is a *different
object* from the one the adapter runs. Patching the imported one appears to
work and changes nothing. Reach the plugin's module via
`otio.adapters.from_name(...).module()`.

### 3. Round-trip validation through one library is a weak check

`fcpxml_check.py` parses the XML directly rather than reading it back with the
adapter that wrote it. The immediate reason is finding 2 — the reader is broken
where the writer is not. The general reason matters more:

**Writing and reading with the same library cannot catch a symmetric bug.** If
writer and reader share a wrong assumption, the round trip agrees with itself
and the gate passes a file no NLE can open.

This applies directly to the stage-12 validation gate in the product. Validate
by independent parse, not by round trip.

### 4. EDL carries no frame rate

CMX3600 is a text format from the tape era. It holds timecode and nothing that
says what those timecodes mean. OTIO's reader defaults to 24 and silently
misreads everything; the spike passes the rate in explicitly.

Product consequence: an EDL handed to a customer is ambiguous unless they
already know the rate. Put the rate in the filename, and prefer AAF or FCPXML
wherever the NLE will take one.

EDL also expresses audio as channel notation on the video event rather than as
separate clips, so audio does not survive as countable objects. That is correct
behaviour, not a failure — `validate.py` reports it rather than failing it.

### 5. `09:58:00;00` is not a timecode

The spike's first draft used it as the source start. In drop-frame, frames `;00`
and `;01` are skipped at second `:00` of every minute not divisible by ten, so
that label does not exist — and the conversion silently returned a frame number
2 frames off, which surfaced as a checklist reading `09:57:59;28`.

Two fixes, both worth carrying into the product:

- **One conversion pair, used everywhere** (`timecode.py`). The original bug was
  `timeline.py` doing label arithmetic and `checklist.py` then applying the
  drop-frame correction a second time. Two implementations, one bug.
- **Reject impossible labels** rather than returning something. `tc_to_frames`
  raises `InvalidTimecode`. A timecode that cannot exist is a bug upstream, and
  guessing hides it.

The self-test walks every frame of hours 0, 1, 9, 10 and 23. An earlier version
walked only hour 0, passed, and left the bug at 09:58 in place — a reminder that
where a test looks matters as much as whether it exists.

## Files

| | |
|---|---|
| `spike.py` | CLI |
| `rates.py` | Frame rates and the 20-cut plan, with a reason per cut |
| `timecode.py` | The one timecode conversion pair, plus its self-test |
| `mobid.py` | Stable AAF MobIDs |
| `testmedia.py` | ffmpeg test source with burned-in TC and audio markers |
| `timeline.py` | OTIO timeline builder |
| `exporters.py` | AAF / FCPXML / EDL / OTIO writers |
| `validate.py` | Round-trip validation |
| `fcpxml_check.py` | Independent FCPXML parse |
| `fcpx_patch.py` | NTSC frame-rate workaround, documented |
| `checklist.py` | Manual NLE verification checklist generator |

`out/` is generated and gitignored, media included — the `.mov` files are about
250 MB each.
