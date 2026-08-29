"""Initial schema: twenty tables, org_id everywhere, RLS on all of them.

Revision ID: 0001
Revises:
Create Date: 2026-08-29

This is the greenfield migration and the only one that gets to be this large.
The schema is designed in docs/architecture/03-platform-and-data.md; dribbling
it out table by table would buy nothing, because there is no previous release to
be compatible with yet.

It is also where the conventions are established. Read migrations/README.md.

Two things about this migration are worth knowing before you copy from it:

* `NOT NULL` without a default is used freely here and is **not** licence to do
  the same in migration #2. The rule it appears to break — no NOT NULL without
  a default — is about adding a column to a table an older release is already
  writing to. Nothing was writing to these tables. From here on, the rule bites.

* Indexes are created `CONCURRENTLY` even though every table is empty and a
  plain `CREATE INDEX` would be instant. That is deliberate: the autocommit
  block is fiddly, it is the thing people get wrong under pressure, and proving
  it works belongs in the migration where it cannot hurt anyone.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB

from conventions import (
    APP_ROLE,
    APPEND_ONLY,
    append_only,
    concurrent_index,
    create_org_table,
    drop_append_only,
    drop_concurrent_index,
)

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)
NOW = sa.text("now()")
CREDITS = sa.Numeric(12, 2)

ORG_TIERS = ("starter", "pro", "studio")
USER_ROLES = ("owner", "member", "viewer")
ASSET_KINDS = ("video", "aaf", "audio")
INGEST_MODES = ("full_media", "aaf_embedded", "audio_only")
ASSET_STATUSES = ("uploading", "probing", "ready", "failed")
JOB_MODES = ("ai", "manual", "hybrid")
JOB_STATUSES = (
    "estimating", "awaiting_approval", "awaiting_edit", "queued", "preparing",
    "transcribing", "analyzing", "selecting", "assembling", "validating",
    "complete", "failed", "cancelled",
)
STEP_STATUSES = ("pending", "active", "done", "failed")
ARTIFACT_KINDS = ("aaf", "fcpxml", "edl", "otio", "json")
SPEAKER_SOURCES = ("track", "diarization")
LEDGER_KINDS = ("purchase", "grant", "hold", "release", "settle", "refund", "adjustment")

# The vocabularies are spelled out again here rather than imported from
# mishne.db.vocab. A migration is a historical record: it has to keep describing
# the schema as it was on this date, and it would stop doing that the moment
# someone appends a job status to the live vocabulary module.


class _Vector(sa.types.UserDefinedType):
    """pgvector's `vector(n)`, spelled locally.

    A migration is a historical record and should not import a type from the
    application, which is free to change under it.
    """

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **_: object) -> str:
        return f"vector({self.dim})"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _ck(name: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    return sa.CheckConstraint(_in(column, values), name=name)


INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_users_org", "users", ["org_id"]),
    ("ix_projects_org_created", "projects", ["org_id", "created_at"]),
    ("ix_assets_org", "assets", ["org_id"]),
    ("ix_assets_project", "assets", ["project_id"]),
    ("ix_source_clips_asset", "source_clips", ["asset_id"]),
    ("ix_jobs_org_created", "jobs", ["org_id", "created_at"]),
    ("ix_jobs_project", "jobs", ["project_id"]),
    ("ix_job_assets_asset", "job_assets", ["asset_id"]),
    ("ix_speaker_links_project", "speaker_links", ["project_id"]),
    ("ix_words_source_clip", "words", ["source_clip_id"]),
    ("ix_beats_asset", "beats", ["asset_id"]),
    ("ix_beat_scores_beat", "beat_scores", ["beat_id"]),
    ("ix_selections_beat", "selections", ["beat_id"]),
    ("ix_selections_asset", "selections", ["asset_id"]),
    ("ix_credit_ledger_org_created", "credit_ledger", ["org_id", "created_at"]),
    ("ix_credit_ledger_project", "credit_ledger", ["project_id"]),
    ("ix_audit_log_org_at", "audit_log", ["org_id", "at"]),
]

# Reverse dependency order for the downgrade.
TABLES = [
    "orgs", "org_balances", "users", "projects", "assets", "source_clips",
    "jobs", "job_assets", "job_steps", "transcripts", "speakers",
    "speaker_links", "words", "beats", "beat_scores", "selections",
    "artifacts", "credit_ledger", "stripe_events", "audit_log",
]


def upgrade() -> None:
    # pgvector, for redundancy clustering on beats. Creating the extension is
    # cheap; the column it enables is empty and unindexed for now.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # The role the application connects as. NOLOGIN: it is a privilege bundle,
    # not a user, so no credential appears in a migration or in version control.
    # A login user is granted membership per environment — infra/local-app-user.sql
    # locally, secrets management in staging and production.
    #
    # It matters that this role is not a superuser and does not have BYPASSRLS.
    # Both of those bypass every policy below without any error, which is the
    # failure mode where the schema looks correct and isolates nothing.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")

    # ─────────────────────────────────────────────────────── tenancy, people

    # The tenant itself. Its primary key IS the tenant key, so it is the one
    # table without an org_id column — and the policy is keyed on `id`.
    create_org_table(
        "orgs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("tier", sa.Text, nullable=False),
        sa.Column("retention_days", sa.Integer, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        _ck("ck_orgs_tier", "tier", ORG_TIERS),
        org_column=False,
        key="id",
    )

    create_org_table(
        "org_balances",
        sa.Column(
            "org_id", sa.Text, sa.ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("available", CREDITS, nullable=False, server_default=sa.text("0")),
        sa.Column("held", CREDITS, nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint("available >= 0", name="ck_org_balances_available_non_negative"),
        sa.CheckConstraint("held >= 0", name="ck_org_balances_held_non_negative"),
        org_column=False,
    )

    create_org_table(
        "users",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("external_id", sa.Text),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        _ck("ck_users_role", "role", USER_ROLES),
        sa.UniqueConstraint("org_id", "email", name="uq_users_org_email"),
    )

    # ───────────────────────────────────────────────────── projects, assets

    create_org_table(
        "projects",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        # Nullable until B4. There is no authenticated identity to attribute a
        # project to yet, and a NOT NULL column nothing can fill is a table
        # nobody can insert into.
        sa.Column("created_by", sa.Text),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("archived_at", TS),
    )

    create_org_table(
        "assets",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "project_id",
            sa.Text,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("ingest_mode", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'uploading'")),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("s3_bucket", sa.Text),
        sa.Column("s3_key", sa.Text),
        sa.Column("bytes", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("checksum", sa.Text),
        # Rational rate, never a float: 24000/1001 is not 23.976, and the
        # difference is a frame every 42 seconds.
        sa.Column("edit_rate_num", sa.Integer, nullable=False),
        sa.Column("edit_rate_den", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("drop_frame", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("start_tc_frames", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("duration_frames", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("probe", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        _ck("ck_assets_kind", "kind", ASSET_KINDS),
        _ck("ck_assets_ingest_mode", "ingest_mode", INGEST_MODES),
        _ck("ck_assets_status", "status", ASSET_STATUSES),
        sa.CheckConstraint("edit_rate_num > 0", name="ck_assets_rate_num_positive"),
        sa.CheckConstraint("edit_rate_den > 0", name="ck_assets_rate_den_positive"),
        # Drop-frame is only defined for the NTSC family. A drop-frame 25 fps
        # asset produces timecode labels that do not exist.
        sa.CheckConstraint(
            "NOT drop_frame OR edit_rate_den = 1001", name="ck_assets_drop_frame_is_ntsc"
        ),
    )

    create_org_table(
        "source_clips",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "asset_id", sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
        ),
        # The relink key. An AAF written without one cannot be relinked and is
        # useless to the editor who receives it.
        sa.Column("mob_id", sa.Text),
        sa.Column("tape_name", sa.Text),
        sa.Column("src_tc_in_frames", sa.BigInteger, nullable=False),
        sa.Column("src_tc_out_frames", sa.BigInteger, nullable=False),
        sa.Column("track_kind", sa.Text),
        sa.Column("track_index", sa.Integer),
        sa.Column("file_path", sa.Text),
    )

    # ─────────────────────────────────────────────────────────────── jobs

    create_org_table(
        "jobs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "project_id",
            sa.Text,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.Text, nullable=False, server_default=sa.text("'ai'")),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("notes_raw", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("brief", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # The estimate as approved, frozen. Recomputing it later against today's
        # tier and balance answers a different question than "what did they
        # agree to?", and the approved figure is a ceiling the customer consented
        # to (ADR-0006).
        sa.Column("estimate", JSONB),
        sa.Column("approved_cap", CREDITS),
        sa.Column("credits_settled", CREDITS),
        # {task: ["provider/model", ...]}, in failover order (ADR-0011).
        sa.Column(
            "model_versions", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        # Vendor cost in cents. Not the customer's credits — different number.
        sa.Column("cost_cents", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("error", JSONB),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        _ck("ck_jobs_mode", "mode", JOB_MODES),
        _ck("ck_jobs_status", "status", JOB_STATUSES),
    )

    create_org_table(
        "job_assets",
        sa.Column("job_id", sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  primary_key=True),
        # RESTRICT, not CASCADE: an asset that a job was cut from cannot be
        # deleted out from under it. The job's artifacts reference source
        # timecodes in that asset and become unrelinkable without it.
        sa.Column("asset_id", sa.Text, sa.ForeignKey("assets.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("order_idx", sa.Integer, nullable=False),
        sa.UniqueConstraint("job_id", "order_idx", name="uq_job_assets_order"),
    )

    create_org_table(
        "job_steps",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("job_id", sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt", sa.Integer, nullable=False, server_default=sa.text("0")),
        # ADR-0012: an in-flight job's steps were written by the previous
        # release and the new one has to read them. Nothing may assume this is
        # the current version.
        sa.Column("payload_version", sa.Integer, nullable=False, server_default=sa.text("1")),
        # References into S3, never payloads — Step Functions caps state at
        # 256 KB and a transcript exceeds it.
        sa.Column("input_ref", sa.Text),
        sa.Column("output_ref", sa.Text),
        sa.Column("detail", sa.Text),
        sa.Column("error", JSONB),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        _ck("ck_job_steps_status", "status", STEP_STATUSES),
        sa.UniqueConstraint("job_id", "idx", name="uq_job_steps_job_idx"),
    )

    # ──────────────────────────────────────────────── transcript and beats

    create_org_table(
        "transcripts",
        sa.Column("id", sa.Text, primary_key=True),
        # Keyed on the ASSET, not the job. Transcription is the expensive step
        # and it belongs to the upload; a job next month reuses it for free.
        sa.Column("asset_id", sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("provider_model", sa.Text, nullable=False),
        sa.Column("language", sa.Text, nullable=False),
        sa.Column("raw_s3_key", sa.Text),
        # The database equivalent of CACHE_VERSION in pipeline/project.py.
        #
        # Beats are a cache of an expensive computation, and this table is where
        # that cache now lives. A row written by older code serves beats built
        # by segmentation rules that no longer exist, and the only symptom is a
        # cut that looks subtly wrong — no error, no log line, a delivered file
        # somebody has to notice by eye. Whatever writes beats compares this
        # against its own version and rebuilds on a mismatch.
        sa.Column("ingest_version", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("attribution", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("asset_id", name="uq_transcripts_asset"),
    )

    create_org_table(
        "speakers",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("asset_id", sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"),
                  nullable=False),
        # Local to the asset: "T1", "S2". Deliberately not unique across a
        # project — the same person on two days is two speakers until a human
        # says otherwise, and speaker_links is where they say it.
        sa.Column("speaker_id", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("default_label", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("confirmed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("track_index", sa.Integer),
        sa.Column("word_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("speech_ms", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        _ck("ck_speakers_source", "source", SPEAKER_SOURCES),
        sa.UniqueConstraint("asset_id", "speaker_id", name="uq_speakers_asset_speaker"),
    )

    create_org_table(
        "speaker_links",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("project_id", sa.Text, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("canonical_speaker_id", sa.Text, nullable=False),
        sa.Column("asset_id", sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("speaker_id", sa.Text, nullable=False),
        sa.Column("confirmed_by", sa.Text),
        sa.Column("confirmed_at", TS),
        sa.UniqueConstraint("project_id", "asset_id", "speaker_id",
                            name="uq_speaker_links_member"),
    )

    create_org_table(
        "words",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("transcript_id", sa.Text,
                  sa.ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_clip_id", sa.Text,
                  sa.ForeignKey("source_clips.id", ondelete="SET NULL")),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        # Milliseconds, and this is the one table where time is not frames: ASR
        # emits time, a word is never a cut point, and the conversion happens
        # once at `structure`.
        sa.Column("start_ms", sa.BigInteger, nullable=False),
        sa.Column("end_ms", sa.BigInteger, nullable=False),
        sa.Column("confidence", sa.REAL, nullable=False, server_default=sa.text("1")),
        sa.Column("speaker", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.UniqueConstraint("transcript_id", "idx", name="uq_words_transcript_idx"),
    )

    create_org_table(
        "beats",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("transcript_id", sa.Text,
                  sa.ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False),
        # Denormalised from the transcript on purpose: a beat's timing is local
        # to its own file and meaningless without knowing which file (ADR-0008).
        sa.Column("asset_id", sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("source_clip_id", sa.Text,
                  sa.ForeignKey("source_clips.id", ondelete="SET NULL")),
        sa.Column("idx", sa.Integer, nullable=False),
        # Frames, in the asset's own rate and timecode origin. Not seconds, and
        # not nanoseconds — a frame at 24000/1001 is 41708333.33... ns, so ns
        # cannot land on a frame boundary and the boundary cannot be recovered.
        sa.Column("start_frames", sa.BigInteger, nullable=False),
        sa.Column("end_frames", sa.BigInteger, nullable=False),
        sa.Column("speaker", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("flags", ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("mean_confidence", sa.REAL, nullable=False, server_default=sa.text("1")),
        # pgvector. Nothing populates it yet and there is deliberately no
        # index: HNSW parameters cannot be chosen against no data, and
        # adding the index later is expand-only.
        sa.Column("embedding", _Vector(1024)),
        sa.UniqueConstraint("transcript_id", "idx", name="uq_beats_transcript_idx"),
        sa.CheckConstraint("end_frames > start_frames", name="ck_beats_positive_duration"),
    )

    # ───────────────────────────────────────────── selection and output

    create_org_table(
        "beat_scores",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("job_id", sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("beat_id", sa.Text, sa.ForeignKey("beats.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("scores", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("composite", sa.REAL),
        sa.Column("depends_on", ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("rationale", sa.Text),
        sa.Column("cluster_id", sa.Text),
        sa.UniqueConstraint("job_id", "beat_id", name="uq_beat_scores_job_beat"),
    )

    create_org_table(
        "selections",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("job_id", sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("beat_id", sa.Text, sa.ForeignKey("beats.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("asset_id", sa.Text, sa.ForeignKey("assets.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("order_idx", sa.Integer, nullable=False),
        # The actual cut points. Not read off the beat, because stage 6 proposes
        # narrower spans carved from one (ADR-0010).
        sa.Column("src_tc_in_frames", sa.BigInteger, nullable=False),
        sa.Column("src_tc_out_frames", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("job_id", "order_idx", name="uq_selections_job_order"),
        sa.UniqueConstraint("job_id", "beat_id", name="uq_selections_job_beat"),
        sa.CheckConstraint("src_tc_out_frames > src_tc_in_frames",
                           name="ck_selections_positive_duration"),
    )

    create_org_table(
        "artifacts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("job_id", sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("s3_key", sa.Text),
        sa.Column("bytes", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("validated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("validation", JSONB),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        _ck("ck_artifacts_kind", "kind", ARTIFACT_KINDS),
        sa.UniqueConstraint("job_id", "kind", name="uq_artifacts_job_kind"),
    )

    # ──────────────────────────────────────────────────── billing and audit

    create_org_table(
        "credit_ledger",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("project_id", sa.Text, sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.Text, sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("delta", CREDITS, nullable=False),
        sa.Column("balance_after", CREDITS, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("stripe_event_id", sa.Text),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        _ck("ck_credit_ledger_kind", "kind", LEDGER_KINDS),
        # Append-only, so the role gets no UPDATE and no DELETE. The trigger
        # below is the belt; this is the braces.
        grants=APPEND_ONLY,
    )
    # Idempotency as a constraint rather than as careful code: a retried settle
    # for the same job is a duplicate-key error, not a second charge (ADR-0006).
    op.create_index(
        "uq_credit_ledger_job_kind",
        "credit_ledger",
        ["job_id", "kind"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )
    append_only("credit_ledger")

    create_org_table(
        "stripe_events",
        # Stripe's own event id. Webhooks are delivered at least once, and this
        # primary key is the dedupe.
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("received_at", TS, nullable=False, server_default=NOW),
        grants=APPEND_ONLY,
    )

    create_org_table(
        "audit_log",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("actor_user_id", sa.Text),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("resource_type", sa.Text, nullable=False),
        sa.Column("resource_id", sa.Text),
        sa.Column("ip", INET),
        sa.Column("user_agent", sa.Text),
        sa.Column("at", TS, nullable=False, server_default=NOW),
        # An audit log the application can rewrite is not an audit log.
        grants=APPEND_ONLY,
    )
    append_only("audit_log")

    # ───────────────────────────────────────────────────────────── indexes
    #
    # CONCURRENTLY, in the autocommit block it requires. See the module
    # docstring for why this is done here on empty tables.
    for name, table, columns in INDEXES:
        concurrent_index(name, table, columns)


def downgrade() -> None:
    """Back to an empty database. Tested by `alembic downgrade base` in CI.

    Policies, triggers and grants are owned by their tables and go with them, so
    this is mostly a drop in reverse dependency order. The extension is
    database-level and is removed explicitly; the role is cluster-level and is
    only revoked — see below.
    """
    for name, table, _ in reversed(INDEXES):
        drop_concurrent_index(name, table)

    drop_append_only("audit_log")
    drop_append_only("credit_ledger")
    op.drop_index("uq_credit_ledger_job_kind", table_name="credit_ledger")

    for table in reversed(TABLES):
        op.drop_table(table)

    # Privileges are revoked; the role itself is left alone on purpose.
    #
    # A role is cluster state, not database state. Dropping it would fail
    # outright whenever another database on the same cluster has granted it
    # anything — which is the normal case on a developer's machine running both
    # a dev and a test database — and a downgrade that fails is worse than a
    # downgrade that leaves a NOLOGIN role holding no privileges at all.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                EXECUTE 'REVOKE ALL ON SCHEMA public FROM {APP_ROLE}';
            END IF;
        END
        $$;
        """
    )
    # Only correct because this migration created it. A downgrade that drops an
    # extension somebody else installed would be a different kind of bug.
    op.execute("DROP EXTENSION IF EXISTS vector")
