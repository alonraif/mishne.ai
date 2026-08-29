"use client";

import { useMemo, useState } from "react";
import { Check, Filter, Quote } from "lucide-react";
import {
  formatDuration,
  formatTimecode,
  framesToSeconds,
  type Beat,
  type Speaker,
  type Transcript,
  directionFor,
} from "@mishne/shared";
import { SpeakerLegend, speakerColor } from "@/components/speaker-legend";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const FLAG_STYLE: Record<string, string> = {
  filler: "text-flag-filler border-flag-filler/35",
  false_start: "text-flag-filler border-flag-filler/35",
  retake: "text-flag-retake border-flag-retake/35",
  crosstalk: "text-flag-retake border-flag-retake/35",
  low_confidence: "text-flag-lowconf border-flag-lowconf/35",
  off_mic: "text-flag-lowconf border-flag-lowconf/35",
};

const FLAG_LABEL: Record<string, string> = {
  filler: "filler",
  false_start: "false start",
  retake: "retake",
  crosstalk: "crosstalk",
  low_confidence: "low confidence",
  off_mic: "off mic",
};

type Mode = "all" | "used" | "unused";

export function TranscriptViewer({ transcript }: { transcript: Transcript }) {
  const [mode, setMode] = useState<Mode>("all");
  const [speaker, setSpeaker] = useState<string>("all");
  const [openId, setOpenId] = useState<string | null>(null);
  const [roster, setRoster] = useState<Speaker[]>(transcript.speakers);

  const rename = (id: string, label: string) =>
    setRoster((prev) =>
      prev.map((s) =>
        s.id === id ? { ...s, label, confirmed: label.length > 0 } : s
      )
    );

  const byId = useMemo(
    () => new Map(roster.map((s, i) => [s.id, { speaker: s, index: i }])),
    [roster]
  );
  const nameOf = (id: string) =>
    byId.get(id)?.speaker.label || byId.get(id)?.speaker.defaultLabel || id;

  const { rate, dropFrame } = transcript;

  const beats = useMemo(
    () =>
      transcript.beats.filter((b) => {
        if (mode === "used" && !b.used) return false;
        if (mode === "unused" && b.used) return false;
        if (speaker !== "all" && b.speaker !== speaker) return false;
        return true;
      }),
    [transcript.beats, mode, speaker]
  );

  const usedCount = transcript.beats.filter((b) => b.used).length;
  const sourceS = framesToSeconds(transcript.sourceDurationFrames, rate);
  const cutS = framesToSeconds(transcript.cutDurationFrames, rate);

  return (
    <div className="space-y-4">
      <SpeakerLegend
        speakers={roster}
        attribution={transcript.attribution}
        onRename={rename}
      />

      {/* Summary */}
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Source" value={formatDuration(sourceS)} />
        <Stat label="Cut" value={formatDuration(cutS)} accent />
        <Stat
          label="Beats used"
          value={`${usedCount} of ${transcript.beats.length}`}
        />
        <Stat
          label="Reduction"
          value={`${(100 - (cutS / sourceS) * 100).toFixed(1)}%`}
        />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <Filter className="size-3.5 text-muted-foreground" />
        <Seg value={mode} onChange={setMode} options={[
          { id: "all", label: `All ${transcript.beats.length}` },
          { id: "used", label: `Used ${usedCount}` },
          { id: "unused", label: `Not used ${transcript.beats.length - usedCount}` },
        ]} />
        <span className="mx-1 h-4 w-px bg-border" />
        <Seg
          value={speaker}
          onChange={setSpeaker}
          options={[
            { id: "all", label: "All speakers" },
            ...roster.map((s) => ({ id: s.id, label: nameOf(s.id) })),
          ]}
        />
      </div>

      {/* Beats.
          The list flips for RTL content so the gutter and reading order land
          where a Hebrew reader expects them. Individual strings still carry
          dir="auto" — a transcript mixes scripts within one sentence. */}
      <div className="space-y-1.5" dir={directionFor(transcript.language)}>
        {beats.map((b) => (
          <BeatRow
            key={b.id}
            beat={b}
            rate={rate}
            dropFrame={dropFrame}
            speakerName={nameOf(b.speaker)}
            speakerIndex={byId.get(b.speaker)?.index ?? 0}
            open={openId === b.id}
            onToggle={() => setOpenId(openId === b.id ? null : b.id)}
          />
        ))}
      </div>
    </div>
  );
}

function BeatRow({
  beat,
  rate,
  dropFrame,
  speakerName,
  speakerIndex,
  open,
  onToggle,
}: {
  beat: Beat;
  rate: Transcript["rate"];
  dropFrame: boolean;
  speakerName: string;
  speakerIndex: number;
  open: boolean;
  onToggle: () => void;
}) {
  const dur = framesToSeconds(beat.endFrames - beat.startFrames, rate);
  return (
    <div
      className={cn(
        "group rounded-md border transition-colors",
        beat.used
          ? "border-used/25 bg-used-surface/25"
          : "border-transparent hover:border-border"
      )}
    >
      <button
        onClick={onToggle}
        className="flex w-full items-start gap-3 p-3 text-left"
      >
        {/* Gutter */}
        <div className="flex w-[92px] shrink-0 flex-col items-start gap-1 pt-0.5">
          <span className="tc text-[11px] text-timecode">
            {formatTimecode(beat.startFrames, rate, dropFrame)}
          </span>
          <span className="text-[11px] text-muted-foreground/70">
            {dur.toFixed(1)}s
          </span>
        </div>

        {/* Used marker */}
        <div className="w-5 shrink-0 pt-0.5">
          {beat.used ? (
            <span
              className="grid size-4 place-items-center rounded-full bg-used/20 text-used"
              title={`In the cut, position ${(beat.orderIdx ?? 0) + 1}`}
            >
              <Check className="size-2.5" />
            </span>
          ) : (
            <span className="block size-4 rounded-full border border-border/60" />
          )}
        </div>

        {/* Body */}
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1.5 text-xs font-medium">
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ background: speakerColor(speakerIndex) }}
              />
              <span
                className={
                  beat.used ? "text-used-foreground" : "text-muted-foreground"
                }
              >
                {speakerName}
              </span>
            </span>
            {beat.flags.map((f) => (
              <Badge
                key={f}
                variant="outline"
                className={cn("text-[10px]", FLAG_STYLE[f])}
              >
                {FLAG_LABEL[f]}
              </Badge>
            ))}
            {beat.used && beat.orderIdx != null && (
              <span className="tc text-[10px] text-muted-foreground">
                #{beat.orderIdx + 1} in cut
              </span>
            )}
          </div>
          <p
            dir="auto"
            className={cn(
              "text-sm leading-relaxed",
              beat.used ? "text-foreground" : "text-unused-foreground"
            )}
          >
            {beat.text}
          </p>
        </div>

        {/* Score */}
        <div className="w-10 shrink-0 pt-0.5 text-right">
          <span
            className={cn(
              "tc text-xs",
              (beat.score ?? 0) >= 85
                ? "text-used"
                : (beat.score ?? 0) >= 50
                  ? "text-muted-foreground"
                  : "text-muted-foreground/50"
            )}
          >
            {beat.score}
          </span>
        </div>
      </button>

      {open && beat.rationale && (
        <div className="border-t border-border/60 px-3 py-3 ps-[132px]">
          <div className="flex gap-2 text-xs">
            <Quote className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
            <p dir="auto" className="text-muted-foreground">{beat.rationale}</p>
          </div>
        </div>
      )}
      {open && !beat.rationale && (
        <div
          dir="auto"
          className="border-t border-border/60 px-3 py-3 ps-[132px] text-xs text-muted-foreground"
        >
          Not selected. Score fell below the threshold for the target duration.
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <Card className="p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("tc mt-1 text-lg font-semibold", accent && "text-used")}>
        {value}
      </div>
    </Card>
  );
}

function Seg<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: Array<{ id: string; label: string }>;
}) {
  return (
    <div className="flex gap-1">
      {options.map((o) => (
        <button
          key={o.id}
          onClick={() => onChange(o.id as T)}
          className={cn(
            "rounded-md px-2.5 py-1 text-xs transition-colors",
            value === o.id
              ? "bg-secondary text-secondary-foreground"
              : "text-muted-foreground hover:bg-accent/50"
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
