"""SQLAlchemy models — the schema in docs/architecture/03-platform-and-data.md.

Three things about this file are load-bearing and easy to undo by accident.

**`org_id text NOT NULL` on every table, including where a join could derive
it.** It makes the RLS policy identical on all twenty tables, and it removes any
path where a forgotten join condition leaks across tenants. A NULL org_id slips
past a naive policy, so the column is NOT NULL and has no default.

**Time is stored the way the format that has to survive stores it.** Anything
that becomes a cut point is in FRAMES, alongside the asset's rational rate
(`edit_rate_num`/`edit_rate_den`) and its drop-frame flag. Seconds — or
nanoseconds, which cannot represent 1001/24000 exactly — lose the frame boundary
and it cannot be recovered. `words` is the one exception: ASR emits milliseconds,
a word is never a cut point, and the conversion happens once at `structure`.

**Closed vocabularies are `text` + CHECK, never a native ENUM.** See vocab.py.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB

from . import vocab
from .base import Base
from .types import Vector

TS = sa.TIMESTAMP(timezone=True)
NOW = sa.text("now()")
CREDITS = sa.Numeric(12, 2)


def _ck(name: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    return sa.CheckConstraint(vocab.check(column, values), name=name)


# ───────────────────────────────────────────────────────────── tenancy, people


class Org(Base):
    """The tenant. Its own `id` is the RLS key — it has no org_id column."""

    __tablename__ = "orgs"
    id = sa.Column(sa.Text, primary_key=True)
    name = sa.Column(sa.Text, nullable=False)
    # `tier`, not `plan`: TierId in packages/shared, schemas.py, billing/credits.py
    # and every fixture already say tier, and a rename after migration #1 is
    # forbidden. docs/architecture/03 says plan and is the odd one out.
    tier = sa.Column(sa.Text, nullable=False)
    retention_days = sa.Column(sa.Integer, nullable=False)
    created_at = sa.Column(TS, nullable=False, server_default=NOW)
    __table_args__ = (_ck("ck_orgs_tier", "tier", vocab.ORG_TIERS),)


class OrgBalance(Base):
    """The materialised projection of the ledger.

    ADR-0006: balance is a projection, never a mutable source of truth. This row
    exists because every screen shows a balance and replaying the whole ledger
    per request is absurd — it is written in the same transaction as the ledger
    insert, and it is reconstructible by summing `credit_ledger.delta`.
    """

    __tablename__ = "org_balances"
    org_id = sa.Column(sa.Text, sa.ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True)
    available = sa.Column(CREDITS, nullable=False, server_default=sa.text("0"))
    held = sa.Column(CREDITS, nullable=False, server_default=sa.text("0"))
    updated_at = sa.Column(TS, nullable=False, server_default=NOW)
    __table_args__ = (
        sa.CheckConstraint("available >= 0", name="ck_org_balances_available_non_negative"),
        sa.CheckConstraint("held >= 0", name="ck_org_balances_held_non_negative"),
    )


class User(Base):
    __tablename__ = "users"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    email = sa.Column(sa.Text, nullable=False)
    name = sa.Column(sa.Text, nullable=False, server_default=sa.text("''"))
    # The IdP's subject. Null until B4 wires real identity.
    external_id = sa.Column(sa.Text)
    role = sa.Column(sa.Text, nullable=False)
    # Which mechanism authenticated this user. Nullable and added after the
    # fact, so a release that knows nothing about it keeps inserting (ADR-0012).
    auth_provider = sa.Column(sa.Text)
    created_at = sa.Column(TS, nullable=False, server_default=NOW)
    __table_args__ = (
        _ck("ck_users_role", "role", vocab.USER_ROLES),
        sa.CheckConstraint(
            "auth_provider IS NULL OR "
            + vocab.check("auth_provider", vocab.AUTH_PROVIDERS),
            name="ck_users_auth_provider",
        ),
        sa.UniqueConstraint("org_id", "email", name="uq_users_org_email"),
    )


# ──────────────────────────────────────────────────────────── projects, assets


class UserCredential(Base):
    """How a user proves who they are, when the answer is a password.

    Separate from `users` because a password is not an attribute of a person but
    one way of authenticating them: an org on SSO has users with no row here at
    all, and revoking a credential must not delete an account.

    `password_hash` is the full encoded form — algorithm, parameters, salt and
    digest — so the cost parameters can be raised later without invalidating
    every existing password.
    """

    __tablename__ = "user_credentials"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    user_id = sa.Column(
        sa.Text, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    password_hash = sa.Column(sa.Text, nullable=False)
    updated_at = sa.Column(TS, nullable=False, server_default=NOW)
    __table_args__ = (sa.UniqueConstraint("user_id", name="uq_user_credentials_user"),)


class Session(Base):
    """One signed-in browser.

    The token itself is never stored — only its SHA-256. A dump of this table is
    then a list of session ids rather than a set of working credentials, for the
    same reason a password is not stored either.

    Sessions are revoked rather than deleted: "when did this session end, and
    was it a logout or an expiry" is a question a security review asks.
    """

    __tablename__ = "sessions"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    user_id = sa.Column(
        sa.Text, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash = sa.Column(sa.Text, nullable=False)
    created_at = sa.Column(TS, nullable=False, server_default=NOW)
    last_seen_at = sa.Column(TS, nullable=False, server_default=NOW)
    expires_at = sa.Column(TS, nullable=False)
    revoked_at = sa.Column(TS)
    __table_args__ = (sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),)


class Project(Base):
    __tablename__ = "projects"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    name = sa.Column(sa.Text, nullable=False)
    # Nullable until B4: there is no authenticated identity to attribute a
    # project to yet, and a NOT NULL column nothing can fill is a table nobody
    # can insert into.
    created_by = sa.Column(sa.Text)
    created_at = sa.Column(TS, nullable=False, server_default=NOW)
    archived_at = sa.Column(TS)


class Asset(Base):
    """One upload. Carries its own rate, its own start timecode, its own length.

    There is no project-wide rate and no global timeline (ADR-0008). Two assets
    in one job routinely differ — 25 in the studio, 23.976 on location — and
    every timecode shown in the UI is formatted against the asset the beat came
    from, never against a job-wide rate.
    """

    __tablename__ = "assets"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    project_id = sa.Column(
        sa.Text, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind = sa.Column(sa.Text, nullable=False)
    ingest_mode = sa.Column(sa.Text, nullable=False)
    status = sa.Column(sa.Text, nullable=False, server_default=sa.text("'uploading'"))
    # The name the customer uploaded. Not derivable from s3_key, which is an id.
    filename = sa.Column(sa.Text, nullable=False)
    s3_bucket = sa.Column(sa.Text)
    s3_key = sa.Column(sa.Text)
    bytes = sa.Column(sa.BigInteger, nullable=False, server_default=sa.text("0"))
    checksum = sa.Column(sa.Text)
    # Rational, never a float. 24000/1001 is not 23.976, and the difference is a
    # frame every 42 seconds. See src/mishne/timecode.py.
    edit_rate_num = sa.Column(sa.Integer, nullable=False)
    edit_rate_den = sa.Column(sa.Integer, nullable=False, server_default=sa.text("1"))
    drop_frame = sa.Column(sa.Boolean, nullable=False, server_default=sa.false())
    start_tc_frames = sa.Column(sa.BigInteger, nullable=False, server_default=sa.text("0"))
    duration_frames = sa.Column(sa.BigInteger, nullable=False, server_default=sa.text("0"))
    # ffprobe / AAF structure, verbatim. codec and audio_tracks are read from
    # here; promoting them to columns would be two more things to keep in step
    # with a probe result that already has them.
    probe = sa.Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    # The in-flight multipart upload, so an explicit cancel can abort it and
    # stop paying for parts nobody can see. Cleared at completion. The lifecycle
    # rule in infra/s3_lifecycle.py is the backstop for the ones nobody cancels.
    upload_id = sa.Column(sa.Text)
    # Why probing failed. Nullable and added after the fact, so an older release
    # that knows nothing about it keeps inserting happily (ADR-0012).
    error = sa.Column(JSONB)
    probed_at = sa.Column(TS)
    created_at = sa.Column(TS, nullable=False, server_default=NOW)
    __table_args__ = (
        _ck("ck_assets_kind", "kind", vocab.ASSET_KINDS),
        _ck("ck_assets_ingest_mode", "ingest_mode", vocab.INGEST_MODES),
        _ck("ck_assets_status", "status", vocab.ASSET_STATUSES),
        sa.CheckConstraint("edit_rate_num > 0", name="ck_assets_rate_num_positive"),
        sa.CheckConstraint("edit_rate_den > 0", name="ck_assets_rate_den_positive"),
        # Drop-frame is only defined for the 30/60-family NTSC rates. A
        # drop-frame 25 fps asset is a bug upstream, and it produces timecodes
        # that do not exist.
        sa.CheckConstraint(
            "NOT drop_frame OR edit_rate_den = 1001",
            name="ck_assets_drop_frame_is_ntsc",
        ),
    )


class SourceClip(Base):
    """What an asset resolves to on the editor's disk. The relink key.

    `mob_id` is how Media Composer finds the media again; an AAF written without
    one cannot be relinked and is useless to the person who receives it.
    """

    __tablename__ = "source_clips"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    asset_id = sa.Column(sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    mob_id = sa.Column(sa.Text)
    tape_name = sa.Column(sa.Text)
    src_tc_in_frames = sa.Column(sa.BigInteger, nullable=False)
    src_tc_out_frames = sa.Column(sa.BigInteger, nullable=False)
    track_kind = sa.Column(sa.Text)
    track_index = sa.Column(sa.Integer)
    file_path = sa.Column(sa.Text)


class AssetMediaRequirement(Base):
    """A file a linked AAF references and does not contain.

    An AAF is a sequence, not a container. An *embedded* one carries its essence
    inside itself and is self-sufficient; a *linked* one is a few hundred
    kilobytes of pointers at media sitting on an editor's SAN, and uploading it
    alone gets you a transcript of silence. `aaf_ingest.parse` already notices
    which clips it cannot resolve — this table is that finding, made durable, so
    the UI can ask for exactly the missing files and the job can refuse to start
    until they arrive.

    Matching is on **basename**, because that is the only thing that survives
    the trip: the absolute path inside the AAF describes a filesystem we will
    never see, and `aaf_ingest._url_to_path` already falls back to a
    same-directory basename match for exactly this reason. Materialising the
    companions beside the AAF is therefore all the resolution that is needed —
    the parser does the rest, unchanged.

    A basename is not unique in principle. Two source files really can both be
    called `A001.mxf`, and the honest answer is that the AAF gives us nothing
    better to key on. `mob_id` is recorded so that a stricter check is possible
    later without a second migration.
    """

    __tablename__ = "asset_media_requirements"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    asset_id = sa.Column(sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    # The referenced file's basename, as the AAF spells it.
    basename = sa.Column(sa.Text, nullable=False)
    # Case- and separator-normalised, and the column the match is actually made
    # on. Stored rather than computed per query so the unique constraint and the
    # index agree with the lookup — a functional index and a hand-written
    # `lower()` drift apart the first time somebody edits one of them.
    match_key = sa.Column(sa.Text, nullable=False)
    mob_id = sa.Column(sa.Text)
    clip_name = sa.Column(sa.Text)
    # How many clips on the timeline need this file. Drives the ordering of the
    # "still needed" list: the file that unblocks forty clips is the one worth
    # asking for first.
    clip_count = sa.Column(sa.Integer, nullable=False, server_default=sa.text("1"))
    satisfied_by_asset_id = sa.Column(sa.Text, sa.ForeignKey("assets.id", ondelete="SET NULL"))
    satisfied_at = sa.Column(TS)
    __table_args__ = (
        sa.UniqueConstraint("asset_id", "match_key", name="uq_asset_media_req"),
    )


# ────────────────────────────────────────────────────────────────────── jobs


class Job(Base):
    __tablename__ = "jobs"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    project_id = sa.Column(
        sa.Text, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # ai | manual | hybrid. Absent from the architecture doc's table list, but it
    # decides which of stages 5-8 run and it changes the price.
    mode = sa.Column(sa.Text, nullable=False, server_default=sa.text("'ai'"))
    status = sa.Column(sa.Text, nullable=False)
    notes_raw = sa.Column(sa.Text, nullable=False, server_default=sa.text("''"))
    brief = sa.Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    # The estimate as the user approved it, frozen. Recomputing it later against
    # today's tier and balance answers a different question than "what did they
    # agree to?", and the approved figure is a contract.
    estimate = sa.Column(JSONB)
    approved_cap = sa.Column(CREDITS)
    credits_settled = sa.Column(CREDITS)
    # {task: ["provider/model", ...]} — every model per task, in failover order.
    # The reproducibility contract: without it "why did the output change?" has
    # no answer (ADR-0011).
    model_versions = sa.Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    # Vendor cost, in cents. NOT the customer's credits — different number,
    # different currency, different purpose. Unit economics live here.
    cost_cents = sa.Column(sa.Integer, nullable=False, server_default=sa.text("0"))
    error = sa.Column(JSONB)
    created_at = sa.Column(TS, nullable=False, server_default=NOW)
    started_at = sa.Column(TS)
    finished_at = sa.Column(TS)
    __table_args__ = (
        _ck("ck_jobs_mode", "mode", vocab.JOB_MODES),
        _ck("ck_jobs_status", "status", vocab.JOB_STATUSES),
    )


class JobAsset(Base):
    """The join that makes a project a project (ADR-0008).

    A job draws on many assets and an asset feeds many jobs, and both directions
    are real: one interview cut three ways, and one piece cut from three days of
    rushes. `order_idx` is upload order, which is all "chronological" can
    honestly mean for material shot on different days.
    """

    __tablename__ = "job_assets"
    org_id = sa.Column(sa.Text, nullable=False)
    job_id = sa.Column(sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    asset_id = sa.Column(
        sa.Text, sa.ForeignKey("assets.id", ondelete="RESTRICT"), primary_key=True
    )
    order_idx = sa.Column(sa.Integer, nullable=False)
    __table_args__ = (
        sa.UniqueConstraint("job_id", "order_idx", name="uq_job_assets_order"),
    )


class JobStep(Base):
    """Fine-grained progress. Drives the progress UI; coarse state is on the job."""

    __tablename__ = "job_steps"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    job_id = sa.Column(sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    # Pipeline order. Without it "which step is running" is a question about row
    # insertion order, which is not a guarantee.
    idx = sa.Column(sa.Integer, nullable=False)
    name = sa.Column(sa.Text, nullable=False)
    status = sa.Column(sa.Text, nullable=False, server_default=sa.text("'pending'"))
    attempt = sa.Column(sa.Integer, nullable=False, server_default=sa.text("0"))
    # ADR-0012: an in-flight job's steps were written by the PREVIOUS release
    # and the new one has to read them. The version says which shape
    # input_ref/output_ref are in. Nothing may assume it is the current one.
    payload_version = sa.Column(sa.Integer, nullable=False, server_default=sa.text("1"))
    # S3 references, never payloads — Step Functions caps state at 256 KB and a
    # transcript exceeds it.
    input_ref = sa.Column(sa.Text)
    output_ref = sa.Column(sa.Text)
    # Human-readable progress, e.g. "412 beats · 6 of 9 windows".
    detail = sa.Column(sa.Text)
    error = sa.Column(JSONB)
    started_at = sa.Column(TS)
    finished_at = sa.Column(TS)
    # The upload this step ran for; NULL for a job-phase step. The runner has
    # always known it — `StepRun.asset_id` — and until 0005 the write path
    # dropped it, which made a three-upload job's per-asset timings eighteen
    # indistinguishable rows and a per-source-hour baseline uncomputable.
    asset_id = sa.Column(sa.Text)
    # Duration of the attempt that ENDED the step, and of every attempt
    # together. Not derivable: this row is idempotent on (job_id, idx), so a
    # retry overwrites started_at and the derived figure describes the last
    # attempt only — a stage that failed twice at eight minutes reads as cheap.
    seconds = sa.Column(sa.Float)
    cumulative_seconds = sa.Column(sa.Float)
    # Served from the ingest cache rather than executed (ADR-0016). Without it,
    # a re-run is six stages that took no time and no recorded reason, and the
    # cache hits average into the cost of the work they skipped.
    from_cache = sa.Column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    # Model spend attributed to this step, in millionths of a dollar. Micros
    # because a scoring call rounds to zero cents and summing zeros is how a
    # cost model concludes the models are free.
    model_cost_micros = sa.Column(
        sa.BigInteger, nullable=False, server_default=sa.text("0")
    )
    __table_args__ = (
        _ck("ck_job_steps_status", "status", vocab.STEP_STATUSES),
        sa.UniqueConstraint("job_id", "idx", name="uq_job_steps_job_idx"),
    )


class JobLlmCall(Base):
    """One model call. The evidence C1 prices a credit from.

    `Ledger`/`CallRecord` in `llm/base.py` already carry exactly this in memory
    and write it into the job's `.mishne.json`; this is the same record kept
    somewhere a query can reach, which is what "cost per model" requires.

    Nothing here is customer content — task, vendor, model id, counts, latency,
    cost, status. Prompts and completions are not recorded and must not be
    added (docs/architecture/04-security.md).
    """

    __tablename__ = "job_llm_calls"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    job_id = sa.Column(
        sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    step_idx = sa.Column(sa.Integer, nullable=False)
    step_name = sa.Column(sa.Text, nullable=False)
    #: brief | spans | score | transcribe — the stage's own name for the work.
    #: Transcription is here too: a managed engine call is a vendor call that
    #: costs money, and it is the largest one in a job.
    task = sa.Column(sa.Text, nullable=False)
    provider = sa.Column(sa.Text, nullable=False)
    model = sa.Column(sa.Text, nullable=False)
    ok = sa.Column(sa.Boolean, nullable=False)
    latency_ms = sa.Column(sa.Integer, nullable=False, server_default=sa.text("0"))
    input_tokens = sa.Column(sa.Integer, nullable=False, server_default=sa.text("0"))
    output_tokens = sa.Column(sa.Integer, nullable=False, server_default=sa.text("0"))
    cost_micros = sa.Column(sa.BigInteger, nullable=False, server_default=sa.text("0"))
    #: An unpriced model is not a free model. `priced=False` with cost 0 is
    #: UNKNOWN; `priced=True` with cost 0 is genuinely nothing.
    priced = sa.Column(sa.Boolean, nullable=False, server_default=sa.text("true"))
    #: The model this call was a failover FROM. Non-empty means the router moved
    #: vendors and this call is the recovery, not a second failure — counting it
    #: as one is an error rate wrong in the direction that causes needless work.
    fell_back_from = sa.Column(sa.Text, nullable=False, server_default=sa.text("''"))
    violations = sa.Column(sa.Integer, nullable=False, server_default=sa.text("0"))
    proposals = sa.Column(sa.Integer, nullable=False, server_default=sa.text("0"))
    #: The exception TYPE, never a provider's message.
    error_type = sa.Column(sa.Text, nullable=False, server_default=sa.text("''"))
    #: Seconds of audio, for a transcription call. Cost per source hour — the
    #: number the GPU-or-CPU decision was blocked on — is this column over
    #: cost_micros, with no join.
    audio_seconds = sa.Column(sa.Float, nullable=False, server_default=sa.text("0"))
    #: The cost came from published rates on assumed quantities, not from what
    #: the vendor reported. Priced but not measured; do not reconcile it.
    cost_estimated = sa.Column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    created_at = sa.Column(TS, nullable=False, server_default=sa.text("now()"))


# ─────────────────────────────────────────────────────── transcript and beats


class Transcript(Base):
    """Keyed on the ASSET, not the job.

    Transcription is the expensive step and it belongs to the upload. An asset
    transcribed today is reused by a job next month at no cost, which is the
    entire economics of separated uploads.
    """

    __tablename__ = "transcripts"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    asset_id = sa.Column(sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    provider = sa.Column(sa.Text, nullable=False)
    provider_model = sa.Column(sa.Text, nullable=False)
    language = sa.Column(sa.Text, nullable=False)
    # The canonical raw ASR response. The tables below are the query surface,
    # not the record of truth.
    raw_s3_key = sa.Column(sa.Text)
    # The database equivalent of CACHE_VERSION in pipeline/project.py. Beats are
    # a cache of an expensive computation and this table is where that cache
    # lives now; a row written by older code serves beats built by segmentation
    # rules that no longer exist, and the only symptom is a subtly wrong cut.
    ingest_version = sa.Column(sa.Integer, nullable=False, server_default=sa.text("0"))
    # Crosstalk and unattributed word counts, reliability, notes. The transcript
    # page renders this and treats `reliable: false` as a reason to distrust the
    # speaker labels, so it is not decoration.
    attribution = sa.Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    created_at = sa.Column(TS, nullable=False, server_default=NOW)
    __table_args__ = (
        # One transcript per asset. Re-transcribing replaces it; a second row
        # would make "the transcript for this asset" ambiguous.
        sa.UniqueConstraint("asset_id", name="uq_transcripts_asset"),
    )


class Speaker(Base):
    """A distinct voice in one asset, before anyone has given it a name.

    Not in the architecture doc, and the transcript page cannot be rendered
    without it: `beats.speaker` is a bare local id like "T1" and something has to
    say that T1 is Mic 1, that a human called it Margret Olsen, and that they
    confirmed it.
    """

    __tablename__ = "speakers"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    asset_id = sa.Column(sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    # Local to the asset: "T1", "S2". Not unique across the project, on purpose.
    speaker_id = sa.Column(sa.Text, nullable=False)
    source = sa.Column(sa.Text, nullable=False)
    # What the UI shows until a human renames it: "Mic 2", "Speaker 1".
    default_label = sa.Column(sa.Text, nullable=False)
    label = sa.Column(sa.Text, nullable=False, server_default=sa.text("''"))
    confirmed = sa.Column(sa.Boolean, nullable=False, server_default=sa.false())
    track_index = sa.Column(sa.Integer)
    word_count = sa.Column(sa.Integer, nullable=False, server_default=sa.text("0"))
    speech_ms = sa.Column(sa.BigInteger, nullable=False, server_default=sa.text("0"))
    __table_args__ = (
        _ck("ck_speakers_source", "source", vocab.SPEAKER_SOURCES),
        sa.UniqueConstraint("asset_id", "speaker_id", name="uq_speakers_asset_speaker"),
    )


class Invitation(Base):
    """An offer of membership, and the record of who accepted it.

    The token is not here: `token_hash` is sha256 of it, exactly as `sessions`
    stores a session token (migration 0003). It exists in an email and in the
    invitee's URL bar, so a leaked database is not a way into an organisation.

    A used or revoked invitation is kept rather than deleted. Who joined, when,
    and on whose invitation is an access-control question, and the row is the
    answer.
    """

    __tablename__ = "invitations"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    email = sa.Column(sa.Text, nullable=False)
    role = sa.Column(sa.Text, nullable=False)
    token_hash = sa.Column(sa.Text, nullable=False, unique=True)
    #: Deliberately not a foreign key: an invitation outlives the person who
    #: sent it, and their leaving is not a reason to lose who let somebody in.
    invited_by = sa.Column(sa.Text)
    expires_at = sa.Column(TS, nullable=False)
    accepted_at = sa.Column(TS)
    accepted_user_id = sa.Column(sa.Text)
    revoked_at = sa.Column(TS)
    created_at = sa.Column(TS, nullable=False, server_default=NOW)


class SpeakerLink(Base):
    """A human saying "Tuesday's Mic 1 and Friday's Mic 1 are the same person".

    Attribution knows which microphone a voice came down and nothing at all
    about whether two days' track 1 are the same person. Guessing reads as
    intelligence right up until it puts words in the wrong mouth in a delivered
    cut, where nobody can tell it happened. So the merge is a row a person
    creates, and `confirmed_by` records who.
    """

    __tablename__ = "speaker_links"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    project_id = sa.Column(
        sa.Text, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    canonical_speaker_id = sa.Column(sa.Text, nullable=False)
    asset_id = sa.Column(sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    speaker_id = sa.Column(sa.Text, nullable=False)
    confirmed_by = sa.Column(sa.Text)
    confirmed_at = sa.Column(TS)
    __table_args__ = (
        sa.UniqueConstraint(
            "project_id", "asset_id", "speaker_id", name="uq_speaker_links_member"
        ),
    )


class Word(Base):
    """~40k rows per three-hour transcript. Partition by transcript_id if ever needed.

    Milliseconds, not frames, and this is the one table where that is right: ASR
    emits time, a word is never a cut point, and the conversion to frames happens
    once at `structure`. Storing frames here would bake a rate into the ASR
    output that the ASR never saw.
    """

    __tablename__ = "words"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    transcript_id = sa.Column(
        sa.Text, sa.ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    source_clip_id = sa.Column(sa.Text, sa.ForeignKey("source_clips.id", ondelete="SET NULL"))
    idx = sa.Column(sa.Integer, nullable=False)
    text = sa.Column(sa.Text, nullable=False)
    start_ms = sa.Column(sa.BigInteger, nullable=False)
    end_ms = sa.Column(sa.BigInteger, nullable=False)
    confidence = sa.Column(sa.REAL, nullable=False, server_default=sa.text("1"))
    speaker = sa.Column(sa.Text, nullable=False, server_default=sa.text("''"))
    __table_args__ = (
        sa.UniqueConstraint("transcript_id", "idx", name="uq_words_transcript_idx"),
    )


class Beat(Base):
    """A unit of speech that can stand alone. The thing selection chooses between.

    `asset_id` is denormalised from the transcript on purpose: a beat's timing is
    local to its own file and meaningless without knowing which file. Two beats
    with the same frame number are not the same moment unless they share an
    asset.
    """

    __tablename__ = "beats"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    transcript_id = sa.Column(
        sa.Text, sa.ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    asset_id = sa.Column(sa.Text, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    source_clip_id = sa.Column(sa.Text, sa.ForeignKey("source_clips.id", ondelete="SET NULL"))
    idx = sa.Column(sa.Integer, nullable=False)
    # Frames, in the asset's own rate and timecode origin.
    start_frames = sa.Column(sa.BigInteger, nullable=False)
    end_frames = sa.Column(sa.BigInteger, nullable=False)
    speaker = sa.Column(sa.Text, nullable=False, server_default=sa.text("''"))
    text = sa.Column(sa.Text, nullable=False)
    flags = sa.Column(ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]"))
    mean_confidence = sa.Column(sa.REAL, nullable=False, server_default=sa.text("1"))
    # For redundancy clustering. Nothing populates it yet and there is no index
    # on it — an HNSW index on an empty column costs nothing to skip and its
    # parameters cannot be tuned against no data. Adding one later is expand-only.
    embedding = sa.Column(Vector(1024))
    __table_args__ = (
        sa.UniqueConstraint("transcript_id", "idx", name="uq_beats_transcript_idx"),
        sa.CheckConstraint("end_frames > start_frames", name="ck_beats_positive_duration"),
    )


# ──────────────────────────────────────────────────────── selection and output


class BeatScore(Base):
    """One job's opinion of one beat. Scores are per-job; beats are not."""

    __tablename__ = "beat_scores"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    job_id = sa.Column(sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    beat_id = sa.Column(sa.Text, sa.ForeignKey("beats.id", ondelete="CASCADE"), nullable=False)
    # Per-dimension scores, as the scoring stage produced them.
    scores = sa.Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    # The 0-100 the UI shows. Denormalised from `scores` because every transcript
    # page sorts and filters on it, and a jsonb extraction per row is not the
    # place to spend that.
    composite = sa.Column(sa.REAL)
    # Beats this one does not stand up without — a question before its answer.
    depends_on = sa.Column(ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]"))
    # Why. The single most trust-building string in the product; it is shown
    # next to every selected beat. Lives here and nowhere else.
    rationale = sa.Column(sa.Text)
    # Redundancy group: at most one member of a cluster can be selected.
    cluster_id = sa.Column(sa.Text)
    __table_args__ = (
        sa.UniqueConstraint("job_id", "beat_id", name="uq_beat_scores_job_beat"),
    )


class Selection(Base):
    """The cut. One row per span, in cut order.

    A selection is not always a whole beat: stage 6 proposes narrower spans
    carved from one (ADR-0010), which is why the in/out timecodes are here rather
    than read off the beat.
    """

    __tablename__ = "selections"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    job_id = sa.Column(sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    beat_id = sa.Column(sa.Text, sa.ForeignKey("beats.id", ondelete="RESTRICT"), nullable=False)
    asset_id = sa.Column(sa.Text, sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False)
    order_idx = sa.Column(sa.Integer, nullable=False)
    src_tc_in_frames = sa.Column(sa.BigInteger, nullable=False)
    src_tc_out_frames = sa.Column(sa.BigInteger, nullable=False)
    __table_args__ = (
        # Ordered, with no duplicate positions. There is deliberately NO unique
        # constraint on (job_id, beat_id): a beat carved into two candidate
        # spans with the middle dropped is two clips from one beat (ADR-0010),
        # which is the case the in/out columns above exist to express. See
        # migration 0007.
        sa.UniqueConstraint("job_id", "order_idx", name="uq_selections_job_order"),
        sa.CheckConstraint(
            "src_tc_out_frames > src_tc_in_frames", name="ck_selections_positive_duration"
        ),
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    job_id = sa.Column(sa.Text, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    kind = sa.Column(sa.Text, nullable=False)
    # What the download is called. s3_key is an id and is not it.
    filename = sa.Column(sa.Text, nullable=False)
    s3_key = sa.Column(sa.Text)
    bytes = sa.Column(sa.BigInteger, nullable=False, server_default=sa.text("0"))
    # The round-trip gate. False means the file was generated and rejected —
    # which is a refund, not a delivery.
    validated = sa.Column(sa.Boolean, nullable=False, server_default=sa.false())
    validation = sa.Column(JSONB)
    created_at = sa.Column(TS, nullable=False, server_default=NOW)
    __table_args__ = (
        _ck("ck_artifacts_kind", "kind", vocab.ARTIFACT_KINDS),
        sa.UniqueConstraint("job_id", "kind", name="uq_artifacts_job_kind"),
    )


# ─────────────────────────────────────────────────────────── billing and audit


class CreditLedger(Base):
    """Append-only. Balance is a projection of this table (ADR-0006).

    No row is ever updated or deleted — enforced by a trigger, not by convention.
    A mistake is corrected with a compensating `adjustment` entry, which is what
    makes "why is my balance 142.5?" a query rather than an investigation.
    """

    __tablename__ = "credit_ledger"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    # Plain columns, not foreign keys. `ON DELETE SET NULL` would make deleting
    # a project *update* this table, and the append-only trigger refuses — so
    # the two rules together made any project with billing history undeletable.
    # The ledger says what happened and what it cost; a project that no longer
    # exists changes neither. See migration 0004.
    project_id = sa.Column(sa.Text)
    job_id = sa.Column(sa.Text)
    kind = sa.Column(sa.Text, nullable=False)
    delta = sa.Column(CREDITS, nullable=False)
    balance_after = sa.Column(CREDITS, nullable=False)
    description = sa.Column(sa.Text, nullable=False, server_default=sa.text("''"))
    # Set on `purchase` and `grant` rows that came from Stripe.
    stripe_event_id = sa.Column(sa.Text)
    created_at = sa.Column(TS, nullable=False, server_default=NOW)
    __table_args__ = (
        _ck("ck_credit_ledger_kind", "kind", vocab.LEDGER_KINDS),
        # Idempotency as a constraint rather than as careful code: a retried
        # settle for the same job is a duplicate-key error, not a double charge.
        sa.Index(
            "uq_credit_ledger_job_kind",
            "job_id",
            "kind",
            unique=True,
            postgresql_where=sa.text("job_id IS NOT NULL"),
        ),
    )


class StripeEvent(Base):
    """Webhook dedupe. Stripe delivers at least once.

    Credits are granted on the webhook, never on the checkout redirect — a user
    closing the tab must not lose their purchase.
    """

    __tablename__ = "stripe_events"
    # Stripe's own event id is the primary key. That is the dedupe.
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    type = sa.Column(sa.Text, nullable=False)
    payload = sa.Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    received_at = sa.Column(TS, nullable=False, server_default=NOW)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = sa.Column(sa.Text, primary_key=True)
    org_id = sa.Column(sa.Text, nullable=False)
    actor_user_id = sa.Column(sa.Text)
    action = sa.Column(sa.Text, nullable=False)
    resource_type = sa.Column(sa.Text, nullable=False)
    resource_id = sa.Column(sa.Text)
    ip = sa.Column(INET)
    user_agent = sa.Column(sa.Text)
    at = sa.Column(TS, nullable=False, server_default=NOW)


# ────────────────────────────────────────────────────────────────── indexes
#
# Declared here rather than inside each class so the whole access plan is
# readable in one place — and so that `alembic check` is a real gate: an index
# added to a migration and not to this list shows up as a diff.
#
# Every policy filters on org_id, so an org_id-leading index is not an
# optimisation, it is the access path for a tenant-scoped read. The rest are
# foreign keys: unindexed ones turn an ON DELETE CASCADE into a sequential scan
# per child table.
#
# There is deliberately no index on `beats.embedding`. Nothing populates it, and
# HNSW parameters cannot be chosen against no data. Adding one later is
# expand-only and costs a single CONCURRENTLY build.

sa.Index("ix_users_org", User.__table__.c.org_id)
sa.Index("ix_sessions_user", Session.__table__.c.org_id, Session.__table__.c.user_id)
sa.Index("ix_projects_org_created", Project.__table__.c.org_id, Project.__table__.c.created_at)
sa.Index("ix_assets_org", Asset.__table__.c.org_id)
sa.Index("ix_assets_project", Asset.__table__.c.project_id)
sa.Index("ix_source_clips_asset", SourceClip.__table__.c.asset_id)
sa.Index(
    "ix_asset_media_requirements_asset",
    AssetMediaRequirement.__table__.c.asset_id,
)
# The resolution lookup: given a freshly uploaded file, which linked AAFs in
# this org were waiting for it? Leads with org_id because the policy filters
# on it and an index that does not is not the access path.
sa.Index(
    "ix_asset_media_requirements_match",
    AssetMediaRequirement.__table__.c.org_id,
    AssetMediaRequirement.__table__.c.match_key,
)
sa.Index("ix_jobs_org_created", Job.__table__.c.org_id, Job.__table__.c.created_at)
sa.Index("ix_jobs_project", Job.__table__.c.project_id)
sa.Index("ix_job_assets_asset", JobAsset.__table__.c.asset_id)
sa.Index("ix_speaker_links_project", SpeakerLink.__table__.c.project_id)
sa.Index("ix_words_source_clip", Word.__table__.c.source_clip_id)
sa.Index("ix_beats_asset", Beat.__table__.c.asset_id)
sa.Index("ix_beat_scores_beat", BeatScore.__table__.c.beat_id)
sa.Index("ix_selections_beat", Selection.__table__.c.beat_id)
sa.Index("ix_selections_asset", Selection.__table__.c.asset_id)
sa.Index(
    "ix_credit_ledger_org_created",
    CreditLedger.__table__.c.org_id,
    CreditLedger.__table__.c.created_at,
)
sa.Index("ix_credit_ledger_project", CreditLedger.__table__.c.project_id)
sa.Index("ix_audit_log_org_at", AuditLog.__table__.c.org_id, AuditLog.__table__.c.at)


#: Every table, in dependency order. Creation order for the migration, reverse
#: for the downgrade, and the list the RLS test walks so a new table cannot be
#: added without something noticing it has no policy.
ALL_TABLES = [
    "orgs",
    "org_balances",
    "users",
    "user_credentials",
    "sessions",
    "projects",
    "assets",
    "source_clips",
    "asset_media_requirements",
    "jobs",
    "job_assets",
    "job_steps",
    # C3. Added in 0005, and listed here for the reason the docstring gives:
    # the RLS test walks this list, so a table that is not on it is a table
    # whose policy nobody checks.
    "job_llm_calls",
    "invitations",
    "transcripts",
    "speakers",
    "speaker_links",
    "words",
    "beats",
    "beat_scores",
    "selections",
    "artifacts",
    "credit_ledger",
    "stripe_events",
    "audit_log",
]
