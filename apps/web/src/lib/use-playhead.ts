"use client";

/**
 * Welding the player to the transcript, in both directions.
 *
 * ## The arithmetic
 *
 * A beat's `startFrames` is **absolute source timecode**; a media element's
 * `currentTime` is an offset into a file. `mediaSecondsOf` and
 * `framesAtMediaSeconds` (in `@mishne/shared`) are the only two places that
 * conversion is written, and everything here goes through them.
 *
 * ## Why a rAF loop and not `timeupdate`
 *
 * `timeupdate` fires about four times a second. At that rate the highlight
 * visibly trails the voice — you hear a line start, and a beat later the text
 * catches up. `requestAnimationFrame` while playing costs nothing on a page
 * that is already compositing video, and stops entirely when paused.
 *
 * ## Why the frame position is a ref and not state
 *
 * A three-hour interview is several hundred rows and the list is not
 * virtualised. Re-rendering it sixty times a second is not affordable, so the
 * loop writes the position to a ref and only *state* that actually changed —
 * the active beat, which changes every few seconds — triggers a render. The
 * running timecode is rendered by `PlayheadClock`, which runs its own loop and
 * re-renders only itself.
 *
 * ## Following, and how not to make it hateful
 *
 * The list scrolls to keep the spoken line in view. The moment the reader
 * scrolls for themselves, that stops, or the page fights them for control of
 * the viewport every few seconds — which is the single thing that makes these
 * interfaces unusable.
 *
 * Detection is on **input events**, not scroll events. A `scroll` listener
 * cannot tell whose scroll it is looking at, and every way of teaching it —
 * flags, timers, comparing offsets — is a race that fails in exactly the case
 * that matters, when the two happen together. A wheel, a touch drag or an arrow
 * key is unambiguously a person.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  assetOf,
  framesAtMediaSeconds,
  mediaSecondsOf,
  type Beat,
  type Transcript,
  type TranscriptAsset,
} from "@mishne/shared";

/** Keys that mean the reader is moving through the transcript themselves. */
const NAVIGATION_KEYS = new Set([
  "ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " ",
]);

export interface Playhead {
  /** The reel on screen. A cut can draw on several; the player shows one. */
  activeAssetId: string;
  activeAsset: TranscriptAsset | null;
  /** The beat being spoken, or the most recent one in a pause. */
  activeBeatId: string | null;
  playing: boolean;
  follow: boolean;
  setFollow: (on: boolean) => void;
  /** Put the playhead at the start of a beat, switching reel if need be. */
  seekToBeat: (beat: Beat) => void;
  /** Absolute source frames, read on demand rather than rendered. */
  framesNow: () => number;
  /**
   * The `<video>`/`<audio>` ref, as a **callback** rather than an object ref.
   *
   * This has to be a callback. The element does not exist on the first render:
   * the player shows "preparing the preview" until the transcode lands, so the
   * media element mounts later. An object ref's arrival is invisible to the
   * effect that wires up the listeners — `ref.current` is null when it runs,
   * it bails, and nothing changes afterwards to make it run again. The whole
   * sync was silently dead: no listeners, so no playing state, so no sampling
   * loop, so a frozen clock, a frozen position bar and following that never
   * had an active beat to follow.
   */
  attachMedia: (el: HTMLMediaElement | null) => void;
  /** The element itself, for the imperative calls — play, pause, seek. */
  media: HTMLMediaElement | null;
  /** Rows call this so following knows what to scroll to. */
  registerRow: (beatId: string, el: HTMLElement | null) => void;
}

export function usePlayhead(transcript: Transcript): Playhead {
  // State, not a ref: see `attachMedia` on the interface above. Every effect
  // here depends on this, so they all re-run when the element finally mounts.
  const [media, setMedia] = useState<HTMLMediaElement | null>(null);
  const attachMedia = useCallback(
    (el: HTMLMediaElement | null) => setMedia(el),
    []
  );
  const rows = useRef(new Map<string, HTMLElement>());
  const framesRef = useRef(0);

  const [activeAssetId, setActiveAssetId] = useState(
    transcript.assets[0]?.assetId ?? ""
  );
  const [activeBeatId, setActiveBeatId] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [follow, setFollow] = useState(true);

  const activeAsset =
    transcript.assets.find((a) => a.assetId === activeAssetId) ??
    transcript.assets[0] ??
    null;

  /**
   * This reel's beats, in time order, for the search below.
   *
   * Per asset, not job-wide: two beats with the same frame number are not the
   * same moment unless they share an `assetId` (ADR-0008), so a single sorted
   * list across reels would find the wrong line.
   */
  const timeline = useMemo(
    () =>
      transcript.beats
        .filter((b) => b.assetId === activeAssetId)
        .sort((a, b) => a.startFrames - b.startFrames),
    [transcript.beats, activeAssetId]
  );

  /**
   * The beat at `frames`: the last one that has started.
   *
   * Not "the beat containing the playhead". Speech is mostly gaps — every
   * breath is a hole between beats — and a highlight that switched off in each
   * of them would strobe for the whole runtime. The line most recently begun is
   * the one a reader is still looking at.
   */
  const beatAt = useCallback(
    (frames: number): string | null => {
      if (timeline.length === 0) return null;
      if (frames < timeline[0].startFrames) return null;
      let lo = 0;
      let hi = timeline.length - 1;
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        if (timeline[mid].startFrames <= frames) lo = mid;
        else hi = mid - 1;
      }
      return timeline[lo].id;
    },
    [timeline]
  );

  const framesNow = useCallback(() => framesRef.current, []);

  // ── the loop ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!media || !activeAsset) return;

    let raf = 0;
    const sample = () => {
      framesRef.current = framesAtMediaSeconds(activeAsset, media.currentTime);
      const id = beatAt(framesRef.current);
      // Only a change costs a render. This is what keeps a 600-row list off the
      // frame budget.
      setActiveBeatId((prev) => (prev === id ? prev : id));
    };
    const tick = () => {
      sample();
      raf = requestAnimationFrame(tick);
    };

    const onPlay = () => {
      setPlaying(true);
      raf = requestAnimationFrame(tick);
    };
    const onPause = () => {
      setPlaying(false);
      cancelAnimationFrame(raf);
      sample();
    };

    media.addEventListener("play", onPlay);
    media.addEventListener("pause", onPause);
    media.addEventListener("ended", onPause);
    // A scrub while paused still moves the highlight.
    media.addEventListener("seeked", sample);
    media.addEventListener("loadedmetadata", sample);
    if (!media.paused) onPlay();

    return () => {
      cancelAnimationFrame(raf);
      media.removeEventListener("play", onPlay);
      media.removeEventListener("pause", onPause);
      media.removeEventListener("ended", onPause);
      media.removeEventListener("seeked", sample);
      media.removeEventListener("loadedmetadata", sample);
    };
  }, [media, activeAsset, beatAt]);

  // ── text → player ───────────────────────────────────────────────────────
  const pendingSeek = useRef<number | null>(null);

  const seekToBeat = useCallback(
    (beat: Beat) => {
      const asset = assetOf(transcript, beat);
      const seconds = Math.max(0, mediaSecondsOf(asset, beat.startFrames));

      if (asset.assetId !== activeAssetId) {
        // Another reel. The element's `src` is about to change, which resets
        // it, so the position is parked for `loadedmetadata` to apply —
        // assigning `currentTime` before metadata exists is discarded.
        pendingSeek.current = seconds;
        setActiveAssetId(asset.assetId);
        setActiveBeatId(beat.id);
        return;
      }
      if (!media) return;
      if (media.readyState === 0) {
        pendingSeek.current = seconds;
        return;
      }
      media.currentTime = seconds;
      setActiveBeatId(beat.id);
      // Seeking is a deliberate act of navigation, so it re-arms following:
      // the reader has just said where they want to be.
      setFollow(true);
    },
    [transcript, activeAssetId, media]
  );

  useEffect(() => {
    if (!media) return;
    const apply = () => {
      if (pendingSeek.current === null) return;
      media.currentTime = pendingSeek.current;
      pendingSeek.current = null;
    };
    media.addEventListener("loadedmetadata", apply);
    if (media.readyState > 0) apply();
    return () => media.removeEventListener("loadedmetadata", apply);
  }, [media, activeAssetId]);

  // ── the reader takes over ───────────────────────────────────────────────
  useEffect(() => {
    if (!follow) return;
    const release = () => setFollow(false);
    const onKey = (e: KeyboardEvent) => {
      if (NAVIGATION_KEYS.has(e.key)) release();
    };
    window.addEventListener("wheel", release, { passive: true });
    window.addEventListener("touchmove", release, { passive: true });
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("wheel", release);
      window.removeEventListener("touchmove", release);
      window.removeEventListener("keydown", onKey);
    };
  }, [follow]);

  // ── player → text ───────────────────────────────────────────────────────
  const registerRow = useCallback((beatId: string, el: HTMLElement | null) => {
    if (el) rows.current.set(beatId, el);
    else rows.current.delete(beatId);
  }, []);

  useEffect(() => {
    if (!follow || !activeBeatId) return;
    rows.current.get(activeBeatId)?.scrollIntoView({
      block: "center",
      behavior: "smooth",
    });
  }, [activeBeatId, follow]);

  return {
    activeAssetId,
    activeAsset,
    activeBeatId,
    playing,
    follow,
    setFollow,
    seekToBeat,
    framesNow,
    attachMedia,
    media,
    registerRow,
  };
}
