"use client";

import { useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Filter,
  Sparkles,
  Trash2,
  Undo2,
  Wand2,
} from "lucide-react";
import {
  assetOf,
  formatDuration,
  formatTimecode,
  framesToSeconds,
  type Beat,
  type JobMode,
  type Transcript,
  directionFor,
  speakerRoster,
} from "@mishne/shared";
import { SpeakerLegend, speakerColor } from "@/components/speaker-legend";
import { useTranscript } from "@/lib/use-transcript";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { apiSend } from "@/lib/dto";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const FLAG_STYLE: Record<string, string> = {
  filler: "text-flag-filler border-flag-filler/35",
  false_start: "text-flag-filler border-flag-filler/35",
  retake: "text-flag-retake border-flag-retake/35",
  crosstalk: "text-flag-retake border-flag-retake/35",
  low_confidence: "text-flag-lowconf border-flag-lowconf/35",
  off_mic: "text-flag-lowconf border-flag-lowconf/35",
};

/**
 * Text-based cut editor.
 *
 * The left pane is the full transcript; the right pane is the cut, in order.
 * Selecting a beat adds it to the cut, and the cut can be reordered
 * independently of source order — which is the entire point, since a rough cut
 * is rarely chronological.
 *
 * In hybrid mode the engine's selection is the starting state and every change
 * is a diff against it, so "reset to the AI's cut" is always one click away.
 */
export function CutEditor({
  transcript: initial,
  mode,
  targetDurationS,
  jobId,
}: {
  transcript: Transcript;
  mode: JobMode;
  targetDurationS: number;
  /** Omitted in a preview: without it the editor is a sketch that cannot be
   *  submitted, which is better than a button that silently does nothing. */
  jobId?: string;
}) {
  const { transcript, rename, merge, error: speakerError } = useTranscript(
    initial, jobId
  );
  const roster = transcript.speakers;

  // The suggestion a hybrid job arrived with. Read from the beats rather than
  // held separately: `used` and `orderIdx` are the job's own record of the cut
  // it made, and this screen is where a person changes it.
  const aiOrder = useMemo(
    () =>
      transcript.beats
        .filter((b) => b.used)
        .sort((a, b) => (a.orderIdx ?? 0) - (b.orderIdx ?? 0))
        .map((b) => b.id),
    [transcript.beats]
  );

  const [order, setOrder] = useState<string[]>(mode === "manual" ? [] : aiOrder);
  const [speaker, setSpeaker] = useState("all");
  const [hideFlagged, setHideFlagged] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  /**
   * The cut, to the API, in the order on screen.
   *
   * Only the ids and their order are sent. Where each clip actually starts and
   * ends is stage 9's answer, not the browser's: cut points snap to real
   * silence and handles are added, and a UI that sent frames would be
   * proposing an edit the pipeline is about to overrule.
   */
  const submit = async () => {
    if (!jobId) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiSend(`/v1/jobs/${jobId}/cut`, { json: { beat_ids: order } });
      // Back to the job, which is where the stages it still has to run are
      // shown. The editor has nothing left to say once the cut is in.
      router.push(`/jobs/${jobId}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : String(cause));
      setSubmitting(false);
    }
  };

  // Named voices, and their colours. A beat whose speaker is not in the roster
  // reads as unattributed rather than as a raw id — see `speakerRoster`.
  const voices = useMemo(() => speakerRoster(transcript), [transcript]);
  const nameOf = (id: string) => voices.nameOf(id);

  const byId = useMemo(
    () => new Map(transcript.beats.map((b) => [b.id, b])),
    [transcript.beats]
  );

  const selected = new Set(order);
  const multiAsset = transcript.assets.length > 1;

  // Every duration is read against the beat's own reel. Summing raw frame
  // counts across a 25 and a 23.976 reel is how a "ten minute" cut turns out
  // to be ten minutes and eleven seconds.
  const cutSeconds = order.reduce((a, id) => {
    const b = byId.get(id)!;
    return a + framesToSeconds(b.endFrames - b.startFrames, assetOf(transcript, b).rate);
  }, 0);
  const cutS = cutSeconds;
  // A transcription job has no target: the field is a selection parameter, it
  // does not move the price, and nothing reads it before this screen — so the
  // wizard stops asking for one and sends 0. Without this the delta is the
  // whole cut length and the bar's width is `cutS / 0`.
  const hasTarget = targetDurationS > 0;
  const delta = cutS - targetDurationS;
  const onTarget = !hasTarget || Math.abs(delta) <= 30;

  const dirty = useMemo(
    () => JSON.stringify(order) !== JSON.stringify(aiOrder),
    [order, aiOrder]
  );

  const visible = transcript.beats.filter((b) => {
    if (speaker !== "all" && b.speaker !== speaker) return false;
    if (hideFlagged && b.flags.length > 0 && !selected.has(b.id)) return false;
    return true;
  });

  const toggle = (id: string) =>
    setOrder((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );

  const move = (i: number, dir: -1 | 1) =>
    setOrder((prev) => {
      const next = [...prev];
      const j = i + dir;
      if (j < 0 || j >= next.length) return prev;
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });

  return (
    <div className="space-y-5">
      <SpeakerLegend
        speakers={roster}
        attribution={transcript.attribution}
        onRename={rename}
        onMerge={jobId ? merge : undefined}
      />

      <div className="grid gap-5 lg:grid-cols-[1fr_400px]">
      {/* ------------------------------------------------ transcript pane */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Filter className="size-3.5 text-muted-foreground" />
          <div className="flex gap-1">
            {[{ id: "all", label: "All speakers" },
              ...roster.map((s) => ({ id: s.id, label: nameOf(s.id) }))].map((s) => (
              <button
                key={s.id}
                onClick={() => setSpeaker(s.id)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs transition-colors",
                  speaker === s.id
                    ? "bg-secondary text-secondary-foreground"
                    : "text-muted-foreground hover:bg-accent/50"
                )}
              >
                {s.label}
              </button>
            ))}
          </div>
          <span className="mx-1 h-4 w-px bg-border" />
          <button
            onClick={() => setHideFlagged((v) => !v)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs transition-colors",
              hideFlagged
                ? "bg-secondary text-secondary-foreground"
                : "text-muted-foreground hover:bg-accent/50"
            )}
          >
            {hideFlagged ? "Hiding" : "Showing"} filler and false starts
          </button>
        </div>

        <div className="space-y-1" dir={directionFor(transcript.language)}>
          {visible.map((b) => {
            const on = selected.has(b.id);
            const pos = order.indexOf(b.id);
            return (
              <button
                key={b.id}
                onClick={() => toggle(b.id)}
                className={cn(
                  // `text-start`, not `text-left`: a Hebrew transcript sets
                  // `dir="rtl"` on the list and the lines have to align with
                  // the edge a Hebrew reader starts from. `text-left` pinned
                  // every line to the far side of the row from its timecode.
                  "flex w-full items-start gap-3 rounded-md border p-3 text-start transition-colors",
                  on
                    ? "border-used/40 bg-used-surface/30"
                    : "border-transparent hover:border-border hover:bg-accent/20"
                )}
              >
                <div className="w-[86px] shrink-0 pt-0.5">
                  <div className="tc text-[11px] text-timecode">
                    {formatTimecode(b.startFrames, assetOf(transcript, b).rate, assetOf(transcript, b).dropFrame)}
                  </div>
                  <div className="text-[11px] text-muted-foreground/70">
                    {framesToSeconds(b.endFrames - b.startFrames, assetOf(transcript, b).rate).toFixed(1)}s
                  </div>
                  {multiAsset && (
                    <div dir="ltr" className="truncate text-[10px] text-muted-foreground/60" title={assetOf(transcript, b).filename}>
                      {assetOf(transcript, b).filename}
                    </div>
                  )}
                </div>
                <span
                  className={cn(
                    "mt-0.5 grid size-4 shrink-0 place-items-center rounded-[4px] border text-[9px] font-semibold",
                    on
                      ? "border-used bg-used text-background"
                      : "border-border"
                  )}
                >
                  {on ? pos + 1 : ""}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex flex-wrap items-center gap-1.5">
                    <span
                      className={cn(
                        "flex items-center gap-1.5 text-xs font-medium",
                        voices.has(b.speaker)
                          ? "text-muted-foreground"
                          : "italic text-muted-foreground/60"
                      )}
                    >
                      {voices.has(b.speaker) && (
                        <span
                          className="size-2 shrink-0 rounded-full"
                          style={{
                            background: speakerColor(voices.indexOf(b.speaker)),
                          }}
                        />
                      )}
                      {nameOf(b.speaker)}
                    </span>
                    {b.flags.map((f) => (
                      <Badge
                        key={f}
                        variant="outline"
                        className={cn("text-[10px]", FLAG_STYLE[f])}
                      >
                        {f.replace(/_/g, " ")}
                      </Badge>
                    ))}
                    {mode !== "manual" && b.used && (
                      <span
                        className="flex items-center gap-0.5 text-[10px] text-primary"
                        title={b.rationale}
                      >
                        <Sparkles className="size-2.5" /> suggested
                      </span>
                    )}
                  </div>
                  <p
                    dir="auto"
                    className={cn(
                      "text-sm leading-relaxed",
                      on ? "text-foreground" : "text-unused-foreground"
                    )}
                  >
                    {b.text}
                  </p>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* ------------------------------------------------------- cut pane */}
      <div className="space-y-3 lg:sticky lg:top-20 lg:self-start">
        <Card className="p-4">
          <div className="flex items-baseline justify-between">
            <span className="text-sm text-muted-foreground">Cut length</span>
            <span
              className={cn(
                "tc text-2xl font-semibold",
                onTarget ? "text-used" : "text-flag-lowconf"
              )}
            >
              {formatDuration(cutS)}
            </span>
          </div>
          {hasTarget ? (
            <>
              <div className="mt-1 flex items-baseline justify-between text-xs text-muted-foreground">
                <span>target {formatDuration(targetDurationS)}</span>
                <span className={cn("tc", onTarget ? "text-used" : "text-flag-lowconf")}>
                  {delta >= 0 ? "+" : "−"}
                  {formatDuration(Math.abs(delta))}
                </span>
              </div>
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-secondary">
                <div
                  className={cn(
                    "h-full rounded-full transition-all",
                    onTarget ? "bg-used" : "bg-flag-lowconf"
                  )}
                  style={{
                    width: `${Math.min(100, (cutS / targetDurationS) * 100)}%`,
                  }}
                />
              </div>
            </>
          ) : (
            <div className="mt-1 text-xs text-muted-foreground">
              No target length — this cut is whatever you make it.
            </div>
          )}
          <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
            <span>{order.length} segments</span>
            {mode !== "manual" && dirty && (
              <button
                onClick={() => setOrder(aiOrder)}
                className="flex items-center gap-1 hover:text-foreground"
              >
                <Undo2 className="size-3" /> Reset to suggestion
              </button>
            )}
          </div>
        </Card>

        <Card className="max-h-[52vh] overflow-y-auto p-2">
          {order.length === 0 ? (
            <div className="p-6 text-center text-sm text-muted-foreground">
              {mode === "manual" ? (
                <>
                  <Wand2 className="mx-auto mb-2 size-5 opacity-40" />
                  Nothing selected yet. Click any line on the left to add it to the cut.
                </>
              ) : (
                "The cut is empty. Reset to the suggestion or pick lines yourself."
              )}
            </div>
          ) : (
            <ol className="space-y-1">
              {order.map((id, i) => {
                const b = byId.get(id)!;
                return (
                  <li
                    key={id}
                    className="group flex items-start gap-2 rounded-md p-2 hover:bg-accent/30"
                  >
                    <span className="tc mt-0.5 w-5 shrink-0 text-[11px] text-muted-foreground">
                      {i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="tc text-[10px] text-timecode">
                        {formatTimecode(b.startFrames, assetOf(transcript, b).rate, assetOf(transcript, b).dropFrame)} ·{" "}
                        {framesToSeconds(b.endFrames - b.startFrames, assetOf(transcript, b).rate).toFixed(1)}s
                        {multiAsset && (
                          <span dir="ltr" className="ms-1 text-muted-foreground/60">
                            {assetOf(transcript, b).filename}
                          </span>
                        )}
                      </div>
                      <p dir="auto" className="mt-0.5 line-clamp-2 text-xs leading-snug">{b.text}</p>
                    </div>
                    <div className="flex shrink-0 flex-col opacity-0 transition-opacity group-hover:opacity-100">
                      <button
                        onClick={() => move(i, -1)}
                        className="rounded p-0.5 hover:bg-accent"
                        aria-label="Move up"
                      >
                        <ArrowUp className="size-3" />
                      </button>
                      <button
                        onClick={() => move(i, 1)}
                        className="rounded p-0.5 hover:bg-accent"
                        aria-label="Move down"
                      >
                        <ArrowDown className="size-3" />
                      </button>
                    </div>
                    <button
                      onClick={() => toggle(id)}
                      className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                      aria-label="Remove"
                    >
                      <Trash2 className="size-3" />
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </Card>

        <Button
          className="w-full"
          onClick={submit}
          disabled={order.length === 0 || submitting || !jobId}
        >
          <Check />{" "}
          {submitting ? "Submitting…" : "Assemble and generate artifacts"}
        </Button>
        {(error || speakerError) && (
          <p className="text-center text-sm text-destructive" role="alert">
            {error ?? speakerError}
          </p>
        )}
        <p className="text-center text-xs text-muted-foreground">
          Cut points snap to silence and handles are added automatically.
        </p>
      </div>
      </div>
    </div>
  );
}
