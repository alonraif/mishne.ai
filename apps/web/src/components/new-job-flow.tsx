"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Info,
  AlertTriangle,
  Sparkles,
  PenLine,
  Blend,
} from "lucide-react";
import {
  JOB_MODE_LABEL,
  TIERS,
  formatCredits,
  formatDuration,
  framesToSeconds,
  packForShortfall,
  type Asset,
  type JobMode,
  type NarrativeShape,
  type Project,
  type TierId,
} from "@mishne/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { AssetUpload } from "@/components/asset-upload";
import { KIND_ICON } from "@/components/asset-meta";
import { ApiError } from "@/lib/api";
import { apiSend } from "@/lib/dto";
import { cn } from "@/lib/utils";
import type { CreditEstimate } from "@mishne/shared";


const SHAPES: Array<{ id: NarrativeShape; label: string; hint: string }> = [
  { id: "inverted_pyramid", label: "Inverted pyramid", hint: "Strongest material first" },
  { id: "chronological", label: "Chronological", hint: "Source order preserved" },
  { id: "thematic", label: "Thematic", hint: "Grouped by topic" },
  { id: "q_and_a", label: "Q & A", hint: "Question and answer pairs kept together" },
];

const TONES = ["conversational", "urgent", "warm", "reflective", "authoritative", "punchy"];

/**
 * The language of the material, asked rather than guessed.
 *
 * This is the transcription routing decision (ADR-0018) and it has to be made
 * before a word has been transcribed, so nothing downstream can supply it. An
 * unanswered question here is treated as unidentified audio, not as English —
 * which is the safe reading and the expensive one.
 *
 * The list is the languages the engines publish, Hebrew first because it is a
 * first-class target for this product. `other` exists so a language nobody
 * listed is still submittable: it routes exactly as an unspecified language
 * would, to the engine with general coverage.
 */
const LANGUAGES: Array<{ code: string; label: string }> = [
  { code: "he", label: "Hebrew" },
  { code: "en", label: "English" },
  { code: "ar", label: "Arabic" },
  { code: "cs", label: "Czech" },
  { code: "da", label: "Danish" },
  { code: "nl", label: "Dutch" },
  { code: "fil", label: "Filipino" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "hi", label: "Hindi" },
  { code: "id", label: "Indonesian" },
  { code: "it", label: "Italian" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
  { code: "mk", label: "Macedonian" },
  { code: "ms", label: "Malay" },
  { code: "fa", label: "Persian" },
  { code: "pl", label: "Polish" },
  { code: "pt", label: "Portuguese" },
  { code: "ro", label: "Romanian" },
  { code: "ru", label: "Russian" },
  { code: "es", label: "Spanish" },
  { code: "sv", label: "Swedish" },
  { code: "th", label: "Thai" },
  { code: "tr", label: "Turkish" },
  { code: "vi", label: "Vietnamese" },
  { code: "und", label: "Something else / not sure" },
];

/**
 * The label is `JOB_MODE_LABEL`, not a second wording of it: this form is
 * where a customer learns what the three modes are called, and the job list is
 * where they read it back. Only the hint lives here, because only this screen
 * has room for a sentence.
 */
const MODES: Array<{ id: JobMode; hint: string; icon: typeof Sparkles }> = [
  {
    id: "ai",
    hint: "Describe the piece and get a finished rough cut back.",
    icon: Sparkles,
  },
  {
    id: "hybrid",
    hint: "The engine proposes a cut, you adjust it before assembly.",
    icon: Blend,
  },
  {
    id: "manual",
    hint: "Just the transcript. You mark the cut yourself. Costs less.",
    icon: PenLine,
  },
];

// Matches `JOB_NAME_MAX` in the API, which refuses anything longer.
const NAME_MAX = 120;

/**
 * A name for a job the customer has not named yet.
 *
 * Derived from the first source file rather than left blank, because a
 * required empty field on step three of four is a wall, and because the name
 * it produces is the one the API would have chosen anyway. It is a starting
 * point in an editable box, not a decision made on the customer's behalf.
 */
function suggestedName(filename: string | undefined): string {
  if (!filename) return "";
  const stem = filename.replace(/\.[^./\\]+$/, "").trim();
  return stem.slice(0, NAME_MAX);
}

const STEPS = ["Source", "Method", "Brief", "Estimate"] as const;

export function NewJobFlow({
  project,
  assets,
  balance,
  tierId,
}: {
  project: Project;
  assets: Asset[];
  balance: number;
  tierId: TierId;
}) {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [mode, setMode] = useState<JobMode>("ai");
  // A cut is made from every upload the editor chose, in order — footage over
  // weeks, one finished piece (ADR-0008). Pricing on the first one is what C1
  // found: wrong in the customer's favour, and consistently so at both ends,
  // which is why nobody noticed.
  const [assetIds, setAssetIds] = useState<string[]>(
    assets[0] ? [assets[0].id] : []
  );
  // Left empty until the customer reaches the brief, where it is seeded from
  // whatever they picked on step one. Seeding it at mount would name the job
  // after an asset they may be about to deselect.
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [targetMinutes, setTargetMinutes] = useState(10);
  const [language, setLanguage] = useState("en");
  const [shape, setShape] = useState<NarrativeShape>("inverted_pyramid");
  const [tones, setTones] = useState<string[]>(["conversational"]);
  const [notes, setNotes] = useState("");
  const [approved, setApproved] = useState(false);
  const [estimate, setEstimate] = useState<CreditEstimate | null>(null);
  const [pricing, setPricing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const tier = TIERS[tierId];
  const chosen = assets.filter((a) => assetIds.includes(a.id));

  /**
   * The price comes from the API, not from `estimateJob` in the browser.
   *
   * They implement the same rules and that is exactly the trap: submission
   * recomputes server-side and refuses a cap that no longer matches, so a
   * browser that priced it itself can show a number the API will reject —
   * and the customer sees an approval screen followed by a 409 they cannot
   * act on. One source for the number the customer approves.
   */
  useEffect(() => {
    if (assetIds.length === 0) {
      setEstimate(null);
      return;
    }
    let cancelled = false;
    setPricing(true);
    apiSend<CreditEstimate>("/v1/jobs/estimate", {
      json: {
        asset_ids: assetIds,
        target_duration_s: targetMinutes * 60,
        mode,
      },
    })
      .then((next) => !cancelled && (setEstimate(next), setError(null)))
      .catch((cause) => {
        if (cancelled) return;
        setEstimate(null);
        setError(cause instanceof ApiError ? cause.detail : String(cause));
      })
      .finally(() => !cancelled && setPricing(false));
    return () => {
      cancelled = true;
    };
    // `assetIds` is a new array identity every render if inlined; joined so the
    // effect runs when the selection changes rather than when React re-renders.
  }, [assetIds.join(","), targetMinutes, mode]);

  // Approving a number and then changing the job is how somebody is charged for
  // something they did not agree to. Any change to what is being priced clears
  // the approval.
  useEffect(() => setApproved(false), [estimate?.cap]);

  // The suggestion follows the source selection right up until the customer
  // edits the field, and then it stops — an input that rewrites itself under
  // somebody who is typing in it is worse than no suggestion at all.
  const firstChosen = assets.find((a) => a.id === assetIds[0]);
  useEffect(() => {
    if (!nameTouched) setName(suggestedName(firstChosen?.filename));
  }, [firstChosen?.filename, nameTouched]);

  const submit = async () => {
    if (!estimate) return;
    setSubmitting(true);
    setError(null);
    try {
      const job = await apiSend<{ id: string }>("/v1/jobs", {
        json: {
          asset_ids: assetIds,
          name: name.trim(),
          mode,
          notes,
          target_duration_s: targetMinutes * 60,
          narrative_shape: shape,
          tone: tones,
          // Sent in every mode, transcribe-only included: the transcript is
          // what a manual job is for, and this is what decides which engine
          // writes it.
          language,
          // What the customer approved, sent so the API can compare it with
          // what it recomputes. It is a check, not the price.
          approved_cap: estimate.cap,
        },
      });
      router.push(`/jobs/${job.id}`);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.status === 409
            ? `${cause.detail} — the estimate has moved; go back and price it again.`
            : cause.detail
          : String(cause)
      );
      setSubmitting(false);
    }
  };

  // Step three is the only one that can be left in an unusable state: no
  // source is a job with nothing to cut, and no name is a job the customer
  // will be looking at as an id a week from now.
  const canAdvance =
    step === 0 ? chosen.length > 0 : step === 2 ? name.trim().length > 0 : true;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <Link
          href={`/projects/${project.id}`}
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" /> {project.name}
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">New job</h1>
      </div>

      {/* Step rail */}
      <div className="flex items-center gap-2">
        {STEPS.map((label, i) => (
          <div key={label} className="flex flex-1 items-center gap-2">
            <div
              className={cn(
                "flex items-center gap-2 text-sm",
                i === step ? "text-foreground" : "text-muted-foreground"
              )}
            >
              <span
                className={cn(
                  "grid size-5 place-items-center rounded-full border text-[11px]",
                  i < step && "border-stage-done/50 bg-stage-done/15 text-stage-done",
                  i === step && "border-primary bg-primary text-primary-foreground",
                  i > step && "border-border"
                )}
              >
                {i < step ? <Check className="size-3" /> : i + 1}
              </span>
              {label}
            </div>
            {i < STEPS.length - 1 && <div className="h-px flex-1 bg-border" />}
          </div>
        ))}
      </div>

      {/* ---------------------------------------------------------- source */}
      {step === 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              Choose source material
              {/* The same uploader as the project page, not a second one: an
                  upload started here resumes on the project page and the other
                  way round, because there is one implementation of it. */}
              <AssetUpload projectId={project.id} />
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {assets.length === 0 && (
              <p className="py-6 text-center text-sm text-muted-foreground">
                Nothing ready to cut yet. Upload footage, and it appears here
                once it has been probed.
              </p>
            )}
            {assets.map((a) => {
              const Icon = KIND_ICON[a.kind];
              const selected = assetIds.includes(a.id);
              return (
                <button
                  key={a.id}
                  onClick={() =>
                    setAssetIds((current) =>
                      current.includes(a.id)
                        ? current.filter((id) => id !== a.id)
                        : [...current, a.id]
                    )
                  }
                  className={cn(
                    "flex w-full items-center gap-3 rounded-md border p-3 text-left transition-colors",
                    selected
                      ? "border-primary bg-primary/5"
                      : "border-border hover:bg-accent/40"
                  )}
                >
                  <Icon className="size-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{a.filename}</div>
                    <div className="text-xs text-muted-foreground">
                      {a.codec} · {formatDuration(framesToSeconds(a.durationFrames, a.rate))} ·{" "}
                      {a.audioTracks} audio tracks
                    </div>
                  </div>
                  {selected && <Check className="size-4 shrink-0 text-primary" />}
                </button>
              );
            })}
          </CardContent>
        </Card>
      )}

      {/* ---------------------------------------------------------- method */}
      {step === 1 && (
        <Card>
          <CardHeader>
            <CardTitle>How should the cut be made?</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {MODES.map((m) => {
              const Icon = m.icon;
              const on = m.id === mode;
              return (
                <button
                  key={m.id}
                  onClick={() => setMode(m.id)}
                  className={cn(
                    "flex w-full items-start gap-3 rounded-md border p-4 text-left transition-colors",
                    on ? "border-primary bg-primary/5" : "border-border hover:bg-accent/40"
                  )}
                >
                  <Icon className={cn("mt-0.5 size-4 shrink-0", on ? "text-primary" : "text-muted-foreground")} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium">{JOB_MODE_LABEL[m.id]}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground">{m.hint}</div>
                  </div>
                  {on && <Check className="mt-0.5 size-4 shrink-0 text-primary" />}
                </button>
              );
            })}
            <p className="pt-2 text-xs text-muted-foreground">
              Every method produces the same artifacts — AAF, FCPXML, EDL and a
              transcript. Only the way the selection gets made differs.
            </p>
          </CardContent>
        </Card>
      )}

      {/* ----------------------------------------------------------- brief */}
      {step === 2 && (
        <Card>
          <CardHeader>
            <CardTitle>
              {mode === "manual" ? "Name and target length" : "Name and production notes"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Asked, not generated. A project holds several cuts of the same
                interview and the id tells nobody which is which — this is the
                only thing on the job list that will. */}
            <div className="grid gap-2">
              <Label htmlFor="job-name">Job name</Label>
              <Input
                id="job-name"
                value={name}
                maxLength={NAME_MAX}
                placeholder="Ep. 3 — web cut"
                aria-invalid={!name.trim()}
                onChange={(e) => {
                  setNameTouched(true);
                  setName(e.target.value);
                }}
              />
              {/* Said rather than only enforced: Continue is disabled while
                  this is empty, and a dead button with no explanation is a
                  dead end. */}
              <p
                className={cn(
                  "text-xs",
                  name.trim() ? "text-muted-foreground" : "text-destructive"
                )}
              >
                {name.trim()
                  ? "What this job is called everywhere you will look for it again. Suggested from the source file; change it to whatever you will recognise."
                  : "Give the job a name — it is how you will find it again in the job list."}
              </p>
            </div>

            <div className="grid gap-2">
              <Label htmlFor="target">Target length</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="target"
                  type="number"
                  min={1}
                  value={targetMinutes}
                  onChange={(e) => setTargetMinutes(Number(e.target.value))}
                  className="w-24 tc"
                />
                <span className="text-sm text-muted-foreground">minutes</span>
              </div>
              <p className="text-xs text-muted-foreground">
                {mode === "manual"
                  ? "Used to show you how close your cut is as you build it."
                  : "Target length does not affect price — the work is in reading the source, not in writing the cut."}
              </p>
            </div>

            {/* Every mode. A transcribe-only job is *entirely* this decision. */}
            <div className="grid gap-2">
              <Label htmlFor="language">Language</Label>
              <select
                id="language"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="h-9 w-full max-w-xs rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                {LANGUAGES.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                The language spoken in the source. It decides which engine
                transcribes it, so this is worth getting right — a transcript in
                the wrong language is not an error you will see, it is a
                plausible transcript of the wrong words.
              </p>
            </div>

            {mode !== "manual" && (
            <div className="grid gap-2">
              <Label>Structure</Label>
              <div className="grid gap-2 sm:grid-cols-2">
                {SHAPES.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => setShape(s.id)}
                    className={cn(
                      "rounded-md border p-3 text-left transition-colors",
                      shape === s.id
                        ? "border-primary bg-primary/5"
                        : "border-border hover:bg-accent/40"
                    )}
                  >
                    <div className="text-sm font-medium">{s.label}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground">{s.hint}</div>
                  </button>
                ))}
              </div>
            </div>
            )}

            {mode !== "manual" && (
            <div className="grid gap-2">
              <Label>Tone</Label>
              <div className="flex flex-wrap gap-2">
                {TONES.map((t) => {
                  const on = tones.includes(t);
                  return (
                    <button
                      key={t}
                      onClick={() =>
                        setTones((prev) =>
                          on ? prev.filter((x) => x !== t) : [...prev, t]
                        )
                      }
                      className={cn(
                        "rounded-md border px-3 py-1.5 text-sm capitalize transition-colors",
                        on
                          ? "border-primary bg-primary/10 text-foreground"
                          : "border-border text-muted-foreground hover:bg-accent/40"
                      )}
                    >
                      {t}
                    </button>
                  );
                })}
              </div>
            </div>
            )}

            {mode !== "manual" && (
            <div className="grid gap-2">
              <Label htmlFor="notes">Director&apos;s notes</Label>
              <Textarea
                id="notes"
                rows={6}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="What has to be in it, what has to stay out, who leads. Write it the way you would brief an assistant editor."
              />
              <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
                <Info className="mt-0.5 size-3 shrink-0" />
                Anything ambiguous is resolved with a documented default and shown back to
                you before the job runs.
              </p>
            </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* -------------------------------------------------------- estimate */}
      {step === 3 && pricing && !estimate && (
        <Card className="p-8 text-center text-sm text-muted-foreground">
          Pricing this job…
        </Card>
      )}

      {step === 3 && estimate && chosen.length > 0 && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Estimated cost</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="rounded-md bg-muted/50 p-3 text-sm">
                <div className="mb-1 flex justify-between gap-4">
                  <span className="shrink-0 text-muted-foreground">Name</span>
                  <span className="truncate text-right">{name}</span>
                </div>
                <div className="flex justify-between gap-4">
                  <span className="shrink-0 text-muted-foreground">
                    {chosen.length === 1 ? "Source" : `${chosen.length} sources`}
                  </span>
                  {/* Every upload the cut draws on, named. A job priced on
                      three reels that lists one reads as a mistake in the
                      customer's favour right up until it isn't. */}
                  <span className="truncate text-right" dir="ltr">
                    {chosen.map((a) => a.filename).join(" · ")}
                  </span>
                </div>
                <div className="mt-1 flex justify-between">
                  <span className="text-muted-foreground">Method</span>
                  <span>{JOB_MODE_LABEL[mode]}</span>
                </div>
                {/* On the screen where the customer approves a number: the
                    language is the one setting here they cannot correct after
                    the fact without paying for the source to be read again. */}
                <div className="mt-1 flex justify-between">
                  <span className="text-muted-foreground">Language</span>
                  <span>{LANGUAGES.find((l) => l.code === language)!.label}</span>
                </div>
                <div className="mt-1 flex justify-between">
                  <span className="text-muted-foreground">Duration</span>
                  <span className="tc">
                    {formatDuration(
                      chosen.reduce(
                        (total, a) => total + framesToSeconds(a.durationFrames, a.rate),
                        0
                      )
                    )}{" "}
                    ({estimate.sourceHours.toFixed(2)} h)
                  </span>
                </div>
              </div>

              <div className="space-y-2.5">
                {estimate.lines.map((l) => (
                  <div key={l.label} className="flex items-baseline justify-between gap-4">
                    <div className="min-w-0">
                      <div className="text-sm">{l.label}</div>
                      <div className="text-xs text-muted-foreground">{l.detail}</div>
                    </div>
                    <span className="tc shrink-0 text-sm">{formatCredits(l.credits)}</span>
                  </div>
                ))}
              </div>

              <Separator />

              <div className="flex items-baseline justify-between">
                <span className="text-sm text-muted-foreground">Subtotal</span>
                <span className="tc text-sm">{formatCredits(estimate.subtotal)}</span>
              </div>
              <div className="flex items-baseline justify-between">
                <span className="font-medium">Maximum charge</span>
                <span className="tc text-xl font-semibold">
                  {formatCredits(estimate.cap)} credits
                </span>
              </div>

              <div className="flex items-start gap-2 rounded-md border border-primary/25 bg-primary/5 p-3 text-xs">
                <Info className="mt-0.5 size-3.5 shrink-0 text-primary" />
                <p>
                  This figure is a cap, not a quote. You are charged for actual usage and
                  never more than the amount you approve here. If the job fails, nothing is
                  charged.
                </p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 pt-5">
              <div className="flex items-baseline justify-between text-sm">
                <span className="text-muted-foreground">Balance now</span>
                <span className="tc">{formatCredits(estimate.balanceBefore)}</span>
              </div>
              <div className="flex items-baseline justify-between text-sm">
                <span className="text-muted-foreground">After this job</span>
                <span
                  className={cn(
                    "tc",
                    !estimate.sufficient && "text-destructive"
                  )}
                >
                  {formatCredits(estimate.balanceAfter)}
                </span>
              </div>

              {!estimate.sufficient && (
                <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-destructive" />
                  <div>
                    <p className="font-medium text-destructive">
                      Not enough credits — {formatCredits(estimate.shortfall)} short.
                    </p>
                    <Button asChild size="sm" variant="outline" className="mt-2">
                      <Link href="/billing">
                        Buy {packForShortfall(estimate.shortfall).credits} credits
                      </Link>
                    </Button>
                  </div>
                </div>
              )}

              {estimate.sufficient && (
                <label className="flex cursor-pointer items-start gap-2.5 rounded-md border border-border p-3 text-sm transition-colors hover:bg-accent/30">
                  <input
                    type="checkbox"
                    checked={approved}
                    onChange={(e) => setApproved(e.target.checked)}
                    className="mt-0.5 size-4 accent-[oklch(0.65_0.17_274)]"
                  />
                  <span>
                    I approve a maximum charge of{" "}
                    <span className="tc font-medium">{formatCredits(estimate.cap)}</span>{" "}
                    credits for this job.
                  </span>
                </label>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* ------------------------------------------------------------- nav */}
      <div className="flex items-center justify-between">
        <Button
          variant="ghost"
          onClick={() => setStep((s) => Math.max(0, s - 1))}
          disabled={step === 0}
        >
          <ArrowLeft /> Back
        </Button>

        {step < 3 ? (
          <Button onClick={() => setStep((s) => s + 1)} disabled={!canAdvance}>
            Continue <ArrowRight />
          </Button>
        ) : (
          <Button
            onClick={submit}
            disabled={
              !approved || !estimate?.sufficient || submitting || pricing || !name.trim()
            }
          >
            <Check />{" "}
            {submitting
              ? "Submitting…"
              : mode === "ai"
                ? "Approve and submit"
                : "Approve and transcribe"}
          </Button>
        )}
      </div>

      {error && (
        <p className="text-right text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
