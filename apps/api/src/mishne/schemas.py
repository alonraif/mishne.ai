"""Pydantic schemas. Mirrors packages/shared/src/types.ts — keep the two in step."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
    handle_frames: int = 6
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


class PresignedUpload(BaseModel):
    asset_id: str
    upload_id: str
    part_urls: list[str]
    part_size: int


class CompleteUploadRequest(BaseModel):
    etags: list[str]


class EstimateJobRequest(BaseModel):
    asset_id: str
    target_duration_s: int
    mode: JobMode = "ai"


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


class SubmitCutRequest(BaseModel):
    """A user-authored cut, from manual or hybrid mode.

    An ordered list of beat ids. This replaces the output of stage 7 — the rest
    of the pipeline is unchanged, which is what makes text-based editing cheap
    to support.
    """

    beat_ids: list[str]


class PurchaseCreditsRequest(BaseModel):
    pack_id: Literal["pack_50", "pack_100", "pack_200"]
