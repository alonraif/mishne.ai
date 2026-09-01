"""Give back a hold that a terminal job left behind. One job at a time, by hand.

A job's credits are held at submission and returned by whichever path ends the
job. When a bug puts a job in a terminal state without taking that path — see
`worker._failed` and `devrunner._fail`, which is where the two holes were — the
customer is left with a `hold` and no matching `release`: their balance shows
credits held against a job that will never run, and no code path will ever
release them, because every releaser is on the path that was skipped.

This is the correction, and it is deliberately not an API endpoint. It runs
once, against one job, with a reason, and it goes through `db.jobs.release` so
the ledger and the projection move together — a hand-edited `org_balances` is a
balance that no longer reconciles to the ledger, which is the one thing
ADR-0006 exists to prevent.

    python tools/release_stranded_hold.py --org org_0d8e6c85 --job job_6b5f2ca7 \
        --reason "artifact insert rejected by ck_artifacts_kind; hold stranded"

Dry run by default; `--apply` commits. The release and the platform-action row
are written in one transaction: a correction nobody can account for later is
not much better than the wrong balance.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa

from mishne.admin import actions
from mishne.admin.db import transaction
from mishne.config import load_env_file
from mishne.db import jobs as job_writes
from mishne.db import models as m

#: Statuses that mean the job is over. A hold on anything else is a job that is
#: still running or still waiting for its editor, and that hold is correct.
TERMINAL = ("failed", "cancelled")


class _DryRun(Exception):
    """Raised to leave the transaction rather than commit it.

    Rolling the session back by hand inside `admin.db.transaction` and then
    letting it exit normally asks the context manager to commit a transaction
    that is no longer there. Leaving by exception is the shape that block was
    written for.
    """


def _ledger_kinds(s, org_id: str, job_id: str) -> set[str]:
    lg = m.CreditLedger.__table__
    return set(
        s.execute(
            sa.select(lg.c.kind).where(
                lg.c.org_id == org_id, lg.c.job_id == job_id
            )
        ).scalars()
    )


def _check(s, org_id: str, job_id: str) -> tuple[float, str]:
    """The cap to release, or an explanation of why there is nothing to do."""
    jobs = m.Job.__table__
    row = s.execute(
        sa.select(jobs.c.status, jobs.c.approved_cap).where(
            jobs.c.org_id == org_id, jobs.c.id == job_id
        )
    ).first()
    if row is None:
        return 0.0, f"no such job in {org_id}"
    if row.status not in TERMINAL:
        return 0.0, (f"job is {row.status}, not {' or '.join(TERMINAL)} — "
                     "a hold on a live job is not stranded")

    kinds = _ledger_kinds(s, org_id, job_id)
    if "hold" not in kinds:
        return 0.0, "no hold was ever placed on this job"
    if "settle" in kinds:
        return 0.0, "this job settled; its hold was already accounted for"
    if "release" in kinds:
        return 0.0, "this job's hold was already released"
    return float(row.approved_cap or 0), ""


def main(argv: list[str] | None = None) -> int:
    load_env_file(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(
        prog="python tools/release_stranded_hold.py",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument("--org", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--reason", required=True,
                        help="why this is being done, for the platform action "
                             "log. Not optional, and not shown to the customer.")
    parser.add_argument("--apply", action="store_true",
                        help="commit. Without it, this reports and rolls back.")
    args = parser.parse_args(argv)

    if not args.reason.strip():
        print("--reason cannot be blank")
        return 2

    try:
        return _run(args)
    except _DryRun:
        print("\n  dry run — nothing committed. Re-run with --apply.")
        return 0


def _run(args) -> int:
    with transaction() as s:
        cap, why_not = _check(s, args.org, args.job)
        if why_not:
            print(f"{args.job}: nothing to release — {why_not}")
            return 1
        before = job_writes.balance(s, args.org)

        job_writes.release(s, args.org, args.job, cap,
                           reason="job failed")
        # `credits.adjusted` rather than `credits.granted`: no credits are being
        # created here. What moves is held → available, back to where the
        # customer's own balance said it would be if the job failed.
        actions.record(
            s, actions.CREDITS_ADJUSTED,
            org_id=args.org,
            target_type="job",
            target_id=args.job,
            reason=args.reason,
            detail={"credits": cap, "correction": "stranded_hold_released"},
        )
        after = job_writes.balance(s, args.org)

        print(f"{args.job}: releasing {cap:g} credits")
        print(f"  available {before[0]:g} → {after[0]:g}")
        print(f"  held      {before[1]:g} → {after[1]:g}")
        if not args.apply:
            raise _DryRun
    print("  committed.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
