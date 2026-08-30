"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  FileVideo,
  FileAudio,
  Layers,
  Info,
  AlertTriangle,
  Sparkles,
  PenLine,
  Blend,
} from "lucide-react";
import {
  TIERS,
  estimateJob,
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
import { cn } from "@/lib/utils";

const KIND_ICON = { video: FileVideo, audio: FileAudio, aaf: Layers } as const;

const SHAPES: Array<{ id: NarrativeShape; label: string; hint: string }> = [
  { id: "inverted_pyramid", label: "Inverted pyramid", hint: "Strongest material first" },
  { id: "chronological", label: "Chronological", hint: "Source order preserved" },
  { id: "thematic", label: "Thematic", hint: "Grouped by topic" },
  { id: "q_and_a", label: "Q & A", hint: "Question and answer pairs kept together" },
];

const TONES = ["conversational", "urgent", "warm", "reflective", "authoritative", "punchy"];

const MODES: Array<{
  id: JobMode;
  label: string;
  hint: string;
  icon: typeof Sparkles;
}> = [
  {
    id: "ai",
    label: "AI rough cut",
    hint: "Describe the piece and get a finished rough cut back.",
    icon: Sparkles,
  },
  {
    id: "hybrid",
    label: "AI draft, then edit",
    hint: "The engine proposes a cut, you adjust it before assembly.",
    icon: Blend,
  },
  {
    id: "manual",
    label: "Transcribe only",
    hint: "Just the transcript. You mark the cut yourself. Costs less.",
    icon: PenLine,
  },
];

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
  const [step, setStep] = useState(0);
  const [mode, setMode] = useState<JobMode>("ai");
  const [assetId, setAssetId] = useState(assets[0]?.id ?? "");
  const [targetMinutes, setTargetMinutes] = useState(10);
  const [shape, setShape] = useState<NarrativeShape>("inverted_pyramid");
  const [tones, setTones] = useState<string[]>(["conversational"]);
  const [notes, setNotes] = useState("");
  const [approved, setApproved] = useState(false);

  const tier = TIERS[tierId];
  const asset = assets.find((a) => a.id === assetId);

  const estimate = useMemo(
    () => (asset ? estimateJob({ assets: [asset], tier, balance, mode }) : null),
    [asset, tier, balance, mode]
  );

  const canAdvance = step === 0 ? Boolean(asset) : true;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <Link
          href={`/projects/${project.id}`}
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" /> {project.name}
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">New rough cut</h1>
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
            <CardTitle>Choose source material</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {assets.map((a) => {
              const Icon = KIND_ICON[a.kind];
              const selected = a.id === assetId;
              return (
                <button
                  key={a.id}
                  onClick={() => setAssetId(a.id)}
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
                    <div className="text-sm font-medium">{m.label}</div>
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
              {mode === "manual" ? "Target length" : "Production notes"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
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
      {step === 3 && estimate && asset && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Estimated cost</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="rounded-md bg-muted/50 p-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Source</span>
                  <span className="truncate pl-4">{asset.filename}</span>
                </div>
                <div className="mt-1 flex justify-between">
                  <span className="text-muted-foreground">Method</span>
                  <span>{MODES.find((m) => m.id === mode)!.label}</span>
                </div>
                <div className="mt-1 flex justify-between">
                  <span className="text-muted-foreground">Duration</span>
                  <span className="tc">
                    {formatDuration(framesToSeconds(asset.durationFrames, asset.rate))} (
                    {estimate.sourceHours.toFixed(2)} h)
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
          <Button disabled={!approved || !estimate?.sufficient}>
            <Check />{" "}
            {mode === "ai" ? "Approve and submit" : "Approve and transcribe"}
          </Button>
        )}
      </div>
    </div>
  );
}
