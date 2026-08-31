"""Naming voices, merging them across uploads, and getting the file out.

Three things the schema, the storage layer and the audit vocabulary have all
been ready for since B1-B2, with nothing in between:

* `speakers.label` / `confirmed` existed and only the seed ever set them;
* `speaker_links` existed and only the seed ever wrote one;
* `storage.presigned_get` and `audit.ARTIFACT_DOWNLOADED` were both defined and
  neither was called, so the deliverable — the entire point of the product —
  could not be downloaded.

The interesting cases are the ones where an id is not what it looks like. A
speaker id in the API is job-relative: a merge's canonical id, or a local id
qualified by its reel. Renaming has to resolve it back to every underlying row,
and a merge has to survive being read back through the same rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, PROJECT, requires_schema  # noqa: E402

pytestmark = requires_schema

# Ids are global primary keys, not scoped by org, and `db/seed.py` owns the
# obvious ones — `art_1` is a seeded artifact in another tenant. Named so it
# cannot collide with anything a re-seed puts back.
ARTIFACT = "art_download_test"
REEL_A = "ast_reel_a"
REEL_B = "ast_reel_b"
JOB = "job_two_reels"


@pytest.fixture
def two_reels(tenant, owner):
    """One job over two uploads, each with its own "T1" — two people until a
    person says otherwise."""
    with owner.begin() as conn:
        for asset_id in (REEL_A, REEL_B):
            conn.execute(
                sa.text(
                    "INSERT INTO assets (id, org_id, project_id, kind, ingest_mode, "
                    "status, filename, bytes, checksum, edit_rate_num, edit_rate_den, "
                    "duration_frames, probe, probed_at) VALUES "
                    "(:a, :o, :p, 'video', 'full_media', 'ready', :f, 1024, :c, "
                    "25, 1, 15000, '{}'::jsonb, now())"
                ),
                {"a": asset_id, "o": ORG, "p": PROJECT, "f": f"{asset_id}.mov",
                 "c": asset_id.ljust(64, "0")},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO transcripts (id, org_id, asset_id, provider, "
                    "provider_model, language) VALUES (:t, :o, :a, 'xai', "
                    "'grok-stt', 'en')"
                ),
                {"t": f"trs_{asset_id}", "o": ORG, "a": asset_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO speakers (id, org_id, asset_id, speaker_id, "
                    "source, default_label, track_index, word_count, speech_ms) "
                    "VALUES (:i, :o, :a, 'T1', 'track', 'Mic 1', 1, 100, 60000)"
                ),
                {"i": f"spk_{asset_id}_T1", "o": ORG, "a": asset_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO beats (id, org_id, transcript_id, asset_id, idx, "
                    "start_frames, end_frames, speaker, text) VALUES "
                    "(:id, :o, :t, :a, 0, 1000, 1200, 'T1', 'a line')"
                ),
                {"id": f"{asset_id}_beat_0", "o": ORG, "t": f"trs_{asset_id}",
                 "a": asset_id},
            )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                "notes_raw, brief, estimate, approved_cap) VALUES "
                "(:j, :o, :p, 'complete', 'ai', '', :brief, :est, 10)"
            ),
            {"j": JOB, "o": ORG, "p": PROJECT,
             "brief": '{"target_duration_s": 300}',
             "est": ('{"mode": "ai", "source_duration_frames": 30000, '
                     '"source_hours": 0.33, "lines": [], "subtotal": 10, '
                     '"cap": 10, "balance_before": 500, "balance_after": 490, '
                     '"sufficient": true, "shortfall": 0}')},
        )
        for idx, asset_id in enumerate((REEL_A, REEL_B)):
            conn.execute(
                sa.text(
                    "INSERT INTO job_assets (org_id, job_id, asset_id, order_idx) "
                    "VALUES (:o, :j, :a, :i)"
                ),
                {"o": ORG, "j": JOB, "a": asset_id, "i": idx},
            )
    yield
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})


def _speakers(http) -> list[dict]:
    resp = http.get(f"/v1/jobs/{JOB}/transcript")
    assert resp.status_code == 200, resp.text
    return resp.json()["speakers"]


# ── naming ────────────────────────────────────────────────────────────────


@requires_schema
def test_two_reels_are_two_voices_until_somebody_says_otherwise(api, two_reels):
    """The safe direction to be wrong in: a merge a person makes is one click,
    and a merge the machine invents puts words in the wrong mouth."""
    http, _ = api
    ids = {s["id"] for s in _speakers(http)}
    assert ids == {"T1", f"T1@{REEL_B}"}


@requires_schema
def test_a_voice_can_be_named(api, owner, two_reels):
    http, _ = api

    resp = http.patch(f"/v1/jobs/{JOB}/speakers/T1", json={"label": "Margret Olsen"})

    assert resp.status_code == 200, resp.text
    named = next(s for s in resp.json()["speakers"] if s["id"] == "T1")
    assert named["label"] == "Margret Olsen"
    assert named["confirmed"] is True


@requires_schema
def test_clearing_a_name_unconfirms_the_voice(api, two_reels):
    """Confirmed-as-nothing is not a state a legend can render honestly."""
    http, _ = api
    http.patch(f"/v1/jobs/{JOB}/speakers/T1", json={"label": "Margret Olsen"})

    resp = http.patch(f"/v1/jobs/{JOB}/speakers/T1", json={"label": ""})

    voice = next(s for s in resp.json()["speakers"] if s["id"] == "T1")
    assert voice["label"] == "" and voice["confirmed"] is False


@requires_schema
def test_renaming_a_merged_voice_names_every_row_underneath_it(
    api, owner, two_reels
):
    """A merged voice is several `speakers` rows and one person. Naming the
    first alone leaves half of them with the old name and nothing on screen
    that explains why."""
    http, _ = api
    http.post(f"/v1/jobs/{JOB}/speakers/merge",
              json={"speaker_ids": ["T1", f"T1@{REEL_B}"]})

    http.patch(f"/v1/jobs/{JOB}/speakers/T1", json={"label": "Margret Olsen"})

    with owner.begin() as conn:
        labels = [
            r.label
            for r in conn.execute(
                sa.text("SELECT label FROM speakers WHERE org_id = :o"), {"o": ORG}
            )
        ]
    assert labels == ["Margret Olsen", "Margret Olsen"]


@requires_schema
def test_naming_a_voice_that_is_not_in_this_job_is_a_404(api, two_reels):
    http, _ = api
    resp = http.patch(f"/v1/jobs/{JOB}/speakers/T9", json={"label": "Nobody"})
    assert resp.status_code == 404


# ── merging ───────────────────────────────────────────────────────────────


@requires_schema
def test_a_merge_makes_two_voices_one_and_adds_up_their_totals(api, two_reels):
    http, _ = api

    resp = http.post(f"/v1/jobs/{JOB}/speakers/merge",
                     json={"speaker_ids": ["T1", f"T1@{REEL_B}"]})

    assert resp.status_code == 200, resp.text
    speakers = resp.json()["speakers"]
    assert len(speakers) == 1
    merged = speakers[0]
    assert merged["id"] == "T1"
    assert sorted(merged["assetIds"] if "assetIds" in merged
                  else merged["asset_ids"]) == sorted([REEL_A, REEL_B])
    # Two reels of the same person is that person's whole word count.
    assert (merged.get("wordCount") or merged.get("word_count")) == 200


@requires_schema
def test_a_merge_keeps_the_name_already_given(api, two_reels):
    """An editor who has named a voice on reel one expects the merge to keep
    that name rather than to pick one."""
    http, _ = api
    http.patch(f"/v1/jobs/{JOB}/speakers/T1", json={"label": "Margret Olsen"})

    resp = http.post(f"/v1/jobs/{JOB}/speakers/merge",
                     json={"speaker_ids": ["T1", f"T1@{REEL_B}"]})

    assert resp.json()["speakers"][0]["label"] == "Margret Olsen"


@requires_schema
def test_a_merge_of_one_voice_is_refused(api, two_reels):
    http, _ = api
    resp = http.post(f"/v1/jobs/{JOB}/speakers/merge", json={"speaker_ids": ["T1"]})
    assert resp.status_code == 422


@requires_schema
def test_a_viewer_cannot_rename_a_voice(api, two_reels, viewer_token):
    """Who said what is an editorial claim, and a viewer does not make those."""
    http, _ = api
    resp = http.patch(
        f"/v1/jobs/{JOB}/speakers/T1",
        json={"label": "Margret Olsen"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


# ── the deliverable ───────────────────────────────────────────────────────


@pytest.fixture
def artifact(owner, two_reels, api):
    """A published artifact, object and row."""
    _http, client = api
    key = f"artifacts/{ORG}/{JOB}/roughcut.aaf"
    client.put_object(Bucket="test-artifacts", Key=key, Body=b"AAF" * 100)
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO artifacts (id, org_id, job_id, kind, filename, "
                "s3_key, bytes, validated) VALUES "
                "(:i, :o, :j, 'aaf', 'interview_roughcut.aaf', :k, 300, true)"
            ),
            {"i": ARTIFACT, "o": ORG, "j": JOB, "k": key},
        )
    yield ARTIFACT
    # The job teardown does not reach this: `artifacts.job_id` is not the row
    # that gets deleted first, and a leftover primary key fails the next test
    # in a way that has nothing to do with what it is testing.
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM artifacts WHERE org_id = :o"), {"o": ORG})


@requires_schema
def test_a_download_url_is_issued_and_names_the_file(api, artifact):
    """Content-Disposition is why this endpoint exists rather than a link to
    the key: a customer receives `interview_roughcut.aaf`, not an opaque id."""
    http, _ = api

    resp = http.get(f"/v1/jobs/{JOB}/artifacts/{artifact}/download")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == "interview_roughcut.aaf"
    assert "interview_roughcut.aaf" in body["url"]
    assert body["expires_in_s"] > 0


@requires_schema
def test_every_download_is_audit_logged(api, owner, artifact):
    """The URL works for anyone holding it until it expires, so who asked for
    one and when is a question that has to have an answer."""
    http, _ = api

    def rows() -> list:
        # Counted before and after rather than asserted absolutely: the audit
        # log is append-only at the database and `purge_org` deliberately does
        # not clear it, so it carries every earlier test in this org too.
        with owner.begin() as conn:
            return conn.execute(
                sa.text(
                    "SELECT resource_id, actor_user_id FROM audit_log "
                    "WHERE org_id = :o AND action = 'artifact.downloaded'"
                ),
                {"o": ORG},
            ).all()

    before = len(rows())
    http.get(f"/v1/jobs/{JOB}/artifacts/{artifact}/download")
    after = rows()

    assert len(after) == before + 1
    assert after[-1].resource_id == artifact
    assert after[-1].actor_user_id


@requires_schema
def test_an_artifact_of_another_job_is_a_404(api, artifact):
    http, _ = api
    resp = http.get(f"/v1/jobs/job_someone_else/artifacts/{artifact}/download")
    assert resp.status_code == 404


@requires_schema
def test_an_artifact_with_no_stored_object_is_a_conflict_not_a_broken_url(
    api, owner, two_reels
):
    """A URL for an object that is not there downloads an S3 error page, which
    is a worse answer than saying so."""
    http, _ = api
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO artifacts (id, org_id, job_id, kind, filename, "
                "bytes, validated) VALUES (:i, :o, :j, 'edl', 'cut.edl', 10, true)"
            ),
            {"i": "art_nokey_test", "o": ORG, "j": JOB},
        )

    resp = http.get(f"/v1/jobs/{JOB}/artifacts/art_nokey_test/download")

    assert resp.status_code == 409


@requires_schema
def test_a_viewer_may_download(api, artifact, viewer_token):
    """A viewer reads and downloads; that is what the role is for."""
    http, _ = api
    resp = http.get(
        f"/v1/jobs/{JOB}/artifacts/{artifact}/download",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 200
