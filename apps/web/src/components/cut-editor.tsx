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
  formatDuration,
  formatTimecode,
  framesToSeconds,
  type Beat,
  type JobMode,
  type Speaker,
  type Transcript,
  directionFor,
} from "@mishne/shared";
import { SpeakerLegend, speakerColor } from "@/components/speaker-legend";
import { Button } from "@/components/ui/button";
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
  transcript,
  mode,
  targetDurationS,
}: {
  transcript: Transcript;
  mode: JobMode;
  targetDurationS: number;
}) {
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
  const [roster, setRoster] = useState<Speaker[]>(transcript.speakers);

  const rename = (id: string, label: string) =>
    setRoster((prev) =>
      prev.map((s) =>
        s.id === id ? { ...s, label, confirmed: label.length > 0 } : s
      )
    );

  const speakerById = useMemo(
    () => new Map(roster.map((s, i) => [s.id, { speaker: s, index: i }])),
    [roster]
  );
  const nameOf = (id: string) =>
    speakerById.get(id)?.speaker.label ||
    speakerById.get(id)?.speaker.defaultLabel ||
    id;

  const byId = useMemo(
    () => new Map(transcript.beats.map((b) => [b.id, b])),
    [transcript.beats]
  );

  const selected = new Set(order);
  const { rate, dropFrame } = transcript;

  const cutFrames = order.reduce((a, id) => {
    const b = byId.get(id)!;
    return a + (b.endFrames - b.startFrames);
  }, 0);
  const cutS = framesToSeconds(cutFrames, rate);
  const delta = cutS - targetDurationS;
  const onTarget = Math.abs(delta) <= 30;

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
                  "flex w-full items-start gap-3 rounded-md border p-3 text-left transition-colors",
                  on
                    ? "border-used/40 bg-used-surface/30"
                    : "border-transparent hover:border-border hover:bg-accent/20"
                )}
              >
                <div className="w-[86px] shrink-0 pt-0.5">
                  <div className="tc text-[11px] text-timecode">
                    {formatTimecode(b.startFrames, rate, dropFrame)}
                  </div>
                  <div className="text-[11px] text-muted-foreground/70">
                    {framesToSeconds(b.endFrames - b.startFrames, rate).toFixed(1)}s
                  </div>
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
                    <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <span
                        className="size-2 shrink-0 rounded-full"
                        style={{
                          background: speakerColor(
                            speakerById.get(b.speaker)?.index ?? 0
                          ),
                        }}
                      />
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
                        {formatTimecode(b.startFrames, rate, dropFrame)} ·{" "}
                        {framesToSeconds(b.endFrames - b.startFrames, rate).toFixed(1)}s
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

        <Button className="w-full" disabled={order.length === 0}>
          <Check /> Assemble and generate artifacts
        </Button>
        <p className="text-center text-xs text-muted-foreground">
          Cut points snap to silence and handles are added automatically.
        </p>
      </div>
      </div>
    </div>
  );
}
