"""What a project has cost, and warning before the balance runs out.

Workstream C1. `credit_ledger.project_id` has existed since 0001 and the
billing router already offered `?project_id=` on the ledger, described as "per-
project usage falls out of filtering by project_id". It did not. Only `hold`
wrote the column: `settle` and `release` left it NULL, so filtering returned
the holds and nothing else.

The consequences were all in the same direction and all wrong:

* a finished job showed its approved **cap**, not what it was charged
* a cancelled job showed a charge that had been refunded in full
* no project's total ever came back down

The column was populated on exactly one of the three rows that matter, which is
the kind of bug that looks like a missing feature.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, PROJECT, requires_schema  # noqa: E402

pytestmark = requires_schema

ASSET = "ast_for_spend"


@pytest.fixture
def ready_asset(tenant, owner):
    """A probed, ready asset: 10 minutes at 25 fps."""
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO assets (id, org_id, project_id, kind, ingest_mode, "
                "status, filename, bytes, checksum, edit_rate_num, edit_rate_den, "
                "duration_frames, probe, probed_at) VALUES "
                "(:a, :o, :p, 'video', 'full_media', 'ready', 'rushes.mov', 1024, "
                ":c, 25, 1, 15000, cast(:probe as jsonb), now())"
            ),
            {"a": ASSET, "o": ORG, "p": PROJECT, "c": "e" * 64,
             "probe": '{"codec": "prores", "audio_tracks": 2}'},
        )
    yield ASSET
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})
        conn.execute(sa.text("DELETE FROM assets WHERE id = :a"), {"a": ASSET})


def _submit(http) -> str:
    estimate = http.post(
        "/v1/jobs/estimate", json={"asset_id": ASSET, "target_duration_s": 300}
    ).json()
    resp = http.post(
        "/v1/jobs",
        json={
            "asset_ids": [ASSET], "mode": "ai", "notes": "Ten minutes, tight.",
            "target_duration_s": 300, "approved_cap": estimate["cap"],
        },
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


def _spend(http) -> dict:
    resp = http.get("/v1/billing/projects")
    assert resp.status_code == 200, resp.text
    return {row["project_id"]: row for row in resp.json()}


def _credits(http, project_id: str = PROJECT) -> float:
    """This project's net spend right now.

    Every assertion below is on a CHANGE in this number rather than on its
    value, and that is not defensiveness — it is what the schema requires.
    `credit_ledger` is append-only and `conftest.purge_org` deliberately does
    not delete from it (the trigger would refuse), so the rows written by every
    earlier test in the session are still there and still count. A test
    asserting an absolute total is asserting on its own position in the run
    order.
    """
    return _spend(http).get(project_id, {}).get("credits", 0.0)


@requires_schema
def test_a_running_job_shows_its_hold(api, owner, ready_asset):
    """Held credits are genuinely unavailable to the customer, so a project
    with a job in flight is not free — reporting it as free would make the
    page disagree with the balance."""
    http, _ = api
    before = _credits(http)
    jobs_before = _spend(http).get(PROJECT, {}).get("jobs", 0)

    _submit(http)

    assert _credits(http) > before
    assert _spend(http)[PROJECT]["jobs"] == jobs_before + 1


@requires_schema
def test_a_cancelled_job_nets_to_zero(api, owner, ready_asset):
    """The whole hold is released. Before the fix this left the cap on the
    project permanently, because the release carried no project_id."""
    http, _ = api
    before = _credits(http)

    job_id = _submit(http)
    assert http.post(f"/v1/jobs/{job_id}/cancel").status_code in (200, 202, 204)

    # The hold and its release are both still in the ledger — nothing is
    # deleted, ever. They cancel out, which is the whole point.
    assert _credits(http) == pytest.approx(before, abs=0.01)


@requires_schema
def test_a_completed_job_shows_what_it_was_charged_not_its_cap(api, owner, ready_asset):
    """The cap is a ceiling the customer approved, not a price. A project page
    that reports caps overstates every job that came in under estimate — which
    is meant to be most of them."""
    from mishne.db import jobs as job_writes
    from sqlalchemy.orm import Session

    http, _ = api
    before = _credits(http)
    job_id = _submit(http)
    with owner.begin() as conn:
        cap = float(
            conn.execute(
                sa.text("SELECT approved_cap FROM jobs WHERE id = :j"), {"j": job_id}
            ).scalar_one()
        )

    charged = round(cap / 2, 2)
    with Session(owner) as s:
        job_writes.settle(s, ORG, job_id, charged, cap)
        s.commit()

    assert _credits(http) - before == pytest.approx(charged, abs=0.01)
    assert _credits(http) - before < cap


@requires_schema
def test_a_purchase_belongs_to_the_org_not_to_a_project(api, owner, ready_asset):
    """Money coming in is the organisation's. Bucketing it under whichever
    project spends it next would make one project look free and another look
    like it paid for everything."""
    from mishne.db import jobs as job_writes
    from sqlalchemy.orm import Session

    http, _ = api
    _submit(http)
    before = _credits(http)

    with Session(owner) as s:
        # A distinct event id per run: the purchase path is idempotent on it,
        # and a reused id would make this test pass by doing nothing.
        job_writes.purchase(
            s, ORG, 100.0, stripe_event_id=f"evt_spend_{secrets.token_hex(4)}"
        )
        s.commit()

    assert _credits(http) == pytest.approx(before, abs=0.01)


def _ledger_sum(owner) -> float:
    with owner.begin() as conn:
        return float(
            conn.execute(
                sa.text(
                    "SELECT coalesce(sum(delta), 0) FROM credit_ledger "
                    "WHERE org_id = :o"
                ),
                {"o": ORG},
            ).scalar_one()
        )


def _available(owner) -> float:
    with owner.begin() as conn:
        return float(
            conn.execute(
                sa.text("SELECT available FROM org_balances WHERE org_id = :o"),
                {"o": ORG},
            ).scalar_one()
        )


@requires_schema
def test_every_entry_moves_the_balance_by_exactly_its_own_delta(api, owner, ready_asset):
    """The invariant the module docstring claims, asserted rather than assumed.

    `delta` means the change in available credits, everywhere — that is what
    makes summing the ledger reconstruct the balance, and it is what a
    reconciliation against Stripe depends on (C1's definition of done).

    Stated as a CHANGE across one operation, deliberately. The absolute version
    of this test cannot work here and is not worth writing: `credit_ledger` is
    append-only and survives `purge_org`, while `org_balances` is reset by the
    `tenant` fixture on every test — so the org's rows are a true record of many
    tests and its balance is a true record of one, and comparing the two totals
    compares things that were never meant to line up.

    This is the test that was missing. The one that existed asserted that a hold
    and its release net to zero, which was true both before and after the bug,
    while `settle` wrote `-charged` against a `balance_after` that had gone *up*
    by `cap - charged`. Every completed job double-counted its own hold.
    """
    from mishne.db import jobs as job_writes
    from sqlalchemy.orm import Session

    http, _ = api

    # submission: a hold
    before_sum, before_balance = _ledger_sum(owner), _available(owner)
    job_id = _submit(http)
    assert _ledger_sum(owner) - before_sum == pytest.approx(
        _available(owner) - before_balance, abs=0.01
    )

    with owner.begin() as conn:
        cap = float(
            conn.execute(
                sa.text("SELECT approved_cap FROM jobs WHERE id = :j"), {"j": job_id}
            ).scalar_one()
        )

    # completion: a settle, which is the case that was wrong
    before_sum, before_balance = _ledger_sum(owner), _available(owner)
    with Session(owner) as s:
        job_writes.settle(s, ORG, job_id, round(cap / 3, 2), cap)
        s.commit()

    assert _ledger_sum(owner) - before_sum == pytest.approx(
        _available(owner) - before_balance, abs=0.01
    )


@requires_schema
def test_a_settle_is_the_unused_hold_coming_back(api, owner, ready_asset):
    """The narrowest statement of the fix.

    A settle row is POSITIVE: the hold already removed the whole cap, and this
    returns what the job did not use. The charge is the two together —
    `cap - (cap - charged) == charged`.
    """
    from mishne.db import jobs as job_writes
    from sqlalchemy.orm import Session

    http, _ = api
    job_id = _submit(http)
    with owner.begin() as conn:
        cap = float(
            conn.execute(
                sa.text("SELECT approved_cap FROM jobs WHERE id = :j"), {"j": job_id}
            ).scalar_one()
        )
    charged = round(cap / 3, 2)

    with Session(owner) as s:
        job_writes.settle(s, ORG, job_id, charged, cap)
        s.commit()

    with owner.begin() as conn:
        row = conn.execute(
            sa.text(
                "SELECT delta, description FROM credit_ledger "
                "WHERE job_id = :j AND kind = 'settle'"
            ),
            {"j": job_id},
        ).one()

    assert float(row.delta) == pytest.approx(cap - charged, abs=0.01)
    assert float(row.delta) > 0
    # And what the customer actually paid is legible without arithmetic.
    assert f"charged {charged:g}" in row.description


# ── the low-balance warning ───────────────────────────────────────────────


@requires_schema
def test_a_healthy_balance_is_not_warned_about(api, owner, ready_asset):
    http, _ = api
    resp = http.get("/v1/billing/balance/warning")
    assert resp.status_code == 200, resp.text
    assert resp.json()["low"] is False


@requires_schema
def test_a_balance_below_the_floor_is_warned_about(api, owner, ready_asset):
    """An org that has never completed a job has no typical job to scale to,
    so the flat floor applies."""
    http, _ = api
    with owner.begin() as conn:
        conn.execute(
            sa.text("UPDATE org_balances SET available = 3 WHERE org_id = :o"),
            {"o": ORG},
        )

    body = http.get("/v1/billing/balance/warning").json()
    assert body["low"] is True
    assert body["available"] == 3.0
    assert body["typical_job"] is None


@requires_schema
def test_the_threshold_scales_to_what_this_orgs_jobs_actually_cost(api, owner, ready_asset):
    """"Under 10 credits" is trivial for a broadcaster and a permanent nag for
    a hobbyist. An org whose jobs cost 40 credits is low at 50."""
    http, _ = api
    with owner.begin() as conn:
        for n in range(3):
            conn.execute(
                sa.text(
                    "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                    "notes_raw, brief, estimate, approved_cap, credits_settled) "
                    "VALUES (:j, :o, :p, 'complete', 'ai', '', '{}'::jsonb, "
                    "'{}'::jsonb, 50, 40)"
                ),
                {"j": f"job_typical_{n}", "o": ORG, "p": PROJECT},
            )
        conn.execute(
            sa.text("UPDATE org_balances SET available = 50 WHERE org_id = :o"),
            {"o": ORG},
        )

    body = http.get("/v1/billing/balance/warning").json()
    # Fifty credits would clear any flat floor worth setting, and still will not
    # cover two more of this customer's jobs.
    assert body["low"] is True
    assert body["typical_job"] == 40.0
    assert body["threshold"] == 80.0


# ── the same number on the project page as on the billing page ─────────────


def _credits_used(http, project_id: str = PROJECT) -> float:
    """What the project screen prints under the heading."""
    resp = http.get(f"/v1/projects/{project_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["credits_used"]


@requires_schema
def test_the_project_page_and_the_billing_page_report_the_same_spend(
    api, owner, ready_asset
):
    """`Project.credits_used` is `project_spend` for one project, or it is a
    second answer to a question that has one.

    It was a second answer. `list_projects` summed `-delta` over `kind =
    'settle'` alone, and a settle is the *positive* remainder of a hold coming
    back — so the expression evaluated to `charged - cap`: zero for a job that
    used its whole estimate, negative for every job that came in under one, and
    blind to jobs still running. A project whose work had all finished under
    budget reported a negative number of credits used, on the same screen as a
    billing page that had the figure right.
    """
    from mishne.db import jobs as job_writes
    from sqlalchemy.orm import Session

    http, _ = api

    # One job in flight: its hold is money the customer cannot spend, and both
    # pages have to say so.
    _submit(http)
    assert _credits_used(http) == pytest.approx(_credits(http), abs=0.01)

    # And one that finished under its cap, which is the case that went negative.
    job_id = _submit(http)
    with owner.begin() as conn:
        cap = float(
            conn.execute(
                sa.text("SELECT approved_cap FROM jobs WHERE id = :j"), {"j": job_id}
            ).scalar_one()
        )
    with Session(owner) as s:
        job_writes.settle(s, ORG, job_id, round(cap / 4, 2), cap)
        s.commit()

    assert _credits_used(http) == pytest.approx(_credits(http), abs=0.01)
    assert _credits_used(http) > 0


@requires_schema
def test_a_finished_job_under_budget_does_not_read_as_negative_spend(
    api, owner, ready_asset
):
    """The symptom, on its own, because the parity test above would still pass
    if both numbers were wrong in the same way."""
    from mishne.db import jobs as job_writes
    from sqlalchemy.orm import Session

    http, _ = api
    before = _credits_used(http)
    job_id = _submit(http)
    with owner.begin() as conn:
        cap = float(
            conn.execute(
                sa.text("SELECT approved_cap FROM jobs WHERE id = :j"), {"j": job_id}
            ).scalar_one()
        )
    charged = round(cap / 2, 2)
    with Session(owner) as s:
        job_writes.settle(s, ORG, job_id, charged, cap)
        s.commit()

    assert _credits_used(http) - before == pytest.approx(charged, abs=0.01)
