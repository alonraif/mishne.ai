"""What one job cost, and where it spent its time.

    python -m mishne.report --org org_7fa2 job_a1b2
    python -m mishne.report --org org_7fa2 --baseline

This is the question both C1 and C3 open by asking, and until now the answer was
"the system does not record that". It does now — `job_steps` carries per-asset
timings and a cache flag, `job_llm_calls` carries one row per model call — and
this is the read side of it.

## Why a module rather than a saved query

Three of the numbers here are wrong if you compute them the obvious way, and a
query somebody pastes into psql will compute them the obvious way:

* **Duration is not `finished_at - started_at`.** A retry overwrites the row.
  `seconds` is the attempt that ended the step, `cumulative_seconds` is all of
  them, and the gap is what the retries cost.
* **A cache hit is not a fast stage.** Averaging cached runs into a per-source-
  hour figure produces a baseline that gets better every time somebody re-runs
  a job, which is exactly backwards.
* **An unpriced model is not a free model.** `priced=false` means the catalog
  had no price, and summing those as zero under-reports spend silently. This
  reports them as a separate count rather than folding them in.

## What it deliberately does not print

Filenames, titles, transcript text — the same rule as logs and traces. A cost
report is read by more people than a log and is pasted into more places.
"""

from __future__ import annotations

import argparse
import sys

import sqlalchemy as sa

from .db import models as m
from .db.base import session_for_org

USD_PER_MICRO = 1 / 1_000_000


def job_breakdown(s, org_id: str, job_id: str) -> dict:
    """Everything recorded about one job's cost and time."""
    steps = m.JobStep.__table__
    calls = m.JobLlmCall.__table__
    jobs = m.Job.__table__

    job = s.execute(
        sa.select(
            jobs.c.status, jobs.c.cost_cents, jobs.c.credits_settled,
            jobs.c.model_versions, jobs.c.started_at, jobs.c.finished_at,
        ).where(jobs.c.org_id == org_id, jobs.c.id == job_id)
    ).first()
    if job is None:
        raise SystemExit(f"no such job in {org_id}")

    step_rows = s.execute(
        sa.select(
            steps.c.idx, steps.c.name, steps.c.asset_id, steps.c.status,
            steps.c.attempt, steps.c.seconds, steps.c.cumulative_seconds,
            steps.c.from_cache, steps.c.model_cost_micros,
        )
        .where(steps.c.org_id == org_id, steps.c.job_id == job_id)
        .order_by(steps.c.idx)
    ).all()

    model_rows = s.execute(
        sa.select(
            calls.c.task, calls.c.provider, calls.c.model,
            sa.func.count().label("calls"),
            sa.func.sum(sa.case((calls.c.ok, 1), else_=0)).label("ok"),
            sa.func.sum(calls.c.cost_micros).label("micros"),
            sa.func.sum(sa.case((calls.c.priced, 0), else_=1)).label("unpriced"),
            sa.func.sum(
                sa.case((calls.c.fell_back_from != "", 1), else_=0)
            ).label("failovers"),
        )
        .where(calls.c.org_id == org_id, calls.c.job_id == job_id)
        .group_by(calls.c.task, calls.c.provider, calls.c.model)
        .order_by(sa.desc("micros"))
    ).all()

    return {"job": job, "steps": step_rows, "models": model_rows}


def render(job_id: str, data: dict) -> list[str]:
    job, steps, models = data["job"], data["steps"], data["models"]
    out = [f"job {job_id} — {job.status}"]

    executed = [r for r in steps if not r.from_cache and r.seconds]
    cached = [r for r in steps if r.from_cache]
    wall = sum(float(r.cumulative_seconds or r.seconds or 0) for r in steps)
    retried = sum(
        float(r.cumulative_seconds or 0) - float(r.seconds or 0) for r in steps
    )

    out.append("")
    out.append("  time")
    for r in steps:
        seconds = float(r.seconds or 0)
        marks = []
        if r.from_cache:
            marks.append("cached")
        if r.attempt and r.attempt > 1:
            marks.append(f"attempt {r.attempt}")
        if r.cumulative_seconds and float(r.cumulative_seconds) > seconds:
            marks.append(f"+{float(r.cumulative_seconds) - seconds:.1f}s retried")
        note = ("  · " + ", ".join(marks)) if marks else ""
        asset = f" [{r.asset_id}]" if r.asset_id else ""
        out.append(f"   {r.idx:>3} {r.name:<16}{asset:<12} {seconds:>8.1f}s{note}")
    out.append(
        f"       {'':<16}{'':<12} {wall:>8.1f}s total"
        + (f", of which {retried:.1f}s was retries" if retried > 0.05 else "")
    )
    if cached:
        # The reason a re-run is fast. Without saying so, the work looks like it
        # vanished (ADR-0016).
        out.append(f"       {len(cached)} of {len(steps)} steps served from cache")

    out.append("")
    out.append("  model spend")
    if not models:
        out.append("   none — this job made no model calls")
        # The heuristic scorer and `--spans none` both produce a real job with
        # a real cut and no spend. Saying "$0.00" for that would be true and
        # useless: it is not evidence that models are cheap.
    total_micros = 0
    for r in models:
        micros = int(r.micros or 0)
        total_micros += micros
        flags = []
        if r.unpriced:
            flags.append(f"{r.unpriced} UNPRICED")
        if r.failovers:
            flags.append(f"{r.failovers} failover")
        if r.ok != r.calls:
            flags.append(f"{r.calls - r.ok} failed")
        note = ("  · " + ", ".join(flags)) if flags else ""
        out.append(
            f"   {r.task:<10} {r.provider}/{r.model:<24} {r.calls:>3} calls  "
            f"${micros * USD_PER_MICRO:>8.4f}{note}"
        )
    out.append(f"   {'total':<10} {'':<25} {'':>3}        ${total_micros * USD_PER_MICRO:>8.4f}")

    out.append("")
    out.append(
        f"  recorded on the job row: {job.cost_cents} cents"
        f" · charged to the customer: {job.credits_settled or 0} credits"
    )
    if total_micros and total_micros < 10_000:
        # Integer cents cannot hold this, which is a real limit of the column
        # rather than a rounding detail — see the note in db/jobs.set_job_cost.
        out.append(
            "  NB: spend is under one cent; `jobs.cost_cents` cannot represent it."
        )
    return out


def transcription_baseline(s, org_id: str) -> list[str]:
    """Cost per source hour for transcription — the GPU-or-CPU number.

    Only executed runs count. A cached transcription took 40ms and transcribed
    nothing, and including it makes the baseline improve every time a job is
    re-run.

    Duration is per asset, which is the only reason `job_steps.asset_id` was
    added: without it the six per-asset stages of a multi-upload job are
    indistinguishable rows and this cannot be computed at all.
    """
    steps, assets = m.JobStep.__table__, m.Asset.__table__
    rows = s.execute(
        sa.select(
            steps.c.seconds,
            assets.c.duration_frames,
            assets.c.edit_rate_num,
            assets.c.edit_rate_den,
        )
        .select_from(steps)
        .join(assets, sa.and_(
            assets.c.id == steps.c.asset_id, assets.c.org_id == steps.c.org_id
        ))
        .where(
            steps.c.org_id == org_id,
            steps.c.name == "transcribe",
            steps.c.status == "done",
            steps.c.from_cache.is_(False),
            steps.c.seconds.is_not(None),
            assets.c.probed_at.is_not(None),
        )
    ).all()

    if not rows:
        return [
            "no executed transcription steps recorded for this org.",
            "",
            "This is not a bug in the query. Until a job runs through the",
            "orchestrator against real material, there is nothing to measure —",
            "and the GPU-or-CPU decision stays a guess.",
        ]

    source_hours = 0.0
    machine_seconds = 0.0
    for r in rows:
        rate = (r.edit_rate_num or 1) / (r.edit_rate_den or 1)
        source_hours += (r.duration_frames or 0) / rate / 3600
        machine_seconds += float(r.seconds)

    ratio = machine_seconds / 3600 / source_hours if source_hours else 0
    return [
        f"transcription, {len(rows)} executed runs",
        f"  source material   {source_hours:>10.2f} hours",
        f"  machine time      {machine_seconds / 3600:>10.2f} hours",
        f"  ratio             {ratio:>10.2f} machine-hours per source hour",
        "",
        "Multiply the ratio by the hourly cost of the instance the worker runs",
        "on to get cost per source hour. That is the number the GPU-or-CPU",
        "decision turns on, and the only input it still needs is a price.",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mishne.report")
    parser.add_argument("job_id", nargs="?", default="")
    parser.add_argument("--org", required=True)
    parser.add_argument(
        "--baseline", action="store_true",
        help="cost per source hour for transcription, across every job",
    )
    args = parser.parse_args(argv)

    with session_for_org(args.org) as s:
        if args.baseline:
            lines = transcription_baseline(s, args.org)
        elif args.job_id:
            lines = render(args.job_id, job_breakdown(s, args.org, args.job_id))
        else:
            parser.error("give a job id, or --baseline")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
