# C2 — The web app on real data

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

Point the ten mockup screens at the real API, and build the two interactions the
product actually sells: reviewing a transcript and editing the cut.

## What already exists

Ten screens, complete and styled, against fixtures in `apps/web/src/lib/`:

- `app/(app)/projects/` — list, detail with assets, new job flow
- `app/(app)/jobs/[id]/` — job detail with stage progress, transcript, edit
- `app/(app)/billing/`, `login/`, `signup/`
- `components/transcript-viewer.tsx` — beats, flags, filters, RTL-aware
- `components/cut-editor.tsx` — reorder, include/exclude, target tracking
- `components/speaker-legend.tsx` — rename speakers, merge across uploads
- `components/job-stages.tsx`, `credit-meter.tsx`, `timecode.tsx`

`packages/shared/src/types.ts` is already the real contract, updated for
multi-asset: `Job.assetIds`, `Beat.assetId`, `Transcript.assets[]`,
`Speaker.assetIds`, and the `assetOf(transcript, beat)` helper.

## What to build

1. Replace the fixture imports with API calls. The types do not change.
2. Real upload (B2) in `new-job-flow.tsx`, with progress and resume.
3. Live job progress from the orchestration events (B3).
4. Persist the cut editor: text-based editing is a product feature, not a
   mockup. The user marks what is in and in what order, and gets an AAF back.
5. Persist speaker renames and merges.
6. Artifact download.

## Decisions already made

- **Direction is per string, not per page.** Every text node uses `dir="auto"`
  and timecodes are `unicode-bidi: isolate`. An English string inside an RTL
  container puts its full stop in the wrong place otherwise — this was found and
  fixed, do not undo it.
- **Timecodes are formatted against the beat's own asset**, never a job-wide
  rate. `assetOf(transcript, beat)` exists so no component reinvents the lookup.
  A project can mix 25 and 23.976, and formatting one against the other produces
  timecodes that look right and do not exist.
- **The transcript page lists beats, not candidate spans.** Stage 6 offers
  several spans per beat; showing them all shows the editor the same material six
  times. A carved row shows what was actually used underneath the full beat.
- **Speakers from different uploads are separate until a human merges them**
  (ADR-0009). The legend offers the merge; the system never guesses.
- Browser storage is not used anywhere, deliberately.

## Decisions still open

- Whether the cut editor edits spans or beats now that selection chooses spans
  (ADR-0010). Letting a user drag a boundary means re-running the silence gate
  in the browser or round-tripping to the API.
- Whether to show the model rationale per beat. It exists in the data.
- Waveform display, which everyone asks for and which conflicts with "the AI
  never touches audio" only in perception, not in fact.

## Traps

- The transcript page emitted by `pipeline/steps/transcript_page.py` is a
  **standalone HTML artifact handed to the customer**, separate from this app.
  Both must stay correct; they share no code today.
- A beat's `startFrames` is local to its own asset. Sorting or displaying across
  assets without `assetId` produces confident nonsense.
- Hebrew is a first-class case, not an edge case. Test every screen with it.

## Definition of done

- Every screen runs against the real API with fixtures deleted from the build.
- A user uploads, submits, watches progress, reviews the transcript, edits the
  cut, and downloads an AAF — without leaving the app.
- Renames, merges and cut edits survive a reload.
- Every screen verified in Hebrew, RTL, with mixed English strings.
