"""Submitting a job: the price, the hold, and what happens when it is cancelled.

Money moves through the ledger only (ADR-0006). The balance is a projection of
an append-only table, the hold happens at submission rather than at completion,
and a cancelled job is never charged. Every one of those is a property of the
database rather than of a code path somebody remembers to call, and this is
where that is checked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, PROJECT, VIEWER_USER, mint_session, requires_schema  # noqa: E402

pytestmark = requires_schema

ASSET = "ast_ready_for_a_job"


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
            {
                "a": ASSET, "o": ORG, "p": PROJECT, "c": "d" * 64,
                "probe": '{"codec": "prores", "audio_tracks": 2}',
            },
        )
    yield ASSET
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})
        conn.execute(sa.text("DELETE FROM assets WHERE id = :a"), {"a": ASSET})


def _estimate(http, asset_id: str = ASSET) -> dict:
    resp = http.post(
        "/v1/jobs/estimate", json={"asset_id": asset_id, "target_duration_s": 300}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _submit(http, estimate: dict, **overrides) -> object:
    body = {
        "asset_ids": [ASSET],
        "mode": "ai",
        "notes": "Ten minutes, tight.",
        "target_duration_s": 300,
        "narrative_shape": "inverted_pyramid",
        "approved_cap": estimate["cap"],
        **overrides,
    }
    return http.post("/v1/jobs", json=body)


def _ledger(owner, job_id: str) -> list[tuple[str, float]]:
    with owner.begin() as conn:
        return [
            (r.kind, float(r.delta))
            for r in conn.execute(
                sa.text(
                    "SELECT kind, delta FROM credit_ledger WHERE job_id = :j "
                    "ORDER BY created_at, id"
                ),
                {"j": job_id},
            )
        ]


def _balance(owner) -> tuple[float, float]:
    with owner.begin() as conn:
        row = conn.execute(
            sa.text("SELECT available, held FROM org_balances WHERE org_id = :o"),
            {"o": ORG},
        ).one()
    return float(row.available), float(row.held)


# ────────────────────────────────────────────────────────────────── accepting


def test_submitting_a_job_holds_the_credits_and_plans_the_steps(api, owner, ready_asset):
    http, _ = api
    estimate = _estimate(http)
    before_available, before_held = _balance(owner)

    accepted = _submit(http, estimate)

    assert accepted.status_code == 202, accepted.text
    job = accepted.json()
    assert job["status"] == "queued"

    # Held at submission, not debited at completion: that is what stops a user
    # with five credits starting ten concurrent jobs.
    available, held = _balance(owner)
    assert available == pytest.approx(before_available - estimate["cap"])
    assert held == pytest.approx(before_held + estimate["cap"])
    assert _ledger(owner, job["id"]) == [("hold", -estimate["cap"])]

    # And the job shows its shape before anything has run.
    from mishne.pipeline.steps import ASSET_STEPS, JOB_STEPS

    assert len(job["steps"]) == len(ASSET_STEPS) + len(JOB_STEPS)
    assert all(s["status"] == "pending" for s in job["steps"])


def test_the_price_is_recomputed_and_a_stale_one_is_refused(api, owner, ready_asset):
    http, _ = api
    estimate = _estimate(http)
    before = _balance(owner)

    # The client says the user approved a price we do not agree with. Never
    # trust a client-supplied price; never quietly charge the new one either.
    stale = _submit(http, estimate, approved_cap=estimate["cap"] / 2)

    assert stale.status_code == 409
    assert "price has changed" in stale.json()["detail"]
    assert _balance(owner) == before


def test_a_job_is_refused_when_the_balance_will_not_cover_it(api, owner, ready_asset):
    http, _ = api
    with owner.begin() as conn:
        conn.execute(
            sa.text("UPDATE org_balances SET available = 1 WHERE org_id = :o"), {"o": ORG}
        )
    estimate = _estimate(http)

    refused = _submit(http, estimate)

    assert refused.status_code == 402
    assert _ledger(owner, "") == []


def test_a_job_cannot_be_started_against_an_asset_that_is_not_ready(
    api, owner, ready_asset
):
    http, _ = api
    estimate = _estimate(http)
    with owner.begin() as conn:
        conn.execute(
            sa.text("UPDATE assets SET status = 'awaiting_media' WHERE id = :a"),
            {"a": ASSET},
        )

    # A linked AAF still waiting for its media would transcribe silence.
    refused = _submit(http, estimate)
    assert refused.status_code == 409
    assert "not ready" in refused.json()["detail"]


def test_a_viewer_cannot_start_a_job(api, owner, ready_asset):
    http, _ = api
    estimate = _estimate(http)
    refused = http.post(
        "/v1/jobs",
        json={
            "asset_ids": [ASSET], "mode": "ai", "notes": "", "target_duration_s": 300,
            "approved_cap": estimate["cap"],
        },
        headers={"Authorization": f"Bearer {mint_session(owner, ORG, VIEWER_USER)}"},
    )
    assert refused.status_code == 403


def test_a_job_with_no_assets_is_refused(api, ready_asset):
    http, _ = api
    resp = http.post(
        "/v1/jobs",
        json={"asset_ids": [], "target_duration_s": 300, "approved_cap": 1},
    )
    assert resp.status_code == 422


# ────────────────────────────────────────────────────────────── cancellation


def test_cancelling_releases_the_whole_hold(api, owner, ready_asset):
    http, _ = api
    estimate = _estimate(http)
    before = _balance(owner)
    job_id = _submit(http, estimate).json()["id"]

    cancelled = http.post(f"/v1/jobs/{job_id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    # Nothing is charged for work nobody received.
    assert _balance(owner) == before
    assert _ledger(owner, job_id) == [("hold", -estimate["cap"]), ("release", estimate["cap"])]


def test_cancelling_twice_is_a_conflict_not_a_second_refund(api, owner, ready_asset):
    http, _ = api
    estimate = _estimate(http)
    job_id = _submit(http, estimate).json()["id"]
    assert http.post(f"/v1/jobs/{job_id}/cancel").status_code == 200

    again = http.post(f"/v1/jobs/{job_id}/cancel")

    assert again.status_code == 409
    assert [k for k, _ in _ledger(owner, job_id)].count("release") == 1


def test_cancelling_a_job_that_is_not_yours_is_a_404(api, owner, ready_asset, other_tenant):
    http, _ = api
    estimate = _estimate(http)
    job_id = _submit(http, estimate).json()["id"]

    theirs = http.post(
        f"/v1/jobs/{job_id}/cancel",
        headers={"Authorization": f"Bearer {other_tenant}"},
    )

    assert theirs.status_code == 404


# ──────────────────────────────────────────────────────────── the ledger itself


def test_the_ledger_is_the_balance(api, owner, ready_asset):
    """The projection is reconstructible by summing deltas (ADR-0006)."""
    http, _ = api
    estimate = _estimate(http)
    starting = _balance(owner)[0]
    job_id = _submit(http, estimate).json()["id"]
    http.post(f"/v1/jobs/{job_id}/cancel")

    with owner.begin() as conn:
        total = float(
            conn.execute(
                sa.text("SELECT coalesce(sum(delta), 0) FROM credit_ledger WHERE job_id = :j"),
                {"j": job_id},
            ).scalar_one()
        )
    # A hold and its release net to zero, and the projection agrees. Nothing was
    # updated to make that true: the balance is a sum of rows that only ever
    # get appended.
    assert total == pytest.approx(0)
    assert _balance(owner)[0] == pytest.approx(starting)


def test_a_project_with_billing_history_can_still_be_deleted(api, owner, ready_asset):
    """The bug B3 found: two correct rules that together forbade a delete.

    `credit_ledger.project_id` was a foreign key with ON DELETE SET NULL, so
    deleting a project made Postgres update the ledger — and the append-only
    trigger refused. Any project that had ever been billed for was undeletable,
    which C4's retention work would have hit with a customer's data. Migration
    0004 makes the ledger's ids plain columns.
    """
    http, _ = api
    estimate = _estimate(http)
    job_id = _submit(http, estimate).json()["id"]

    with owner.begin() as conn:
        # Jobs before assets: `job_assets.asset_id` is ON DELETE RESTRICT
        # because an asset a job was cut from must not vanish while the job
        # still refers to it. Then the project itself.
        conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})
        conn.execute(sa.text("DELETE FROM assets WHERE org_id = :o"), {"o": ORG})
        conn.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT})

    # The project and the job are gone; the financial record is not.
    assert [k for k, _ in _ledger(owner, job_id)] == ["hold"]
