"use client";

/**
 * The preview player: a sticky strip above the transcript.
 *
 * Pinned rather than placed in the flow, because its whole job is to be
 * watchable *while* the reader works down several hundred lines of text. It
 * sits above the app header's `z-40` and the screens below it shift down by its
 * height.
 *
 * It is always present, in every state, and never a gate on the screen. A
 * preview that is still encoding, that failed, or that never existed produces a
 * line of explanation and a transcript that works exactly as it did before —
 * the cut is chosen from the text, and the player is there to help judge it.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Crosshair,
  Loader2,
  Music,
  Pause,
  Play,
  VideoOff,
} from "lucide-react";
import {
  formatDuration,
  formatTimecode,
  framesToSeconds,
  type TranscriptAsset,
} from "@mishne/shared";
import type { Playhead } from "@/lib/use-playhead";
import type { Preview } from "@/lib/use-proxy";
import { cn } from "@/lib/utils";

/** How far the arrow keys nudge the playhead on the scrubber, in seconds. */
const NUDGE_S = 5;

export function MediaPlayer({
  playhead,
  preview,
  showReel,
}: {
  playhead: Playhead;
  preview: Preview;
  /** Name the reel when the cut draws on more than one. */
  showReel: boolean;
}) {
  const { activeAsset, follow, setFollow } = playhead;
  const [collapsed, setCollapsed] = useState(false);
  const audioOnly = preview.kind === "audio";
  const ready = preview.status === "ready" && !!preview.url;

  if (!activeAsset) return null;

  return (
    <div className="sticky top-14 z-50 -mx-4 border-b border-border bg-background/95 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <div className="mx-auto flex max-w-[1400px] items-center gap-4">
        {ready ? (
          <Stage
            asset={activeAsset}
            preview={preview}
            playhead={playhead}
            audioOnly={audioOnly}
            collapsed={collapsed}
          />
        ) : (
          <Explain preview={preview} />
        )}

        <div className="ms-auto flex shrink-0 items-center gap-2 self-start">
          {showReel && (
            <span
              dir="ltr"
              className="max-w-[180px] truncate text-[11px] text-muted-foreground/70"
              title={activeAsset.filename}
            >
              {activeAsset.filename}
            </span>
          )}
          {ready && (
            <button
              onClick={() => setFollow(!follow)}
              className={cn(
                "flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors",
                follow
                  ? "bg-secondary text-secondary-foreground"
                  : "text-muted-foreground hover:bg-accent/50"
              )}
              // The control exists because following turns itself off the moment
              // the reader scrolls; this is how they get it back.
              title={
                follow
                  ? "The transcript is following the playhead"
                  : "Scroll back to the playhead and follow again"
              }
            >
              <Crosshair className="size-3" />
              {follow ? "Following" : "Follow"}
            </button>
          )}
          {ready && !audioOnly && (
            <button
              onClick={() => setCollapsed((c) => !c)}
              className="rounded-md p-1 text-muted-foreground hover:bg-accent/50"
              aria-label={collapsed ? "Show the picture" : "Hide the picture"}
            >
              {collapsed ? (
                <ChevronDown className="size-3.5" />
              ) : (
                <ChevronUp className="size-3.5" />
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Where the playhead is, in absolute source frames, kept current.
 *
 * Two sources, because one is not enough. `requestAnimationFrame` while playing
 * gives a readout that moves with the picture rather than in the four-times-a-
 * second steps `timeupdate` fires in. But a *paused* seek — every click on a
 * beat's timecode — fires no frames at all, so the element's own `seeked` and
 * `loadedmetadata` are listened to as well. With only the loop, clicking a line
 * moved the video and left the clock and the position bar where they were.
 */
function useFramesNow(playhead: Playhead): number {
  const { framesNow, playing, media } = playhead;
  const [frames, setFrames] = useState(framesNow);
  const sync = useCallback(() => setFrames(framesNow()), [framesNow]);

  useEffect(() => {
    sync();
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      sync();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, sync]);

  useEffect(() => {
    if (!media) return;
    // `usePlayhead` listens to the same events and writes the frame position
    // first; these only pull the rendered value along behind it.
    for (const e of ["seeked", "timeupdate", "loadedmetadata"]) {
      media.addEventListener(e, sync);
    }
    return () => {
      for (const e of ["seeked", "timeupdate", "loadedmetadata"]) {
        media.removeEventListener(e, sync);
      }
    };
  }, [media, sync]);

  return frames;
}

function Stage({
  asset,
  preview,
  playhead,
  audioOnly,
  collapsed,
}: {
  asset: TranscriptAsset;
  preview: Preview;
  playhead: Playhead;
  audioOnly: boolean;
  collapsed: boolean;
}) {
  const { attachMedia, media, playing } = playhead;

  const toggle = () => {
    if (!media) return;
    if (media.paused) void media.play();
    else media.pause();
  };

  return (
    <>
      <button
        onClick={toggle}
        className="grid size-10 shrink-0 place-items-center self-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90"
        aria-label={playing ? "Pause" : "Play"}
      >
        {playing ? (
          <Pause className="size-4 fill-current" />
        ) : (
          <Play className="size-4 fill-current ps-0.5" />
        )}
      </button>

      {audioOnly ? (
        <>
          <Music className="size-4 shrink-0 self-center text-muted-foreground" />
          {/* An AAF has no picture to show: what there is to hear is the mix of
              its sound tracks, which is what stage 0 renders (ADR-0019). */}
          <audio
            ref={attachMedia}
            src={preview.url ?? undefined}
            preload="metadata"
            onError={preview.reload}
            className="hidden"
          />
        </>
      ) : (
        <video
          ref={attachMedia}
          src={preview.url ?? undefined}
          preload="metadata"
          playsInline
          // The URL expires while the page is open, and the element reports
          // that as a decode error. Re-minting here is the whole recovery.
          onError={preview.reload}
          onClick={toggle}
          className={cn(
            "shrink-0 cursor-pointer self-center rounded bg-black object-contain transition-all",
            // Big enough to read a face on, which is the entire reason for
            // having a picture here rather than a waveform.
            collapsed ? "h-10 w-auto" : "h-48 w-auto"
          )}
        />
      )}

      <Scrubber playhead={playhead} asset={asset} />
    </>
  );
}

/**
 * The position bar, and the running timecode beside it.
 *
 * Click anywhere on the track to go there; arrow keys nudge. Left and right are
 * deliberately not in `NAVIGATION_KEYS` in `use-playhead`, so scrubbing with
 * them does not read as the reader taking over the scroll.
 */
function Scrubber({
  playhead,
  asset,
}: {
  playhead: Playhead;
  asset: TranscriptAsset;
}) {
  const { media, setFollow } = playhead;
  const frames = useFramesNow(playhead);

  const elapsed = Math.max(0, frames - asset.startTcFrames);
  const fraction =
    asset.durationFrames > 0
      ? Math.min(1, elapsed / asset.durationFrames)
      : 0;

  const seek = (seconds: number) => {
    if (!media || !Number.isFinite(media.duration)) return;
    media.currentTime = Math.min(media.duration, Math.max(0, seconds));
    // Scrubbing is navigation: the reader has just said where they want to be.
    setFollow(true);
  };

  const scrub = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!media || !Number.isFinite(media.duration)) return;
    const box = e.currentTarget.getBoundingClientRect();
    const at = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
    seek(at * media.duration);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (!media) return;
    if (e.key === "ArrowRight") seek(media.currentTime + NUDGE_S);
    else if (e.key === "ArrowLeft") seek(media.currentTime - NUDGE_S);
    else return;
    e.preventDefault();
  };

  return (
    <div className="flex min-w-0 flex-1 items-center gap-3 self-center">
      <div
        onClick={scrub}
        onKeyDown={onKey}
        className="group relative h-8 min-w-[80px] flex-1 cursor-pointer rounded outline-none focus-visible:ring-2 focus-visible:ring-ring"
        role="slider"
        aria-label="Position"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(fraction * 100)}
        tabIndex={0}
      >
        <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 overflow-hidden rounded-full bg-secondary">
          <div
            className="h-full rounded-full bg-waveform"
            style={{ width: `${fraction * 100}%` }}
          />
        </div>
        {/* The knob. Without something at the position, a bar that is 2% full
            on a 25-minute reel reads as an empty bar. */}
        <div
          className="pointer-events-none absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-waveform shadow ring-2 ring-background transition-transform group-hover:scale-125"
          style={{ left: `${fraction * 100}%` }}
        />
      </div>

      <span
        dir="ltr"
        className="tc shrink-0 text-xs [unicode-bidi:isolate]"
      >
        <span className="text-timecode">
          {formatTimecode(frames, asset.rate, asset.dropFrame)}
        </span>
        {/* How long the reel is. On a sound-only preview there is no picture to
            give any sense of scale, and "twelve minutes in" means nothing
            without it. Read against the asset's own rate, never a job-wide
            one. */}
        <span className="ms-2 text-muted-foreground/70">
          {formatDuration(framesToSeconds(asset.durationFrames, asset.rate))}
        </span>
      </span>
    </div>
  );
}

/** Everything that is not a playable preview, said plainly. */
function Explain({ preview }: { preview: Preview }) {
  if (preview.building) {
    return (
      <span className="flex items-center gap-2 py-1.5 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        Preparing the preview — the transcript is ready to work on now.
      </span>
    );
  }
  if (preview.status === "failed") {
    return (
      <span className="flex items-center gap-2 py-1.5 text-xs text-muted-foreground">
        <VideoOff className="size-3.5" />
        The preview could not be built.
        <button
          onClick={preview.reload}
          className="underline underline-offset-2 hover:text-foreground"
        >
          Try again
        </button>
      </span>
    );
  }
  return (
    <span className="flex items-center gap-2 py-1.5 text-xs text-muted-foreground">
      <VideoOff className="size-3.5" />
      No preview for this material.
    </span>
  );
}
