/**
 * Mock data for design and development.
 *
 * Everything the UI renders comes from here until the pipeline is real.
 * Keep it plausible: realistic durations, timecodes, filenames and transcript
 * content make design decisions better than lorem ipsum does.
 */

import {
  RATE_25,
  RATE_29_97,
  TIERS,
  estimateJob,
  type Artifact,
  type Asset,
  type Beat,
  type Job,
  type LedgerEntry,
  type Org,
  type Project,
  type Transcript,
  type User,
} from "@mishne/shared";

export const mockOrg: Org = {
  id: "org_7fa2",
  name: "Northline Post",
  tier: "pro",
  creditBalance: 142.5,
  creditsHeld: 27,
  retentionDays: 30,
};

export const mockUser: User = {
  id: "usr_31c8",
  orgId: "org_7fa2",
  email: "alon@northlinepost.tv",
  name: "Alon Raif",
  role: "owner",
};

export const mockProjects: Project[] = [
  {
    id: "prj_harbour",
    orgId: "org_7fa2",
    name: "Harbour Lights — Ep. 3",
    createdAt: "2026-08-14T09:12:00Z",
    assetCount: 4,
    jobCount: 6,
    creditsUsed: 168,
  },
  {
    id: "prj_summit",
    orgId: "org_7fa2",
    name: "Nordic Energy Summit",
    createdAt: "2026-08-21T14:40:00Z",
    assetCount: 2,
    jobCount: 3,
    creditsUsed: 94,
  },
  {
    id: "prj_field",
    orgId: "org_7fa2",
    name: "Field packages — August",
    createdAt: "2026-08-03T07:55:00Z",
    assetCount: 11,
    jobCount: 11,
    creditsUsed: 231.5,
  },
  {
    id: "prj_promo",
    orgId: "org_7fa2",
    name: "Q4 brand promo",
    createdAt: "2026-08-26T16:20:00Z",
    assetCount: 1,
    jobCount: 0,
    creditsUsed: 0,
  },
];

export const mockAssets: Asset[] = [
  {
    id: "ast_9d41",
    projectId: "prj_harbour",
    kind: "video",
    ingestMode: "full_media",
    status: "ready",
    filename: "HARBOUR_EP3_INT_MARGRET_A001.mov",
    bytes: 196_142_000_000,
    durationFrames: 267_750, // 2h 58m 30s at 25 fps
    rate: RATE_25,
    dropFrame: false,
    startTcFrames: 900_000, // 10:00:00:00
    codec: "ProRes 422",
    audioTracks: 4,
    uploadedAt: "2026-08-27T08:30:00Z",
  },
  {
    id: "ast_2b77",
    projectId: "prj_harbour",
    kind: "audio",
    ingestMode: "audio_only",
    status: "ready",
    filename: "HARBOUR_EP3_INT_JONAS_mixdown.wav",
    bytes: 362_000_000,
    durationFrames: 152_100, // 1h 41m 24s
    rate: RATE_25,
    dropFrame: false,
    startTcFrames: 900_000,
    codec: "PCM 48k/24",
    audioTracks: 2,
    uploadedAt: "2026-08-27T11:02:00Z",
  },
  {
    id: "ast_5e10",
    projectId: "prj_summit",
    kind: "aaf",
    ingestMode: "aaf_embedded",
    status: "ready",
    filename: "SUMMIT_KEYNOTE_SELECTS_v4.aaf",
    bytes: 84_900_000_000,
    durationFrames: 195_804, // 1h 48m 52s at 29.97
    rate: RATE_29_97,
    dropFrame: true,
    startTcFrames: 1_079_892,
    codec: "DNxHD 145",
    audioTracks: 8,
    uploadedAt: "2026-08-26T13:15:00Z",
  },
  {
    id: "ast_ab03",
    projectId: "prj_promo",
    kind: "video",
    ingestMode: "full_media",
    status: "uploading",
    filename: "PROMO_Q4_RAW_A002.mp4",
    bytes: 31_400_000_000,
    durationFrames: 108_000,
    rate: RATE_25,
    dropFrame: false,
    startTcFrames: 0,
    codec: "H.264",
    audioTracks: 2,
    uploadedAt: "2026-08-28T15:44:00Z",
  },
];

export const assetById = (id: string) => mockAssets.find((a) => a.id === id);
export const projectById = (id: string) => mockProjects.find((p) => p.id === id);

const STEP_LABELS: Array<[string, string]> = [
  ["prepare", "Probe and normalize"],
  ["audio", "Extract audio"],
  ["transcribe", "Transcribe with word timestamps"],
  ["vad", "Build silence map"],
  ["structure", "Structure into beats"],
  ["brief", "Compile edit brief"],
  ["score", "Score beats"],
  ["select", "Solve selection"],
  ["review", "Review sequence"],
  ["refine", "Refine cut points"],
  ["assemble", "Assemble timeline"],
  ["emit", "Generate artifacts"],
  ["validate", "Validate round-trip"],
];

function steps(activeIdx: number) {
  return STEP_LABELS.map(([name, label], i) => ({
    name,
    label,
    status:
      i < activeIdx
        ? ("done" as const)
        : i === activeIdx
          ? ("active" as const)
          : ("pending" as const),
    detail:
      i === activeIdx && name === "score"
        ? "412 beats · 6 of 9 windows"
        : undefined,
  }));
}

const harbourAsset = mockAssets[0];
const tier = TIERS[mockOrg.tier];

export const mockEstimate = estimateJob({
  assets: [harbourAsset],
  tier,
  balance: mockOrg.creditBalance,
});

export const mockJobs: Job[] = [
  {
    id: "job_c41a",
    projectId: "prj_harbour",
    assetIds: ["ast_9d41"],
    mode: "ai",
    status: "analyzing",
    notesRaw:
      "Ten minutes, tight. Lead on the harbour closure decision — that's the story. Margret's line about her father's boat has to be in there. Keep it conversational, not stuffy. Drop anything about the council vote, we're covering that separately.",
    brief: {
      targetDurationS: 600,
      durationToleranceS: 30,
      tone: ["conversational", "urgent"],
      narrativeShape: "inverted_pyramid",
      mustInclude: ["the harbour closure decision", "Margret's father's boat"],
      mustExclude: ["the council vote"],
      speakerPriority: ["Margret Olsen"],
      pacing: "tight",
      keepFiller: false,
      handleFrames: 0,
      language: "en",
      clarifications: [
        'Notes say "tight" — assumed a 30-second tolerance on the 10-minute target.',
        "No preference given on speaker balance beyond Margret; Jonas kept as secondary.",
      ],
    },
    steps: steps(6),
    createdAt: "2026-08-28T15:58:00Z",
    estimate: mockEstimate,
  },
  {
    id: "job_8f23",
    projectId: "prj_harbour",
    assetIds: ["ast_2b77"],
    mode: "ai",
    status: "complete",
    notesRaw:
      "Six minutes for the web cut. Jonas only. Warm, reflective. Lose the technical stuff about quota systems.",
    brief: {
      targetDurationS: 360,
      durationToleranceS: 20,
      tone: ["warm", "reflective"],
      narrativeShape: "chronological",
      mustInclude: [],
      mustExclude: ["quota systems"],
      speakerPriority: ["Jonas Berg"],
      pacing: "breathing",
      keepFiller: false,
      handleFrames: 0,
      language: "en",
      clarifications: [],
    },
    steps: STEP_LABELS.map(([name, label]) => ({
      name,
      label,
      status: "done" as const,
    })),
    createdAt: "2026-08-27T11:40:00Z",
    finishedAt: "2026-08-27T12:14:00Z",
    estimate: { ...mockEstimate, cap: 16, subtotal: 15.4 },
    creditsSettled: 14.8,
  },
  {
    id: "job_1d90",
    projectId: "prj_summit",
    assetIds: ["ast_5e10"],
    mode: "ai",
    status: "failed",
    notesRaw: "Twelve minutes. Keynote highlights, energy transition focus.",
    brief: {
      targetDurationS: 720,
      durationToleranceS: 45,
      tone: ["authoritative"],
      narrativeShape: "thematic",
      mustInclude: [],
      mustExclude: [],
      speakerPriority: [],
      pacing: "tight",
      keepFiller: false,
      handleFrames: 8,
      language: "en",
      clarifications: [],
    },
    steps: STEP_LABELS.map(([name, label], i) => ({
      name,
      label,
      status: i < 12 ? ("done" as const) : ("failed" as const),
    })),
    createdAt: "2026-08-26T14:02:00Z",
    finishedAt: "2026-08-26T14:51:00Z",
    estimate: { ...mockEstimate, cap: 14, subtotal: 13.2 },
    error:
      "Round-trip validation failed: AAF clip count 47 does not match timeline (48). Credits were refunded.",
  },
];

mockJobs.push({
  id: "job_2e57",
  projectId: "prj_harbour",
  // Two uploads in one cut — the interview and the pickup shoot. Any screen
  // that renders a job must survive this, not just the single-asset case.
  assetIds: ["ast_9d41", "ast_2b77"],
  mode: "hybrid",
  status: "awaiting_edit",
  notesRaw:
    "Give me a starting point for the web version and I'll take it from there.",
  brief: {
    // Scaled to the transcript fixture, which is a 26-beat excerpt rather than
    // a full 3-hour beat list. Keeps the editor's target gauge meaningful.
    targetDurationS: 120,
    durationToleranceS: 20,
    tone: ["conversational"],
    narrativeShape: "chronological",
    mustInclude: [],
    mustExclude: [],
    speakerPriority: [],
    pacing: "breathing",
    keepFiller: false,
    handleFrames: 0,
    language: "en",
    clarifications: [],
  },
  steps: STEP_LABELS.map(([name, label], i) => ({
    name,
    label,
    status: i < 9 ? ("done" as const) : ("pending" as const),
  })),
  createdAt: "2026-08-28T14:10:00Z",
  estimate: { ...mockEstimate, cap: 28, subtotal: 27.4 },
});

export const jobById = (id: string) => mockJobs.find((j) => j.id === id);
export const jobsForProject = (id: string) =>
  mockJobs.filter((j) => j.projectId === id);
export const assetsForProject = (id: string) =>
  mockAssets.filter((a) => a.projectId === id);

export const mockArtifacts: Artifact[] = [
  {
    id: "art_1",
    jobId: "job_8f23",
    kind: "aaf",
    filename: "HARBOUR_EP3_JONAS_roughcut_v1.aaf",
    bytes: 412_000,
    validated: true,
    targetNle: "Avid Media Composer",
  },
  {
    id: "art_2",
    jobId: "job_8f23",
    kind: "fcpxml",
    filename: "HARBOUR_EP3_JONAS_roughcut_v1.fcpxml",
    bytes: 186_000,
    validated: true,
    targetNle: "Premiere Pro · Resolve · Final Cut",
  },
  {
    id: "art_3",
    jobId: "job_8f23",
    kind: "edl",
    filename: "HARBOUR_EP3_JONAS_roughcut_v1.edl",
    bytes: 9_400,
    validated: true,
    targetNle: "Universal fallback",
  },
  {
    id: "art_4",
    jobId: "job_8f23",
    kind: "otio",
    filename: "HARBOUR_EP3_JONAS_roughcut_v1.otio",
    bytes: 244_000,
    validated: true,
    targetNle: "Canonical timeline",
  },
];

export const artifactsForJob = (id: string) =>
  mockArtifacts.filter((a) => a.jobId === id);

export const mockLedger: LedgerEntry[] = [
  {
    id: "led_9",
    orgId: "org_7fa2",
    projectId: "prj_harbour",
    jobId: "job_c41a",
    kind: "hold",
    delta: -27,
    balanceAfter: 142.5,
    description: "Hold for job_c41a · Harbour Lights — Ep. 3",
    createdAt: "2026-08-28T15:58:00Z",
  },
  {
    id: "led_8",
    orgId: "org_7fa2",
    projectId: "prj_summit",
    jobId: "job_1d90",
    kind: "refund",
    delta: 14,
    balanceAfter: 169.5,
    description: "Refund — job_1d90 failed validation",
    createdAt: "2026-08-26T14:51:00Z",
  },
  {
    id: "led_7",
    orgId: "org_7fa2",
    projectId: "prj_harbour",
    jobId: "job_8f23",
    kind: "settle",
    delta: -14.8,
    balanceAfter: 155.5,
    description: "Settled job_8f23 · 14.80 of 16.00 approved",
    createdAt: "2026-08-27T12:14:00Z",
  },
  {
    id: "led_6",
    orgId: "org_7fa2",
    kind: "purchase",
    delta: 105,
    balanceAfter: 170.3,
    description: "Credit pack — $100 (5 bonus credits)",
    createdAt: "2026-08-25T09:30:00Z",
  },
  {
    id: "led_5",
    orgId: "org_7fa2",
    projectId: "prj_field",
    kind: "settle",
    delta: -21.4,
    balanceAfter: 65.3,
    description: "Settled job_7c02 · Field packages — August",
    createdAt: "2026-08-24T17:05:00Z",
  },
];
