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
 * Estimate the credits a job will consume, across every source it draws on.
 *
 * Everything scales with source duration, because that is what actually drives
 * cost: transcription is billed per minute of audio and the edit engine's token
 * count is a function of transcript length. Target cut length does not affect
 * the price — the work is in reading three hours, not in writing ten minutes.
 *
 * `assets` is a list because a job has been cut from several uploads since B2.
 * This took one asset, and so did the API, so the displayed price and the
 * charged price agreed with each other and both under-charged a multi-source
 * job by everything after the first reel. Mirrors
 * `apps/api/src/mishne/billing/credits.py`; the two are checked against each
 * other and must be changed together.
 *
 * Transcription and the engine are summed across sources. **Artifacts are flat
 * and charged once** — a job emits one AAF, one FCPXML, one EDL and one
 * transcript however many uploads it was cut from. Extra audio tracks are per
 * asset, priced on that asset's own duration.
 */
type EstimateAsset = Pick<Asset, "durationFrames" | "rate" | "audioTracks">;

export function estimateJob(params: {
  assets: EstimateAsset | EstimateAsset[];
  tier: Tier;
  balance: number;
  mode?: JobMode;
}): CreditEstimate {
  const { tier, balance, mode = "ai" } = params;
  const sources = Array.isArray(params.assets) ? params.assets : [params.assets];
  if (sources.length === 0) {
    throw new Error("a job needs at least one asset to be priced");
  }
  const secondsOf = (a: EstimateAsset) => framesToSeconds(a.durationFrames, a.rate);
  const sourceHours = sources.reduce((a, s) => a + secondsOf(s), 0) / 3600;

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

  // Per asset, on that asset's own duration: the extra tracks belong to the
  // upload that has them, not to the job's total.
  const multitrack = sources.filter((a) => a.audioTracks > 2);
  if (multitrack.length > 0) {
    const extra = round2(
      multitrack.reduce(
        (a, s) => a + (secondsOf(s) / 3600) * 0.5 * (s.audioTracks - 2),
        0,
      ),
    );
    lines.push({
      label: "Additional audio tracks",
      detail:
        multitrack.length === 1
          ? `${multitrack[0].audioTracks} tracks transcribed separately`
          : `${multitrack.length} sources with more than two tracks`,
      credits: extra,
    });
  }

  const subtotal = round2(lines.reduce((a, l) => a + l.credits, 0));
  const cap = Math.max(MINIMUM_CHARGE, Math.ceil(subtotal));

  return {
    mode,
    // At the FIRST source's rate. Summing raw frame counts across sources at
    // different rates adds numbers that do not mean the same thing, and
    // conforming to the first rate is what assembly does with a mixed-rate
    // project — so this matches the timeline the customer gets.
    sourceDurationFrames: Math.round(
      (sourceHours * 3600 * sources[0].rate.num) / sources[0].rate.den,
    ),
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
