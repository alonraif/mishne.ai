/**
 * Credits, tiers and job estimation.
 *
 * Model: 1 credit = US$1. Deliberately transparent — an opaque credit unit
 * invites the suspicion that the exchange rate moves.
 *
 * A job is estimated before submission and the user approves the estimate.
 * The approved figure is a **cap**: settlement charges the lesser of actual
 * and approved. The user is never billed more than they agreed to, which
 * removes the main objection to consumption pricing.
 *
 * Mode matters: a manual job never runs the LLM stages, so it is not charged
 * for them. See docs/architecture/06-billing-and-metering.md and
 * docs/adr/0006-credit-hold-settle-ledger.md.
 */

import type {
  Asset,
  JobMode,
  CreditEstimate,
  CreditPack,
  EstimateLine,
  Tier,
  TierId,
} from "./types";
import { framesToSeconds } from "./timecode";

/** Vendor-passthrough-ish; same for every tier. */
export const TRANSCRIPTION_RATE_PER_HOUR = 3.5;

/** Flat per-job cost for assembly and artifact generation. */
export const ARTIFACT_FLAT = 1;

/** No job costs less than this, however short. */
export const MINIMUM_CHARGE = 2;

export const TIERS: Record<TierId, Tier> = {
  starter: {
    id: "starter",
    name: "Starter",
    blurb: "For solo creators finding their workflow.",
    monthlyPrice: 0,
    creditRatePerSourceHour: 12,
    maxSourceHours: 2,
    concurrentJobs: 1,
    retentionDays: 7,
    sso: false,
    features: [
      "FCPXML and EDL output",
      "Transcript page with rationale",
      "Up to 2 hours of source per job",
      "1 job at a time",
      "7-day media retention",
    ],
  },
  pro: {
    id: "pro",
    name: "Pro",
    blurb: "For working editors and small production teams.",
    monthlyPrice: 49,
    creditRatePerSourceHour: 9,
    maxSourceHours: 6,
    concurrentJobs: 3,
    retentionDays: 30,
    sso: false,
    features: [
      "Everything in Starter",
      "AAF output for Avid Media Composer",
      "AAF sequence ingest with embedded media",
      "Audio-only ingest",
      "Up to 6 hours of source per job",
      "3 concurrent jobs",
      "30-day media retention",
    ],
  },
  studio: {
    id: "studio",
    name: "Studio",
    blurb: "For broadcasters and post houses.",
    monthlyPrice: null,
    creditRatePerSourceHour: 7,
    maxSourceHours: 12,
    concurrentJobs: 10,
    retentionDays: 90,
    sso: true,
    features: [
      "Everything in Pro",
      "SAML SSO and directory sync",
      "Configurable retention and hard delete",
      "Audit log export",
      "Up to 12 hours of source per job",
      "10 concurrent jobs",
      "Priority support",
    ],
  },
};

export const CREDIT_PACKS: CreditPack[] = [
  { id: "pack_50", amount: 50, credits: 50, bonus: 0, label: "Top up" },
  { id: "pack_100", amount: 100, credits: 105, bonus: 5, label: "Most popular" },
  { id: "pack_200", amount: 200, credits: 220, bonus: 20, label: "Best value" },
];

/** Engine rate is whatever the tier rate is, less the fixed transcription rate. */
export function engineRatePerHour(tier: Tier): number {
  return Math.max(0, tier.creditRatePerSourceHour - TRANSCRIPTION_RATE_PER_HOUR);
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/**
 * Estimate the credits a job will consume.
 *
 * Everything scales with source duration, because that is what actually drives
 * cost: transcription is billed per minute of audio and the edit engine's token
 * count is a function of transcript length. Target cut length does not affect
 * the price — the work is in reading three hours, not in writing ten minutes.
 */
export function estimateJob(params: {
  asset: Pick<Asset, "durationFrames" | "rate" | "audioTracks">;
  tier: Tier;
  balance: number;
  mode?: JobMode;
}): CreditEstimate {
  const { asset, tier, balance, mode = "ai" } = params;
  const seconds = framesToSeconds(asset.durationFrames, asset.rate);
  const sourceHours = seconds / 3600;

  const transcription = sourceHours * TRANSCRIPTION_RATE_PER_HOUR;
  const engine = sourceHours * engineRatePerHour(tier);

  const lines: EstimateLine[] = [
    {
      label: "Transcription and alignment",
      detail: `${sourceHours.toFixed(2)} source hours at ${TRANSCRIPTION_RATE_PER_HOUR} credits/hour`,
      credits: round2(transcription),
    },
  ];

  // Manual mode skips stages 5-8 entirely — no brief compilation, no scoring,
  // no solver, no review pass. That is the whole LLM cost, so it should not be
  // charged for. Pricing that reflects what actually ran is easier to defend
  // than a flat rate, and it makes manual mode genuinely attractive for editors
  // who would rather drive the selection themselves.
  if (mode !== "manual") {
    lines.push({
      label: "Edit engine",
      detail: `Scoring and selection at ${engineRatePerHour(tier)} credits/hour · ${tier.name} rate`,
      credits: round2(engine),
    });
  }

  lines.push({
    label: "Assembly and artifacts",
    detail: "AAF, FCPXML, EDL and transcript",
    credits: ARTIFACT_FLAT,
  });

  if (asset.audioTracks > 2) {
    const extra = round2(sourceHours * 0.5 * (asset.audioTracks - 2));
    lines.push({
      label: "Additional audio tracks",
      detail: `${asset.audioTracks} tracks transcribed separately`,
      credits: extra,
    });
  }

  const subtotal = round2(lines.reduce((a, l) => a + l.credits, 0));
  const cap = Math.max(MINIMUM_CHARGE, Math.ceil(subtotal));

  return {
    mode,
    sourceDurationFrames: asset.durationFrames,
    sourceHours: round2(sourceHours),
    lines,
    subtotal,
    cap,
    balanceBefore: balance,
    balanceAfter: round2(balance - cap),
    sufficient: balance >= cap,
    shortfall: balance >= cap ? 0 : round2(cap - balance),
  };
}

/** Smallest pack that clears a shortfall. */
export function packForShortfall(shortfall: number): CreditPack {
  return (
    CREDIT_PACKS.find((p) => p.credits >= shortfall) ??
    CREDIT_PACKS[CREDIT_PACKS.length - 1]
  );
}

export function formatCredits(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}
