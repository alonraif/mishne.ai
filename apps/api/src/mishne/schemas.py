"""Pydantic schemas. Mirrors packages/shared/src/types.ts — keep the two in step."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Role = Literal["owner", "member", "viewer"]
TierId = Literal["starter", "pro", "studio"]
AssetKind = Literal["video", "aaf", "audio"]
IngestMode = Literal["full_media", "aaf_embedded", "audio_only", "aaf_linked"]
AssetStatus = Literal["uploading", "probing", "ready", "failed", "awaiting_media"]
ArtifactKind = Literal["aaf", "fcpxml", "edl", "otio", "json"]
NarrativeShape = Literal["chronological", "thematic", "inverted_pyramid", "q_and_a"]

# How the selection gets made. The pipeline is identical downstream of selection
# in every mode — stages 9-12 do not care whether the beats were chosen by the
# solver or by a person. Only stages 5-8 differ.
#   ai     — notes in, rough cut out
#   manual — transcribe only, the user marks the cut on the text
#   hybrid — the engine proposes, the user edits before assembly
JobMode = Literal["ai", "manual", "hybrid"]
LedgerKind = Literal["purchase", "grant", "hold", "release", "settle", "refund", "adjustment"]
JobStatus = Literal[
    "estimating",
    "awaiting_approval",
    "awaiting_edit",
    "queued",
    "preparing",
    "transcribing",
    "analyzing",
    "selecting",
    "assembling",
    "validating",
    "complete",
    "failed",
    "cancelled",
]
BeatFlag = Literal["filler", "false_start", "retake", "crosstalk", "low_confidence", "off_mic"]
SpeakerSource = Literal["track", "diarization"]


class Rate(BaseModel):
    """Frame rate as a rational. Never a float — see docs/architecture/02."""

    num: int
    den: int


class Org(BaseModel):
    id: str
    name: str
    tier: TierId
    credit_balance: float
    credits_held: float
    retention_days: int


class User(BaseModel):
    id: str
    org_id: str
    email: str
    name: str
    role: Role


class Project(BaseModel):
    id: str
    org_id: str
    name: str
    created_at: datetime
    asset_count: int
    job_count: int
    credits_used: float


class Asset(BaseModel):
    id: str
    project_id: str
    kind: AssetKind
    ingest_mode: IngestMode
    status: AssetStatus
    filename: str
    bytes: int
    duration_frames: int
    rate: Rate
    drop_frame: bool
    start_tc_frames: int
    codec: str
    audio_tracks: int
    uploaded_at: datetime


class EditBrief(BaseModel):
    target_duration_s: int
    duration_tolerance_s: int = 30
    tone: list[str] = Field(default_factory=list)
    narrative_shape: NarrativeShape = "chronological"
    must_include: list[str] = Field(default_factory=list)
    must_exclude: list[str] = Field(default_factory=list)
    speaker_priority: list[str] = Field(default_factory=list)
    pacing: Literal["tight", "breathing"] = "tight"
    keep_filler: bool = False
    handle_frames: int = 0
    language: str = "en"
    clarifications: list[str] = Field(default_factory=list)


class JobStep(BaseModel):
    name: str
    label: str
    status: Literal["pending", "active", "done", "failed"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str | None = None


class EstimateLine(BaseModel):
    label: str
    detail: str
    credits: float


class CreditEstimate(BaseModel):
    mode: JobMode = "ai"
    source_duration_frames: int
    source_hours: float
    lines: list[EstimateLine]
    subtotal: float
    cap: float
    balance_before: float
    balance_after: float
    sufficient: bool
    shortfall: float


class Job(BaseModel):
    id: str
    project_id: str
    # Every upload this cut draws on, in upload order.
    #
    # A project accumulates footage over weeks and one finished piece is cut
    # from several sessions, so this is a list and the order is meaningful: it
    # is what "chronological" can honestly mean for material shot on different
    # days. Beats carry their own asset's local timing and there is no global
    # timeline, so nothing may show a position without knowing its asset.
    # ADR-0008, and the `job_assets` join table.
    asset_ids: list[str]
    mode: JobMode
    status: JobStatus
    notes_raw: str
    brief: EditBrief
    steps: list[JobStep]
    created_at: datetime
    finished_at: datetime | None = None
    estimate: CreditEstimate
    credits_settled: float | None = None
    error: str | None = None


class Artifact(BaseModel):
    id: str
    job_id: str
    kind: ArtifactKind
    filename: str
    bytes: int
    validated: bool
    target_nle: str


class Beat(BaseModel):
    id: str
    idx: int
    # Which upload this beat came from. `start_frames` is local to that asset's
    # own reel and rate — two beats with the same number are not the same
    # moment unless they share an asset_id.
    asset_id: str
    speaker: str
    start_frames: int
    end_frames: int
    text: str
    flags: list[BeatFlag] = Field(default_factory=list)
    used: bool = False
    order_idx: int | None = None
    score: float | None = None
    rationale: str | None = None


class Speaker(BaseModel):
    """A distinct voice in the source.

    Attribution knows which microphone a voice came down and nothing at all
    about who was in front of it a week earlier, so the same person recorded on
    two days arrives as two speakers until a human merges them. `asset_ids`
    holds more than one only after that merge.
    """

    id: str
    #: "track" — a dedicated microphone. "diarization" — inferred.
    source: SpeakerSource
    #: What the UI shows until a human renames it: "Mic 2", "Speaker 1".
    default_label: str
    #: The human-supplied name. Empty until someone types one.
    label: str = ""
    confirmed: bool = False
    track_index: int | None = None
    word_count: int = 0
    speech_ms: int = 0
    asset_ids: list[str] = Field(default_factory=list)


class SpeakerAttribution(BaseModel):
    speakers: list[Speaker] = Field(default_factory=list)
    crosstalk_words: int = 0
    unattributed_words: int = 0
    #: False when crosstalk is high enough that the labels should not be trusted.
    reliable: bool = True
    notes: list[str] = Field(default_factory=list)


class TranscriptAsset(BaseModel):
    """One upload as the transcript UI needs it: its own reel, its own rate."""

    asset_id: str
    filename: str
    rate: Rate
    drop_frame: bool
    start_tc_frames: int
    duration_frames: int
    #: ISO code. A project can mix languages; direction is decided per asset.
    language: str


class Transcript(BaseModel):
    """A job's transcript: every asset in the cut, and the beats across them.

    Assembled from per-asset `transcripts` rows — transcription belongs to the
    upload, not to the job (ADR-0008). Timecodes are formatted against the entry
    matching each beat's own `asset_id`, never against a single job-wide rate,
    which is how a two-camera cut ends up displaying timecodes that do not exist.
    """

    job_id: str
    assets: list[TranscriptAsset]
    language: str
    speakers: list[Speaker] = Field(default_factory=list)
    attribution: SpeakerAttribution = Field(default_factory=SpeakerAttribution)
    beats: list[Beat]
    source_duration_frames: int
    cut_duration_frames: int


class LedgerEntry(BaseModel):
    id: str
    org_id: str
    project_id: str | None = None
    job_id: str | None = None
    kind: LedgerKind
    delta: float
    balance_after: float
    description: str
    created_at: datetime


# ------------------------------------------------------------------ requests


class CreateProjectRequest(BaseModel):
    name: str


class CreateAssetRequest(BaseModel):
    filename: str
    bytes: int
    checksum: str
    ingest_mode: IngestMode = "full_media"
    #: The sequence rate, for an audio-only upload. Required there and ignored
    #: everywhere else: audio carries no frame rate, and guessing one silently
    #: is how a cut ends up a frame out everywhere (ADR-0005). For video and
    #: AAF the file is authoritative and probe reads it.
    rate: Rate | None = None


class MediaRequirement(BaseModel):
    """A file a linked AAF references and does not contain."""

    basename: str
    #: How many clips on the timeline need it. The list is ordered by this: the
    #: file that unblocks forty clips is the one worth asking for first.
    clip_count: int
    satisfied: bool
    satisfied_by_asset_id: str | None = None


class AssetRequirements(BaseModel):
    asset_id: str
    status: AssetStatus
    outstanding: int
    requirements: list[MediaRequirement] = Field(default_factory=list)


class UploadPart(BaseModel):
    """One presigned PUT, and the slice of the file it covers.

    The offsets are sent rather than left to be derived from `part_size` and the
    index, because a resumed upload asks for some of the parts and not all of
    them, and a client computing `(n - 1) * part_size` for a part it was handed
    out of order uploads the wrong bytes to a URL that accepts them happily.
    """

    part_number: int
    url: str
    offset: int
    length: int


class PresignedUpload(BaseModel):
    asset_id: str
    upload_id: str
    part_size: int
    total_parts: int
    parts: list[UploadPart]
    #: When these URLs stop working. A long upload outlives them, and the client
    #: asks for the parts it has left rather than failing.
    expires_in_s: int


class ResumeUploadRequest(BaseModel):
    """Re-mint URLs for the parts a client has not managed to send yet.

    Empty means all of them, which is the after-a-refresh case. A client that
    knows which parts it already sent asks for the rest.
    """

    part_numbers: list[int] = Field(default_factory=list)


class CompletedPart(BaseModel):
    part_number: int
    etag: str


class UploadedPart(BaseModel):
    part_number: int
    etag: str
    size: int


class UploadState(BaseModel):
    """What S3 already holds for an upload in flight, so a resume sends the rest."""

    asset_id: str
    upload_id: str
    part_size: int
    total_parts: int
    total_bytes: int
    uploaded: list[UploadedPart] = Field(default_factory=list)


class CompleteUploadRequest(BaseModel):
    #: Every part, with the number S3 knows it by. Not a bare list of etags in
    #: upload order: parts are uploaded concurrently and retried out of order,
    #: and an etag matched to the wrong part number completes an upload whose
    #: bytes are in the wrong places.
    parts: list[CompletedPart]


# ─────────────────────────────────────────────────────────────────── identity


class SignupRequest(BaseModel):
    email: str
    password: str
    org_name: str
    name: str = ""
    tier: TierId = "starter"


class LoginRequest(BaseModel):
    email: str
    password: str


class Member(BaseModel):
    id: str
    email: str
    name: str
    role: Role
    #: Which mechanism this person signs in with. Empty for a user provisioned
    #: before they have ever signed in.
    auth_provider: str = ""


class CreateMemberRequest(BaseModel):
    email: str
    name: str = ""
    role: Role = "member"
    #: Set now, or leave empty for an SSO organisation where the identity
    #: provider is the only thing that authenticates anyone.
    password: str = ""


class UpdateMemberRequest(BaseModel):
    role: Role


class Session(BaseModel):
    """Who the caller is, as the web app needs it on every page."""

    user: Member
    org: Org


class EstimateJobRequest(BaseModel):
    #: One upload, or the several a job will be cut from. `asset_id` is kept
    #: because the web app sends it and an estimate for a single upload is a
    #: real question; `asset_ids` is what a multi-source job asks. Exactly one
    #: of the two is required, and the validator below is what says so — a
    #: price computed from a silently empty list is worse than a 422.
    asset_id: str = ""
    asset_ids: list[str] = Field(default_factory=list)
    target_duration_s: int
    mode: JobMode = "ai"

    @property
    def assets(self) -> list[str]:
        return self.asset_ids or ([self.asset_id] if self.asset_id else [])

    @model_validator(mode="after")
    def _needs_an_asset(self):
        if not self.assets:
            raise ValueError("give asset_id or asset_ids")
        return self


class CreateJobRequest(BaseModel):
    #: One or more uploads, in the order they should be treated as sequential.
    asset_ids: list[str]
    mode: JobMode = "ai"
    notes: str = ""
    target_duration_s: int
    narrative_shape: NarrativeShape = "inverted_pyramid"
    tone: list[str] = Field(default_factory=list)
    # The user must approve an estimate before a job is accepted.
    approved_cap: float


class InviteRequest(BaseModel):
    email: str
    role: Role = "member"


class Invitation(BaseModel):
    """An outstanding invitation. Never carries the token — it is not stored."""

    id: str
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime


class InvitationPreview(BaseModel):
    """What the accept page shows before anyone types a password.

    The organisation's name and the address it was sent to, and nothing else:
    the person holding this link is not yet a member of anything, and the reply
    to an unauthenticated request should not describe the tenant it belongs to.
    """

    org_name: str
    email: str
    role: Role
    expires_at: datetime


class AcceptInviteRequest(BaseModel):
    name: str = ""
    password: str


class RenameSpeakerRequest(BaseModel):
    """What a person calls this voice. Empty clears the name."""

    label: str = ""


class MergeSpeakersRequest(BaseModel):
    """Two or more voices that are one person.

    Canonical ids as the transcript returned them. The first is the one the
    merged voice keeps — an editor who has already named "Margret Olsen" on
    reel one expects the merge to keep that name, not to pick one.
    """

    speaker_ids: list[str]


class ArtifactDownload(BaseModel):
    url: str
    filename: str
    expires_in_s: int


class SubmitCutRequest(BaseModel):
    """A user-authored cut, from manual or hybrid mode.

    An ordered list of beat ids. This replaces the output of stage 7 — the rest
    of the pipeline is unchanged, which is what makes text-based editing cheap
    to support.
    """

    beat_ids: list[str]


class PurchaseCreditsRequest(BaseModel):
    pack_id: Literal["pack_50", "pack_100", "pack_200"]
