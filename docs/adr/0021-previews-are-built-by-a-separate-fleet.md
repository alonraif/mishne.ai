# ADR-0021 — Previews are built by a separate fleet, fed by a queue

**Status:** Accepted · **Date:** 2026-09-02

## Context

ADR-0020 put the preview transcode beside the pipeline rather than inside it, so
the transcript never waits on ffmpeg. That solved the *latency* coupling and left
the *resource* coupling untouched: on one machine `./dev.sh proxy` is another
process on the same box, and in production that box answers requests.

ffmpeg is not a well-behaved neighbour, and it is not supposed to be. Handed a
three-hour master it will use every core it can for as long as it takes — around
ten minutes at `-preset superfast`. On a laptop that is a warm fan. On an API
instance it is request latency for every tenant on it, a health check that times
out, and an autoscaler that responds to CPU by adding more instances that are
also transcoding. **This cannot be allowed to happen in production**, and the
time to make it impossible is before anything is deployed rather than after the
first incident.

The pieces already point the right way: `build_proxy` was separated from the
loop that finds work. What was missing is anywhere else for it to run, and any
way for the work to reach it.

## Decision

**The transcode runs on its own fleet, and nothing about the code changes to put
it there.**

`orchestration/proxyworker.py` builds one preview for one asset in one process,
in any environment — the exact counterpart of `worker.py` running one job. It is
the entry point of a preview task definition: its own instance class, sized for
CPU, scaled on queue depth, and not reachable from the internet.

`orchestration/proxyrunner.py` is the part that differs by environment. Locally
it polls the table. With `--serve` (or `preview_dispatch=sqs`) it long-polls a
queue. Both call the same `build_proxy`.

**The asset row is the queue of record; a message is only a wake-up.**
`assets.proxy_status = 'pending'` is written in the same transaction as the probe
result, so it cannot be lost and cannot disagree with the asset it belongs to.
Only after that commit does anything publish.

This ordering is the whole design. Publishing first is the classic dual-write
bug — a crash before the commit leaves a message for a row that never wanted
one. Making the message the only record is worse: a dropped message becomes a
preview that never arrives, with nothing anywhere that knows it is owed. Here a
lost message costs one sweep interval, which is why `notify` is allowed to fail
quietly and why `probe` treats a queue that is down as a non-event.

**A claim is a lease, not a status.** `proxy_claimed_at` is stamped by the
conditional `pending -> running` update that already stopped two workers
encoding the same footage. A lease older than `preview_lease_seconds` is evidence
the worker died — a spot instance reclaimed, a task scaled in, a container
OOM-killed — and the row goes back in the queue. Without this, `running` is a
state nothing ever leaves and the symptom is a preview that never arrives and
never says why.

**Retries are bounded.** Reclaiming means a row can be tried again, and
`proxy_attempts` is what stops "again" from becoming "for ever" on media ffmpeg
will never read. Past the threshold the row is abandoned rather than requeued,
because a worker burning CPU on the same unreadable file every few minutes is
exactly the bill that moving the transcode off the API box was meant to make
visible.

## Why a queue and not the state machine

Step Functions already orchestrates jobs and could carry this. It should not.
Previews are deliberately not a pipeline stage (ADR-0020) — nothing downstream
reads one — and putting them in the job graph would re-couple the transcript's
latency to the transcode's, which is the coupling ADR-0020 exists to break. A
queue with its own consumers scales on its own depth and can be throttled,
starved or drained without touching a job.

Lambda was considered and does not fit: a fifteen-minute ceiling against a
transcode that can approach it, on a source that has to be on local disk
(ADR-0013).

## Consequences

**Config decides where work comes from, and a wrong answer fails at boot.**
`preview_dispatch=sqs` without a `preview_queue_url` is refused by `Settings`,
because the alternative is previews that never arrive with nothing in the logs.
The polling mode refuses to run outside `environment=local`, since "find every
pending asset" needs a privileged cross-tenant read that has no business in a
deployed process.

**The sweep is not optional.** It is the only thing that finds a row whose
notification was lost, and the only thing that frees a dead worker's lease.
A fleet without it degrades silently rather than loudly.

**One knob for the co-located case.** `proxy_ffmpeg_threads` caps the cores
ffmpeg may take. Irrelevant on a machine whose whole job is this — the default
of 0 lets it decide — and the difference between a usable and an unusable laptop
when it is not. It is a courtesy, not the mechanism; the mechanism is a
different machine.

**Not built, because nothing is deployed.** The queue, the task definition and
the scaling policy are Terraform that does not exist yet — see
`docs/AWS-MIGRATION.md`. What exists is the seam: the entry point, the dispatch
interface with both implementations, the lease, and the sweep. Standing them up
is configuration and infrastructure, not a change to this code.

**Cost is now attributable.** Preview compute is a separate fleet with a
separate bill, which is what makes the open pricing question in ADR-0020
answerable rather than a guess.
