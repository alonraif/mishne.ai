"use client";

import { useMemo, useState } from "react";
import { Check, Filter, Quote } from "lucide-react";
import {
  formatDuration,
  formatTimecode,
  framesToSeconds,
  assetOf,
  type Beat,
  type Transcript,
  type TranscriptAsset,
  directionFor,
  speakerRoster,
} from "@mishne/shared";
import { SpeakerLegend, speakerColor } from "@/components/speaker-legend";
import { MediaPlayer } from "@/components/media-player";
import { useTranscript } from "@/lib/use-transcript";
import { usePlayhead, type Playhead } from "@/lib/use-playhead";
import { usePreview } from "@/lib/use-proxy";
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

export function TranscriptViewer({
  transcript: initial,
  jobId,
}: {
  transcript: Transcript;
  /** Omitted in a preview: without it the legend is display-only, which is
   *  better than a rename that looks saved and is not. */
  jobId?: string;
}) {
  const [mode, setMode] = useState<Mode>("all");
  const [speaker, setSpeaker] = useState<string>("all");
  const [openId, setOpenId] = useState<string | null>(null);
  const { transcript, rename, merge, error } = useTranscript(initial, jobId);
  const roster = transcript.speakers;
  // Read-only, but the same two-way sync: click a timecode to hear the line,
  // play and watch the text follow.
  const playhead = usePlayhead(transcript);
  const preview = usePreview(playhead.activeAssetId || null);

  // Named voices, and their colours. A beat whose speaker is not in the roster
  // reads as unattributed rather than as a raw id — see `speakerRoster`.
  const voices = useMemo(() => speakerRoster(transcript), [transcript]);
  const nameOf = (id: string) => voices.nameOf(id);

  // No job-wide rate. A beat's timecode is only meaningful against its own
  // reel, and a project routinely mixes rates.
  const multiAsset = transcript.assets.length > 1;

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
  // Totals are seconds, converted per reel upstream. Dividing a frame count by
  // one asset's rate would misreport a mixed-rate project by whole seconds.
  const seqRate = transcript.assets[0].rate;
  const sourceS = framesToSeconds(transcript.sourceDurationFrames, seqRate);
  const cutS = framesToSeconds(transcript.cutDurationFrames, seqRate);

  return (
    <div className="space-y-4">
      <MediaPlayer playhead={playhead} preview={preview} showReel={multiAsset} />

      <SpeakerLegend
        speakers={roster}
        attribution={transcript.attribution}
        onRename={rename}
        onMerge={jobId ? merge : undefined}
      />
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}

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
            asset={assetOf(transcript, b)}
            showReel={multiAsset}
            speakerName={nameOf(b.speaker)}
            speakerIndex={voices.indexOf(b.speaker)}
            open={openId === b.id}
            onToggle={() => setOpenId(openId === b.id ? null : b.id)}
            playhead={playhead}
          />
        ))}
      </div>
    </div>
  );
}

function BeatRow({
  beat,
  asset,
  showReel,
  speakerName,
  speakerIndex,
  open,
  onToggle,
  playhead,
}: {
  beat: Beat;
  asset: TranscriptAsset;
  showReel: boolean;
  speakerName: string;
  speakerIndex: number;
  open: boolean;
  onToggle: () => void;
  playhead: Playhead;
}) {
  const dur = framesToSeconds(beat.endFrames - beat.startFrames, asset.rate);
  const speaking = playhead.activeBeatId === beat.id;
  return (
    <div
      ref={(el) => playhead.registerRow(beat.id, el)}
      className={cn(
        "group rounded-md border transition-colors",
        beat.used
          ? "border-used/25 bg-used-surface/25"
          : "border-transparent hover:border-border",
        // A third state on top of used/unused, so a ring rather than a border.
        speaking && "ring-1 ring-waveform/60"
      )}
    >
      {/* `text-start`, not `text-left`: a Hebrew transcript sets `dir="rtl"` on
          the list, and a line has to align with the edge a Hebrew reader starts
          from rather than the far side of the row from its timecode. */}
      {/* A row, not a button: the gutter plays from here and the body opens
          the rationale, and a button inside a button is invalid HTML. */}
      <div className="flex w-full items-start">
        <button
          onClick={() => playhead.seekToBeat(beat)}
          title="Play from here"
          className="flex w-[92px] shrink-0 flex-col items-start gap-1 rounded-s-md p-3 text-start transition-colors hover:bg-accent/30"
        >
          <span className={cn("tc text-[11px]", speaking ? "text-waveform" : "text-timecode")}>
            {formatTimecode(beat.startFrames, asset.rate, asset.dropFrame)}
          </span>
          <span className="text-[11px] text-muted-foreground/70">
            {dur.toFixed(1)}s
          </span>
          {/* Which reel. Without it a two-source cut shows two timecodes that
              look like one continuous reel and are not. dir="ltr" because a
              filename must not flip inside a Hebrew transcript. */}
          {showReel && (
            <span
              dir="ltr"
              className="truncate text-[10px] text-muted-foreground/60 max-w-[92px]"
              title={asset.filename}
            >
              {asset.filename}
            </span>
          )}
        </button>

        <button
          onClick={onToggle}
          className="flex min-w-0 flex-1 items-start gap-3 p-3 ps-0 text-start"
        >
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
            <span
              className={cn(
                "flex items-center gap-1.5 text-xs font-medium",
                speakerIndex < 0 && "italic text-muted-foreground/60"
              )}
            >
              {speakerIndex >= 0 && (
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ background: speakerColor(speakerIndex) }}
                />
              )}
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
      </div>

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
