"""A job records the media it went ahead without.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-01

ADR-0014 ended on the rule that a job must refuse to start against an
`awaiting_media` sequence, and it was right about why: a sequence whose media
never arrived would transcribe silence. What it did not anticipate is the
ordinary case where *some* of the media is absent and the rest is the whole
programme. A real export in `samples/` references 776 files and ships 775 — the
one missing is a video reference — and under a flat refusal it can never be
submitted at all.

So the refusal becomes a question the customer can answer, and the answer has
to be durable. A transcript with silent stretches in it must be able to say
which files were not there, months later, without anyone re-deriving it from a
requirement set that has since been satisfied by a later upload.

## Why a column on `jobs` rather than a join back to the requirements

`asset_media_requirements` describes the asset *now*. Uploading the missing file
tomorrow satisfies the row, and then nothing anywhere remembers that this job
ran without it. The gap is a fact about the job, recorded at submission and
never updated — the same reason `model_versions` and `estimate` sit on the job
rather than being recomputed.

## Backward compatibility

Nullable, no default, one column. Release N does not know it exists and its
inserts keep working; `NULL` and `'{}'` both mean "nothing was missing", and
readers treat them alike. The downgrade drops it, which loses the record and
nothing else.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("media_gaps", JSONB(), nullable=True))
    op.execute(
        "COMMENT ON COLUMN jobs.media_gaps IS "
        "'{asset_id: [basename, ...]} — referenced media that had not arrived "
        "when this job was submitted, and which the submitter accepted running "
        "without. A fact about the job as submitted: never updated when the "
        "files later turn up. NULL means nothing was missing.'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN jobs.media_gaps IS NULL")
    op.drop_column("jobs", "media_gaps")
