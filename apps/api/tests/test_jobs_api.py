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


def _estimate(http, asset_id: str = ASSET, **overrides) -> dict:
    resp = http.post(
        "/v1/jobs/estimate",
        json={"asset_id": asset_id, "target_duration_s": 300, **overrides},
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


def _awaiting(owner, *, outstanding: list[str], satisfied: list[str] = ()) -> None:
    """Put the asset in `awaiting_media` with the requirement rows to match.

    The rows are the point: `awaiting_media` alone says a file is wanted, and
    only `asset_media_requirements` says which — and how much of the sequence
    each one unblocks, which is what decides whether there is anything left to
    transcribe.
    """
    with owner.begin() as conn:
        conn.execute(
            sa.text("UPDATE assets SET status = 'awaiting_media' WHERE id = :a"),
            {"a": ASSET},
        )
        rows = [(n, None) for n in outstanding] + [(n, ASSET) for n in satisfied]
        for idx, (basename, by) in enumerate(rows):
            conn.execute(
                sa.text(
                    "INSERT INTO asset_media_requirements (id, org_id, asset_id, "
                    "basename, match_key, clip_count, satisfied_by_asset_id, "
                    "satisfied_at) VALUES (:i, :o, :a, :b, :k, 1, "
                    "cast(:by as text), "
                    "CASE WHEN cast(:by as text) IS NULL THEN NULL ELSE now() END)"
                ),
                {"i": f"req_{idx}", "o": ORG, "a": ASSET,
                 "b": basename, "k": basename.lower(), "by": by},
            )


def test_a_sequence_waiting_for_media_says_which_files_it_wants(
    api, owner, ready_asset
):
    """A refusal the customer can act on names the files.

    "asset ast_… is not ready to cut" is a complaint. "A002.wav has not
    arrived" is an instruction, and it is the same list the requirements panel
    is already showing them.
    """
    http, _ = api
    estimate = _estimate(http)
    _awaiting(owner, outstanding=["B002.wav"], satisfied=["A001.wav"])

    refused = _submit(http, estimate)

    assert refused.status_code == 409
    assert "B002.wav" in refused.json()["detail"]


def test_a_cut_can_go_ahead_without_media_that_never_arrived(
    api, owner, ready_asset
):
    """The Habatim case: 776 files referenced, 775 present, one video absent.

    Refusing this outright — which is what ADR-0014 originally said — means a
    real export can never be submitted at all. The gap is recorded on the job,
    because a transcript with silence in it has to be able to say why.
    """
    http, _ = api
    estimate = _estimate(http)
    _awaiting(owner, outstanding=["B002.wav"], satisfied=["A001.wav"])

    accepted = _submit(http, estimate, accept_missing_media=True)

    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["media_gaps"] == {ASSET: ["B002.wav"]}
    with owner.begin() as conn:
        stored = conn.execute(
            sa.text("SELECT media_gaps FROM jobs WHERE id = :j"), {"j": body["id"]}
        ).scalar_one()
    assert stored == {ASSET: ["B002.wav"]}


def test_a_cut_with_no_media_at_all_is_refused_however_hard_you_ask(
    api, owner, ready_asset
):
    """The thing ADR-0014 was right about, and the floor under the flag.

    A sequence that resolved none of its media transcribes silence. No
    acknowledgement should be able to buy that, because the customer cannot
    know it is what they are buying.
    """
    http, _ = api
    estimate = _estimate(http)
    _awaiting(owner, outstanding=["A001.wav", "B002.wav"])

    refused = _submit(http, estimate, accept_missing_media=True)

    assert refused.status_code == 422
    assert "nothing to transcribe" in refused.json()["detail"]


def test_an_ordinary_job_records_no_gap(api, owner, ready_asset):
    """`media_gaps` is empty for the jobs that are not this feature."""
    http, _ = api
    estimate = _estimate(http)

    accepted = _submit(http, estimate)

    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["media_gaps"] == {}


def test_a_transcription_job_needs_no_target_length(api, owner, ready_asset):
    """It is a selection parameter, and selection does not run.

    It also does not move the price — cost scales with source duration, not cut
    length — and nothing reads it before the cut editor, which is the first
    place anybody could answer it honestly. So the wizard stops asking and
    sends 0, and 0 has to survive submission rather than being defaulted.
    """
    http, _ = api
    # Priced as the mode it will be submitted as: transcription costs less
    # because the LLM stages do not run (ADR-0007), and a cap from the wrong
    # mode is refused — correctly — by the price check.
    estimate = _estimate(http, mode="manual", target_duration_s=0)

    accepted = _submit(http, estimate, mode="manual", target_duration_s=0)

    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["brief"]["target_duration_s"] == 0


def test_a_cut_that_gets_made_for_you_still_needs_a_target(api, owner, ready_asset):
    """`ai` and `hybrid` hand the number to a solver, which cannot choose none.

    Refused at the edge rather than defaulted quietly three stages later, where
    the customer would be charged for a cut against a length they never chose.
    """
    http, _ = api
    estimate = _estimate(http)

    for mode in ("ai", "hybrid"):
        refused = _submit(http, estimate, mode=mode, target_duration_s=0)
        assert refused.status_code == 422, f"{mode}: {refused.text}"
        assert "target length is required" in refused.text


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


# ── the language reaches the transcriber ───────────────────────────────────
#
# `asr/routing.py` decides the engine from the language and treats "not stated"
# as "not identified", which only the general-coverage engine may take. That is
# the right rule and it was being fed nothing: the brief this router writes had
# no `language` key, `worker.prepare_request` read `brief.get("language")` back
# as None, and so every job in the product — English rushes included — was
# routed to the engine that costs three times as much. The rule was never
# wrong; nothing was ever asked.


def _brief_of(owner, job_id: str) -> dict:
    with owner.begin() as conn:
        return conn.execute(
            sa.text("SELECT brief FROM jobs WHERE id = :j"), {"j": job_id}
        ).scalar_one()


def test_the_declared_language_is_stored_where_the_worker_reads_it(
    api, owner, ready_asset
):
    http, _ = api
    accepted = _submit(http, _estimate(http), language="he")
    assert accepted.status_code == 202, accepted.text

    brief = _brief_of(owner, accepted.json()["id"])
    assert brief["language"] == "he"

    # The hop that matters: this dict is what `prepare_request` turns into
    # `JobRequest.language`, and that is the argument `routing.plan` routes on.
    from mishne.asr import routing

    assert [
        e.provider for e in routing.plan(brief["language"], have=["xai", "google"])
    ] == ["google"]


def test_an_unstated_language_defaults_to_english_and_the_cheap_engine(
    api, owner, ready_asset
):
    http, _ = api
    accepted = _submit(http, _estimate(http))
    assert accepted.status_code == 202, accepted.text

    from mishne.asr import routing

    assert _brief_of(owner, accepted.json()["id"])["language"] == "en"
    assert routing.plan("en", have=["xai", "google"])[0].provider == "xai"


def test_a_language_is_normalised_so_two_spellings_route_alike(
    api, owner, ready_asset
):
    http, _ = api
    for sent, stored in (("EN", "en"), ("pt-br", "pt-BR"), (" he ", "he")):
        accepted = _submit(http, _estimate(http), language=sent)
        assert accepted.status_code == 202, accepted.text
        assert _brief_of(owner, accepted.json()["id"])["language"] == stored


def test_a_language_that_is_not_a_code_is_refused_at_submission(api, ready_asset):
    http, _ = api
    refused = _submit(http, _estimate(http), language="Hebrew, I think")
    assert refused.status_code == 422, refused.text


# ──────────────────────────────────────────────────────────────── the name


def test_a_job_is_called_what_the_customer_called_it(api, ready_asset):
    """The name is stored and read back, not derived from anything.

    Before this, a job's only label was its primary key, and a project holding
    four cuts of one interview showed four rows of `job_8a98a1ca`.
    """
    http, _ = api
    accepted = _submit(http, _estimate(http), name="Ep. 3 — web cut")
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["name"] == "Ep. 3 — web cut"

    read_back = http.get(f"/v1/jobs/{accepted.json()['id']}")
    assert read_back.json()["name"] == "Ep. 3 — web cut"


def test_an_unnamed_job_is_named_after_its_source_not_after_its_id(api, ready_asset):
    """An API client with nothing to say still gets a readable row.

    The fallback is deliberately the source filename rather than the job id:
    the column exists precisely so that no screen has to print the id, and a
    default of `job_8a98a1ca` would reintroduce the thing it replaced.
    """
    http, _ = api
    accepted = _submit(http, _estimate(http), name="")
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    # `rushes.mov`, without the extension — see the `ready_asset` fixture.
    assert body["name"] == "rushes"
    assert body["id"] not in body["name"]


def test_a_name_is_trimmed_to_one_line_and_a_long_one_is_refused(api, ready_asset):
    """It is rendered in a list row, so it may not be a paragraph."""
    http, _ = api
    accepted = _submit(http, _estimate(http), name="  Ep. 3\n  web  cut  ")
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["name"] == "Ep. 3 web cut"

    refused = _submit(http, _estimate(http), name="x" * 121)
    assert refused.status_code == 422, refused.text


# ──────────────────────────────────────────────────────────── renaming it


def test_a_job_can_be_renamed_after_it_was_created(api, owner, ready_asset):
    """A name is chosen before the work is seen, and judged after it.

    Allowed at any status: the name is a label, not an identifier — nothing
    downstream is keyed on it, and a finished job is exactly when somebody
    notices that "Untitled" was a poor choice.
    """
    http, _ = api
    job_id = _submit(http, _estimate(http), name="Ep. 3").json()["id"]

    renamed = http.patch(f"/v1/jobs/{job_id}", json={"name": "Ep. 3 — web cut"})

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Ep. 3 — web cut"
    assert http.get(f"/v1/jobs/{job_id}").json()["name"] == "Ep. 3 — web cut"
    with owner.begin() as conn:
        actions = [
            r.action
            for r in conn.execute(
                sa.text("SELECT action FROM audit_log WHERE resource_id = :j"),
                {"j": job_id},
            )
        ]
    assert "job.renamed" in actions


def test_a_rename_obeys_the_rule_submission_obeys(api, ready_asset):
    """One rule for both, or a job can be renamed to something that would
    never have been accepted in the first place."""
    http, _ = api
    job_id = _submit(http, _estimate(http)).json()["id"]

    trimmed = http.patch(f"/v1/jobs/{job_id}", json={"name": "  Ep. 3\n  web  cut  "})
    assert trimmed.status_code == 200, trimmed.text
    assert trimmed.json()["name"] == "Ep. 3 web cut"

    assert http.patch(f"/v1/jobs/{job_id}", json={"name": "x" * 121}).status_code == 422


def test_a_job_cannot_be_renamed_to_nothing(api, ready_asset):
    """`jobs.name` is NOT NULL and exists so no screen prints the id. Clearing
    it would put `job_8a98a1ca` back on the row it replaced — and submission
    has a source filename to fall back on where this has nothing."""
    http, _ = api
    job_id = _submit(http, _estimate(http), name="Ep. 3").json()["id"]

    assert http.patch(f"/v1/jobs/{job_id}", json={"name": "   "}).status_code == 422
    assert http.get(f"/v1/jobs/{job_id}").json()["name"] == "Ep. 3"


def test_a_viewer_cannot_rename_a_job(api, owner, ready_asset):
    http, _ = api
    job_id = _submit(http, _estimate(http), name="Ep. 3").json()["id"]

    refused = http.patch(
        f"/v1/jobs/{job_id}",
        json={"name": "Mine now"},
        headers={"Authorization": f"Bearer {mint_session(owner, ORG, VIEWER_USER)}"},
    )

    assert refused.status_code == 403
    assert http.get(f"/v1/jobs/{job_id}").json()["name"] == "Ep. 3"


def test_renaming_a_job_that_is_not_yours_is_a_404(api, ready_asset, other_tenant):
    http, _ = api
    job_id = _submit(http, _estimate(http), name="Ep. 3").json()["id"]

    theirs = http.patch(
        f"/v1/jobs/{job_id}",
        json={"name": "Mine now"},
        headers={"Authorization": f"Bearer {other_tenant}"},
    )

    assert theirs.status_code == 404
    assert http.get(f"/v1/jobs/{job_id}").json()["name"] == "Ep. 3"
