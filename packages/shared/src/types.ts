/** Shared domain types. Mirrors apps/api/src/mishne/schemas.py — keep in step. */

import type { Rate } from "./timecode";

/* ---------------------------------------------------------------- tenancy */

export type Role = "owner" | "member" | "viewer";
export type TierId = "starter" | "pro" | "studio";

export interface Org {
  id: string;
  name: string;
  tier: TierId;
  creditBalance: number; // credits, 1 credit = US$1. Projection of the ledger.
  creditsHeld: number; // reserved by in-flight jobs, not yet settled
  retentionDays: number;
}

export interface User {
  id: string;
  orgId: string;
  email: string;
  name: string;
  role: Role;
}

/* ---------------------------------------------------------------- projects */

export interface Project {
  id: string;
  orgId: string;
  name: string;
  createdAt: string;
  assetCount: number;
  jobCount: number;
  creditsUsed: number; // lifetime credits consumed by this project
}

/* ------------------------------------------------------------------ assets */

export type AssetKind = "video" | "aaf" | "audio";
export type IngestMode = "full_media" | "aaf_embedded" | "audio_only";
export type AssetStatus = "uploading" | "probing" | "ready" | "failed";

export interface Asset {
  id: string;
  projectId: string;
  kind: AssetKind;
  ingestMode: IngestMode;
  status: AssetStatus;
  filename: string;
  bytes: number;
  durationFrames: number;
  rate: Rate;
  dropFrame: boolean;
  startTcFrames: number;
  codec: string;
  audioTracks: number;
  uploadedAt: string;
}

/* -------------------------------------------------------------------- jobs */

export type JobStatus =
  | "estimating"
  | "awaiting_approval"
  /** Manual and hybrid: transcript is ready and waiting on the user's cut. */
  | "awaiting_edit"
  | "queued"
  | "preparing"
  | "transcribing"
  | "analyzing"
  | "selecting"
  | "assembling"
  | "validating"
  | "complete"
  | "failed"
  | "cancelled";

/**
 * How the selection gets made.
 *
 * The pipeline is identical downstream of selection in every mode — stages 9-12
 * (refine cut points, assemble, emit, validate) do not care whether the beats
 * were chosen by the solver or by a person. Only stages 5-8 differ.
 */
export type JobMode =
  /** Notes in, rough cut out. The engine selects. */
  | "ai"
  /** Transcribe only, then the user marks the cut on the text themselves. */
  | "manual"
  /** The engine proposes a selection, the user edits it before assembly. */
  | "hybrid";

export type NarrativeShape =
  | "chronological"
  | "thematic"
  | "inverted_pyramid"
  | "q_and_a";

export interface EditBrief {
  targetDurationS: number;
  durationToleranceS: number;
  tone: string[];
  narrativeShape: NarrativeShape;
  mustInclude: string[];
  mustExclude: string[];
  speakerPriority: string[];
  pacing: "tight" | "breathing";
  keepFiller: boolean;
  handleFrames: number;
  language: string;
  clarifications: string[];
}

export interface JobStep {
  name: string;
  label: string;
  status: "pending" | "active" | "done" | "failed";
  startedAt?: string;
  finishedAt?: string;
  detail?: string;
}

export interface Job {
  id: string;
  projectId: string;
  assetId: string;
  mode: JobMode;
  status: JobStatus;
  notesRaw: string;
  brief: EditBrief;
  steps: JobStep[];
  createdAt: string;
  finishedAt?: string;
  estimate: CreditEstimate;
  creditsSettled?: number;
  error?: string;
}

/* --------------------------------------------------------------- artifacts */

export type ArtifactKind = "aaf" | "fcpxml" | "edl" | "otio" | "json";

export interface Artifact {
  id: string;
  jobId: string;
  kind: ArtifactKind;
  filename: string;
  bytes: number;
  validated: boolean;
  targetNle: string;
}

/* -------------------------------------------------------------- transcript */

export interface Word {
  t: string;
  startMs: number;
  endMs: number;
  confidence: number;
}

export type BeatFlag =
  | "filler"
  | "false_start"
  | "retake"
  | "crosstalk"
  | "low_confidence"
  | "off_mic";

export interface Beat {
  id: string;
  idx: number;
  speaker: string;
  startFrames: number;
  endFrames: number;
  text: string;
  flags: BeatFlag[];
  used: boolean;
  orderIdx?: number; // position in the cut, when used
  score?: number; // 0..100 composite
  rationale?: string;
}

/**
 * A distinct voice in the source.
 *
 * Two things the product must not confuse:
 *
 * - **Attribution** (who spoke when) is automatic. On multi-track material it
 *   is deterministic, from which microphone is loudest. On single-track it
 *   needs diarization and is only ever 8-15% accurate at the boundaries.
 * - **The name** is not automatic and never will be. Diarization returns
 *   `Speaker_00`. A person supplies the name, and `confirmed` records that they
 *   did. An unconfirmed name must never reach a delivered artifact — a
 *   misattributed quote in a broadcast piece is a serious error, not a typo.
 */
export interface Speaker {
  id: string;
  /** "track" — from a dedicated microphone. "diarization" — inferred. */
  source: "track" | "diarization";
  /** What the UI shows until a human renames it: "Mic 2", "Speaker 1". */
  defaultLabel: string;
  /** The human-supplied name. Empty until someone types one. */
  label: string;
  confirmed: boolean;
  trackIndex?: number;
  wordCount: number;
  speechMs: number;
}

export interface SpeakerAttribution {
  speakers: Speaker[];
  crosstalkWords: number;
  unattributedWords: number;
  /** False when crosstalk is high enough that labels should not be trusted. */
  reliable: boolean;
  notes: string[];
}

export interface Transcript {
  jobId: string;
  assetId: string;
  language: string;
  rate: Rate;
  dropFrame: boolean;
  speakers: Speaker[];
  attribution: SpeakerAttribution;
  beats: Beat[];
  sourceDurationFrames: number;
  cutDurationFrames: number;
}

/* ----------------------------------------------------------------- billing */

export interface Tier {
  id: TierId;
  name: string;
  blurb: string;
  monthlyPrice: number | null; // null = contact sales
  creditRatePerSourceHour: number; // credits charged per hour of source material
  maxSourceHours: number;
  concurrentJobs: number;
  retentionDays: number;
  features: string[];
  sso: boolean;
}

export type CreditPackId = "pack_50" | "pack_100" | "pack_200";

export interface CreditPack {
  id: CreditPackId;
  amount: number; // dollars charged
  credits: number; // credits granted (may include a bonus)
  bonus: number;
  label: string;
}

export interface EstimateLine {
  label: string;
  detail: string;
  credits: number;
}

export interface CreditEstimate {
  mode: JobMode;
  sourceDurationFrames: number;
  sourceHours: number;
  lines: EstimateLine[];
  subtotal: number;
  /** What the user approves. Settlement never exceeds this. */
  cap: number;
  balanceBefore: number;
  balanceAfter: number;
  sufficient: boolean;
  shortfall: number;
}

export type LedgerKind =
  | "purchase"
  | "grant"
  | "hold"
  | "release"
  | "settle"
  | "refund"
  | "adjustment";

export interface LedgerEntry {
  id: string;
  orgId: string;
  projectId?: string;
  jobId?: string;
  kind: LedgerKind;
  /** Signed. Positive adds available credit, negative removes it. */
  delta: number;
  balanceAfter: number;
  description: string;
  createdAt: string;
}
