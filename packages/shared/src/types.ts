/** Shared domain types. Mirrors apps/api/src/mishne/schemas.py — keep in step. */

import { framesToSeconds, secondsToFrames, type Rate } from "./timecode";

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
/**
 * How far an asset's preview rendition has got.
 *
 * A separate axis from `AssetStatus`, and deliberately: an asset is ingestable
 * long before it is playable, and a transcode that fails must not make the
 * upload itself failed. `none` is an asset nobody has asked for a preview of —
 * everything uploaded before previews existed. `unsupported` is a decided
 * answer rather than a pending one: there is nothing decodable behind it, and
 * asking again will not change that.
 */
export type ProxyStatus =
  | "none"
  | "pending"
  | "running"
  | "ready"
  | "failed"
  | "unsupported";
/** A sequence has no picture to show, so its preview is sound only. */
export type ProxyKind = "" | "video" | "audio";
export type IngestMode = "full_media" | "aaf_embedded" | "audio_only" | "aaf_linked";
export type AssetStatus =
  | "uploading"
  | "probing"
  | "ready"
  | "failed"
  // A linked AAF that probed cleanly and is waiting for the media it
  // references. Not an error state — the upload worked; the sequence simply
  // does not carry its own essence.
  | "awaiting_media";

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
  /**
   * The sequence this file was uploaded to satisfy, if it is one.
   *
   * A linked AAF's companions are ordinary assets and nothing special-cases
   * them (ADR-0014) — right for storage, dedup and retention, wrong for a list
   * of source material: four, or 775, mob-id-named WAVs are not that many
   * things to cut, and offering one invites transcribing a single microphone
   * and paying for the whole running time again. Absent for anything a
   * customer would choose.
   */
  companionOf?: string | null;
  /**
   * How big the media behind this file actually is.
   *
   * An AAF is a sequence, not a container: a linked one is a few hundred
   * kilobytes of pointers, and `bytes` therefore answers a question nobody
   * asked. This is the sum over the referenced files that have arrived — a
   * running total while the asset is `awaiting_media`, complete once it is
   * `ready`. Absent for anything carrying its own essence, where `bytes` is
   * already the whole answer.
   */
  mediaBytes?: number | null;
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

/**
 * What each mode is called on screen.
 *
 * One map, because the answer has to be the same in the list and in the form
 * that created the row: a job submitted as "Transcribe only" that appears in
 * the list as "manual" is two names for one thing, and the customer has to
 * work out that they match. The submission form adds a sentence of hint next
 * to these; the label itself is the short form that fits in a badge.
 */
export const JOB_MODE_LABEL: Record<JobMode, string> = {
  ai: "AI",
  hybrid: "AI Draft",
  manual: "Transcription",
};

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

/**
 * The longest a job name may be, matching `JOB_NAME_MAX` in the API, which
 * refuses anything longer.
 *
 * Here rather than in each field that enforces it: the submission form and the
 * rename control are two chances to disagree with the API about the same
 * number, and disagreeing means letting somebody type a paragraph and then
 * showing them a 422 for it.
 */
export const JOB_NAME_MAX = 120;

export interface Job {
  id: string;
  projectId: string;
  /**
   * What the customer called this cut, chosen at submission.
   *
   * Not unique and not an identifier — `id` is, and it is what every link and
   * every API call uses. This exists to be read: a project holds four cuts of
   * one interview and `job_8a98a1ca` does not say which is the web version.
   * Never empty; the API derives one from the first source file when the
   * client sends none, so nothing here needs a fallback.
   */
  name: string;
  /**
   * Every upload this cut draws on, in upload order.
   *
   * A project accumulates footage over weeks and one finished piece is cut from
   * several sessions, so this is a list and the order is meaningful: it is what
   * "chronological" means for material shot on different days. Beats carry
   * their own asset's local timing and there is no global timeline, so nothing
   * in the UI may show a position without knowing which asset it belongs to.
   */
  assetIds: string[];
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
  /**
   * Media this cut went ahead without: `{assetId: [basename, ...]}`.
   *
   * A linked AAF can be submitted while some of the media it references is
   * still missing, if the person submitting says so — the clips it cannot
   * resolve are silent in the transcript and still reference the right source
   * (ADR-0014). Recorded as it stood at submission and never updated, because
   * uploading the file next week would otherwise erase the reason this
   * transcript has silence in it. Empty for every other job.
   */
  mediaGaps: Record<string, string[]>;
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

/**
 * What `GET /v1/assets/{id}/proxy` answers.
 *
 * It answers in every state rather than erroring in most of them: the editor
 * polls this while the transcode runs, and making "not finished yet" an HTTP
 * error would put this client in the business of reading exception bodies to
 * discover that nothing is wrong.
 */
export interface AssetProxy {
  /**
   * Which asset this answer is about.
   *
   * Not redundant with the request. The reader keeps the previous answer while
   * the next one is in flight, so on a job drawing on several reels there is a
   * moment where the response in hand belongs to the reel that *was* showing —
   * and playing that one, seeked to a position from this one, is worse than
   * showing nothing.
   */
  assetId: string;
  status: ProxyStatus;
  kind: ProxyKind;
  /** A presigned GET, and only when `status` is "ready". Never persisted. */
  url: string | null;
  expiresInS: number;
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
  /**
   * Which upload this beat came from. `startFrames` is local to that asset's
   * own reel and rate — two beats with the same number are not the same moment
   * unless they share an assetId.
   */
  assetId: string;
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
  /**
   * The uploads this voice was heard in — more than one only after a human has
   * merged them.
   *
   * The same person recorded on two days arrives as two speakers, because
   * attribution knows which microphone a voice came down and nothing about
   * whether Tuesday's track 1 is Friday's track 1. The legend shows them apart,
   * suffixed with the reel, and offers the merge. Guessing would read as
   * intelligence right up until it puts words in the wrong mouth.
   */
  assetIds: string[];
}

export interface SpeakerAttribution {
  speakers: Speaker[];
  crosstalkWords: number;
  unattributedWords: number;
  /** False when crosstalk is high enough that labels should not be trusted. */
  reliable: boolean;
  notes: string[];
}

/** One upload as the transcript UI needs to see it: its own reel, its own rate. */
export interface TranscriptAsset {
  assetId: string;
  filename: string;
  rate: Rate;
  dropFrame: boolean;
  startTcFrames: number;
  durationFrames: number;
  /** ISO code. A project can mix languages; direction is decided per asset. */
  language: string;
  /**
   * Whether this reel can be played, and as what.
   *
   * Carried here rather than fetched separately because it has to be read
   * *with* `rate` and `startTcFrames` — those three together are what turn a
   * beat into a position in a media file, and splitting them across two
   * payloads invites using one without the others.
   */
  proxyStatus: ProxyStatus;
  proxyKind: ProxyKind;
}

export interface Transcript {
  jobId: string;
  /**
   * Every upload in the cut. Timecodes are formatted against the entry matching
   * the beat's own `assetId` — never against a single job-wide rate, which is
   * how a two-camera cut ends up displaying timecodes that do not exist.
   */
  assets: TranscriptAsset[];
  /** The language most of the material is in; drives overall text direction. */
  language: string;
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


/**
 * The reel a beat belongs to.
 *
 * Every timecode in the UI must be formatted against this, never against a
 * job-wide rate: a project can mix a 25 fps studio reel with a 23.976 pickup,
 * and formatting one against the other's rate produces timecodes that look
 * right and do not exist. Falls back to the first asset so a malformed fixture
 * renders rather than throws.
 */
export function assetOf(
  transcript: Transcript,
  beat: Pick<Beat, "assetId">
): TranscriptAsset {
  return (
    transcript.assets.find((a) => a.assetId === beat.assetId) ??
    transcript.assets[0]
  );
}


/**
 * Where a source frame sits in the asset's own media, in seconds.
 *
 * **`Beat.startFrames` is absolute source timecode, not an offset into the
 * file.** A beat carries `startTcFrames + elapsed`, because that is what a
 * timecode means to an editor. A player's `currentTime` is an offset into the
 * file. Every seek and every highlight crosses between the two, so the
 * subtraction lives here and is written once — the same reason `assetOf`
 * exists.
 *
 * Getting it wrong is silent. A reel starting at 10:00:00:00 seeks ten hours
 * past the end, the element clamps, and the player just sits there.
 */
export function mediaSecondsOf(asset: TranscriptAsset, frames: number): number {
  return framesToSeconds(frames - asset.startTcFrames, asset.rate);
}

/** The absolute source frame a player at `seconds` is sitting on. */
export function framesAtMediaSeconds(
  asset: TranscriptAsset,
  seconds: number
): number {
  return asset.startTcFrames + secondsToFrames(seconds, asset.rate);
}

/** Shown where a beat's voice is not one of the transcript's speakers. */
export const UNATTRIBUTED_SPEAKER = "unattributed";

/**
 * The transcript's speakers, indexed for display.
 *
 * Two screens render a beat's speaker — the transcript viewer and the cut
 * editor — and both need the same two answers: what to call this voice, and
 * which colour it is. Sharing them is not just deduplication; it is what makes
 * "the same voice reads the same everywhere" true rather than intended.
 *
 * **A beat's speaker id can legitimately not be in the roster.** A word the
 * pipeline could not attribute carries a placeholder, and a job ingested by
 * older code carries whatever the ASR vendor called the voice. Both must read
 * as *unattributed* — `indexOf` returns -1 so no colour is claimed, and
 * `nameOf` says so in words. Falling back to the raw id, which is what both
 * screens used to do, put `c0:spk:0` on the line and one colour on every
 * speaker in the job, and it did it silently.
 */
export function speakerRoster(transcript: Pick<Transcript, "speakers">) {
  const byId = new Map(
    transcript.speakers.map((speaker, index) => [speaker.id, { speaker, index }])
  );
  return {
    /** -1 when this voice is not in the roster: no colour, not colour zero. */
    indexOf(id: string): number {
      return byId.get(id)?.index ?? -1;
    },
    nameOf(id: string): string {
      const hit = byId.get(id);
      if (!hit) return UNATTRIBUTED_SPEAKER;
      return hit.speaker.label || hit.speaker.defaultLabel || UNATTRIBUTED_SPEAKER;
    },
    /** False for a beat nothing in the legend accounts for. */
    has(id: string): boolean {
      return byId.has(id);
    },
  };
}
