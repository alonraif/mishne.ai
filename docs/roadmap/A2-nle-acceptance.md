# A2 — NLE acceptance, Avid above all

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first for project
> context; you should not need any other file.

## Goal

Prove a generated AAF opens, relinks and plays correctly in **Avid Media
Composer**, and confirm the other three NLEs. Then keep proving it automatically.

## Why this matters more than it looks

Every deliverable this product makes is an interchange file. If Media Composer
rejects them, there is no product — and Media Composer is the one NLE nobody has
tested. The generic AAF writers have a documented history of producing files it
chokes on.

This is **the highest technical risk in the project and it has nothing to do
with AI.** It is also perhaps a day of work once you have the machine.

## What already exists

- `spikes/aaf-roundtrip/` — hand-built 20-cut timelines, exported to AAF,
  FCPXML, EDL and OTIO at 23.976, 24, 25 and 29.97 DF/NDF. Automated checks pass
  for all four formats at all four rates. Outputs in `spikes/aaf-roundtrip/out/`.
- `apps/api/src/mishne/interchange/validate.py` — independent-parse validation,
  run on every job as stage 12.
- Real production output: `samples/SyncDaniel_roughcut/` and any `run.py` output.
- **DaVinci Resolve confirmed by hand** by Alon. Avid, Premiere and Final Cut
  are unconfirmed.

## What to build

1. **Get access to Media Composer.** A trial licence and a Windows or macOS
   machine is enough. This is the blocking dependency, not the engineering.
2. **Run the acceptance table** in
   [../architecture/02-media-and-interchange.md](../architecture/02-media-and-interchange.md)
   for each NLE and each rate: file opens without error; media relinks without a
   dialog; clip count matches; first and last frame timecodes exact; audio in
   sync at the three-hour mark; handles present and trimmable.
3. **Test the real outputs, not only the spike.** Specifically:
   - an AAF generated *from* an AAF, which inherits the source MobIDs
     (`samples/SyncDaniel_roughcut/`) — this should relink silently in the
     project it came from, and that claim is currently untested;
   - a cut with clips that span source joins, which the pipeline splits into
     several clips on purpose;
   - a **mixed-rate project**, which assembly conforms to the sequence rate. NLE
     behaviour here is unknown and the run output warns about it.
4. **Record the results** in the acceptance table, in the repo, with versions.
5. **If Avid rejects the files**, the documented fallback is writing AAF with
   `pyaaf2` directly against a known-good reference AAF exported from Media
   Composer itself. That is a real chunk of work. Discovering it is needed is the
   whole point of doing this now.

## Decisions already made

- OTIO is the canonical timeline; every format is a projection of it (ADR-0001).
  If a format needs a fix, fix the projection, never hand-edit an output.
- MobIDs are attached explicitly and inherited from the source AAF where there
  is one. This is what makes silent relinking possible.
- Validation is by **independent parse** — read the artifact back with a
  different code path and compare. Never trust the writer's own word.

## Decisions still open

- Whether to ship FCPXML at all. It is the fragile format: its adapter cannot
  write NTSC rates without our patch and reads them back about 4% wrong. If
  Resolve and Premiere both accept AAF, FCPXML may be more liability than value.
- Whether mixed-rate projects should be refused rather than conformed.

## Traps

- **`use_empty_mob_ids=True` produces a file that opens and cannot be relinked.**
  It looks like success.
- **Patch FCPXML through the adapter module**, via
  `otio.adapters.from_name("fcpx_xml").module()`. OTIO loads adapters by path, so
  patching the imported name does nothing and the patch silently has no effect.
- **EDL has no frame rate in it.** Always pass `rate=` when reading one back.
- Media Composer is fussy about **where the media is** relative to the AAF. Test
  both embedded essence and linked media; they fail differently.
- Test at the three-hour mark specifically. Sync errors that are invisible in the
  first minute are obvious there.

## Definition of done

- The acceptance table filled in for Media Composer, Premiere Pro, Resolve and
  Final Cut, at all four rates, with application versions recorded.
- A generated-from-AAF cut confirmed to relink without a dialog in the originating
  project.
- Mixed-rate behaviour documented per NLE, and a decision recorded on whether to
  conform, refuse or warn.
- Any failure turned into either a fix in the projection layer or a written
  decision to drop that format.
- Result recorded in `docs/architecture/02-media-and-interchange.md` and in the
  Spike A README.
