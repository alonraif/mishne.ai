import { FileAudio, FileVideo, Layers } from "lucide-react";
import {
  formatBytes,
  formatDuration,
  formatRate,
  framesToSeconds,
  type Asset,
} from "@mishne/shared";
import { Timecode } from "@/components/timecode";

/** One icon per kind of upload, wherever an upload is listed. */
export const KIND_ICON = { video: FileVideo, audio: FileAudio, aaf: Layers } as const;

/**
 * What a source file is, in the six facts an editor checks before believing an
 * AAF: the codec, how long it runs, its rate, how many audio tracks came with
 * it, its size, and the timecode it starts at. That last one is what ruins a
 * conform when it is wrong, so it is on screen rather than implied.
 *
 * Two renderings of the same six, because the two screens are reading them for
 * different reasons. `AssetMeta` is a scan line under a filename in a list of
 * uploads. `AssetFacts` is the labelled version for the job that cut them,
 * where the reader has one file in front of them and "48" on its own could be
 * a track count, a bit depth or a rate.
 *
 * Rate goes through `formatRate` and start timecode through `Timecode` — the
 * only two places a rational rate becomes a string a person reads, and
 * `Timecode` is also what stops a timecode reordering around its colons inside
 * an RTL line (CLAUDE.md).
 */
export function AssetFacts({ asset }: { asset: Asset }) {
  return (
    <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
      <Fact label="Codec" value={asset.codec} />
      <Fact
        label="Duration"
        mono
        value={formatDuration(framesToSeconds(asset.durationFrames, asset.rate))}
      />
      <Fact
        label="Frame rate"
        mono
        value={`${formatRate(asset.rate)} fps${asset.dropFrame ? " DF" : ""}`}
      />
      <Fact
        label="Audio"
        value={`${asset.audioTracks} ${asset.audioTracks === 1 ? "track" : "tracks"}`}
      />
      <Fact label="Size" mono value={formatBytes(asset.bytes)} />
      <div className="flex justify-between gap-4 sm:block">
        <dt className="text-xs text-muted-foreground">Start timecode</dt>
        <dd>
          <Timecode
            frames={asset.startTcFrames}
            rate={asset.rate}
            dropFrame={asset.dropFrame}
          />
        </dd>
      </div>
    </dl>
  );
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4 sm:block">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={mono ? "tc" : ""}>{value}</dd>
    </div>
  );
}

/** The same facts on one line, for a list of uploads. */
export function AssetMeta({ asset }: { asset: Asset }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      <span>{asset.codec}</span>
      <span>{formatDuration(framesToSeconds(asset.durationFrames, asset.rate))}</span>
      <span className="tc">
        {formatRate(asset.rate)} fps{asset.dropFrame ? " DF" : ""}
      </span>
      <span>{asset.audioTracks} audio</span>
      <span>{formatBytes(asset.bytes)}</span>
      <span className="flex items-center gap-1">
        start{" "}
        <Timecode
          frames={asset.startTcFrames}
          rate={asset.rate}
          dropFrame={asset.dropFrame}
        />
      </span>
    </div>
  );
}
