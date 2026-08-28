/**
 * Timecode utilities.
 *
 * Rule: time is frames plus a rational rate. Never floats, never "23.976".
 * Drop-frame is a *display* convention — arithmetic happens on frame counts.
 * See CLAUDE.md and docs/architecture/02-media-and-interchange.md.
 */

export interface Rate {
  num: number;
  den: number;
}

export const RATE_23_976: Rate = { num: 24000, den: 1001 };
export const RATE_24: Rate = { num: 24, den: 1 };
export const RATE_25: Rate = { num: 25, den: 1 };
export const RATE_29_97: Rate = { num: 30000, den: 1001 };
export const RATE_30: Rate = { num: 30, den: 1 };
export const RATE_50: Rate = { num: 50, den: 1 };
export const RATE_59_94: Rate = { num: 60000, den: 1001 };

export function rateToFps(rate: Rate): number {
  return rate.num / rate.den;
}

/** Nominal frames per second used for timecode labelling (29.97 -> 30). */
export function nominalFps(rate: Rate): number {
  return Math.round(rateToFps(rate));
}

export function secondsToFrames(seconds: number, rate: Rate): number {
  return Math.round((seconds * rate.num) / rate.den);
}

export function framesToSeconds(frames: number, rate: Rate): number {
  return (frames * rate.den) / rate.num;
}

/**
 * Format a frame count as SMPTE timecode.
 * Drop-frame uses ';' as the final separator, per convention.
 */
export function formatTimecode(frames: number, rate: Rate, dropFrame = false): string {
  const fps = nominalFps(rate);
  let f = Math.max(0, Math.round(frames));

  if (dropFrame && (fps === 30 || fps === 60)) {
    const dropPerMinute = fps === 30 ? 2 : 4;
    const framesPer10Min = fps * 60 * 10 - dropPerMinute * 9;
    const framesPerMin = fps * 60 - dropPerMinute;
    const d = Math.floor(f / framesPer10Min);
    const m = f % framesPer10Min;
    f += dropPerMinute * 9 * d;
    if (m > dropPerMinute) {
      f += dropPerMinute * Math.floor((m - dropPerMinute) / framesPerMin);
    }
  }

  const ff = f % fps;
  const totalSeconds = Math.floor(f / fps);
  const ss = totalSeconds % 60;
  const mm = Math.floor(totalSeconds / 60) % 60;
  const hh = Math.floor(totalSeconds / 3600) % 24;
  const p = (n: number) => String(n).padStart(2, "0");
  const sep = dropFrame ? ";" : ":";
  return `${p(hh)}:${p(mm)}:${p(ss)}${sep}${p(ff)}`;
}

/** Human-readable duration, e.g. "2h 58m" or "9m 42s". */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(sec).padStart(2, "0")}s`;
  return `${sec}s`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}
