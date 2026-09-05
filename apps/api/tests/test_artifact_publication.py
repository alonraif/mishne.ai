"""The deliverables become rows, and the rows are ones the schema accepts.

This is the gap that failed every completed job in C2. `emit.Artifact.fmt` is
the label a person reads — "AAF", "FCPXML" — and `artifacts.kind` is an
identifier with a closed vocabulary of file extensions. While `_publish` wrote
one into the other, the insert was rejected by `ck_artifacts_kind` on the *first*
artifact, after the pipeline had produced and validated all four and uploaded
one of them:

* the exception was an `IntegrityError` raised past the end of `execute`'s
  `try`, so the job's own failure handling never ran;
* the only thing downstream, `devrunner._fail`, set the status and not the
  ledger, so the hold was stranded and the customer's balance stayed wrong.

Two bugs, one visible symptom: `failed`, no artifacts, no refund, on a job whose
every step said `done`. Both are covered here and in `test_devrunner.py`; this
file is the first half — that what `_publish` writes is what the column takes.

The label is deliberately still a separate field, because the artifacts list is
also what the CLI prints and what stage 12 keys its adapter table on. What is
not allowed is one attribute serving as both.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conftest import ORG, PROJECT, requires_schema  # noqa: E402

from mishne.db.vocab import TARGET_NLE  # noqa: E402
from mishne.pipeline.steps.emit import FORMATS, Artifact  # noqa: E402

JOB = "job_artifact_publish"


class FakeRef:
    def __init__(self, key: str) -> None:
        self.key = key


class FakeWorkspace:
    """Stands in for the artifacts bucket. What is under test is the row."""

    def __init__(self) -> None:
        self.uploaded: list[str] = []

    def publish_artifact(self, local: Path, job_id: str, name: str) -> FakeRef:
        self.uploaded.append(name)
        return FakeRef(f"orgs/{ORG}/jobs/{job_id}/artifacts/{name}")


class FakeResult:
    def __init__(self, artifacts: list[Artifact]) -> None:
        self.artifacts = artifacts


def _artifacts(tmp_path: Path) -> list[Artifact]:
    """What stage 11 returns on a run where every writer succeeded."""
    # Indexed rather than unpacked: `FORMATS` gained a column (whether the
    # writer needs a video track) and a positional unpack here broke three
    # tests that have nothing to do with track kinds.
    return [
        Artifact(f[0], tmp_path / f"interview_roughcut.{f[2]}", True, 1024, f[3],
                 kind=f[2])
        for f in FORMATS
    ]


@pytest.fixture
def job(tenant, owner):
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                "notes_raw, brief, estimate, approved_cap) VALUES "
                "(:j, :o, :p, 'assembling', 'manual', '', :brief, :est, 10)"
            ),
            {"j": JOB, "o": ORG, "p": PROJECT,
             "brief": '{"target_duration_s": 600}',
             "est": ('{"mode": "manual", "source_duration_frames": 44258, '
                     '"source_hours": 0.49, "lines": [], "subtotal": 2.72, '
                     '"cap": 3, "balance_before": 5, "balance_after": 2, '
                     '"sufficient": true, "shortfall": 0}')},
        )
    yield JOB
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})


def test_every_emitted_format_has_a_kind_the_column_accepts():
    """No database needed for this one, and it is the whole bug.

    `ArtifactKind`, `ck_artifacts_kind` and `TARGET_NLE` are three spellings of
    one closed vocabulary. Stage 11's formats have to be inside it, and the
    labels never were.
    """
    from mishne.schemas import ArtifactKind

    permitted = set(ArtifactKind.__args__)
    assert {f[2] for f in FORMATS} <= permitted
    assert {f[2] for f in FORMATS} <= set(TARGET_NLE)
    # And the labels are not: this is what was being written.
    assert not {f[0] for f in FORMATS} & permitted


@requires_schema
def test_publishing_writes_a_row_per_deliverable(job, owner, tmp_path):
    from mishne.orchestration import worker

    workspace = FakeWorkspace()
    published = worker._publish(
        FakeResult(_artifacts(tmp_path)), workspace, ORG, JOB, request=None
    )

    assert published == len(FORMATS)
    with owner.begin() as conn:
        rows = conn.execute(
            sa.text("SELECT kind, filename, bytes, validated, s3_key FROM "
                    "artifacts WHERE job_id = :j ORDER BY kind"),
            {"j": JOB},
        ).all()
    assert [r.kind for r in rows] == sorted(f[2] for f in FORMATS)
    # The customer receives a filename, not an id.
    assert all(r.filename.startswith("interview_roughcut.") for r in rows)
    assert all(r.bytes == 1024 and r.validated for r in rows)
    assert all(r.s3_key for r in rows)
    # And the bytes went to the bucket, once each.
    assert len(workspace.uploaded) == len(FORMATS)


@requires_schema
def test_the_rows_read_back_through_the_api_shape(job, owner, tmp_path):
    """`repository.list_artifacts` maps `kind` onto the target NLE, so a row
    written with the label came back with an empty one even where the insert was
    somehow allowed."""
    from sqlalchemy.orm import Session

    from mishne.db import repository
    from mishne.orchestration import worker

    worker._publish(FakeResult(_artifacts(tmp_path)), FakeWorkspace(), ORG, JOB,
                    request=None)

    with Session(owner) as s:
        out = repository.list_artifacts(s, ORG, JOB)
    assert {a.kind for a in out} == {f[2] for f in FORMATS}
    assert all(a.target_nle for a in out)


@requires_schema
def test_a_format_that_failed_to_write_is_not_published(job, owner, tmp_path):
    """Stage 11 captures a writer's failure instead of raising, so a partial run
    is a real state. Three of four is worth delivering; the fourth is not a row."""
    from mishne.orchestration import worker

    artifacts = _artifacts(tmp_path)
    artifacts[1] = Artifact(FORMATS[1][0], None, False, target_nle=FORMATS[1][3],
                            error="RuntimeError: no", kind=FORMATS[1][2])

    published = worker._publish(FakeResult(artifacts), FakeWorkspace(), ORG, JOB,
                                request=None)

    assert published == len(FORMATS) - 1
    with owner.begin() as conn:
        kinds = conn.execute(
            sa.text("SELECT kind FROM artifacts WHERE job_id = :j"), {"j": JOB}
        ).scalars().all()
    assert FORMATS[1][2] not in kinds
