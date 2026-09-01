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
 * Which size is worth showing, and whether it is the whole of it.
 *
 * An AAF is a sequence, not a container. A *linked* one — what Media Composer
 * exports by default — is a few hundred kilobytes of pointers at media on the
 * editor's SAN, so its own size answers a question nobody asked: "256 KB" over
 * a 46-minute podcast is true and useless. `mediaBytes` is the essence behind
 * it, present exactly when the sequence references media it does not contain.
 *
 * While that media is still arriving the total is a running one, and saying so
 * is the difference between a number that is wrong and a number that is not
 * finished — an editor who reads 4 GB for a 40 GB export and concludes the
 * upload is done has been misled by us, not by their filesystem.
 */
function sizeOf(asset: Asset): { label: string; value: string; partial: boolean } {
  if (asset.mediaBytes == null) {
    return { label: "Size", value: formatBytes(asset.bytes), partial: false };
  }
  return {
    label: "Media",
    value: formatBytes(asset.mediaBytes),
    partial: asset.status === "awaiting_media",
  };
}

/**
 * What a source file is, in the six facts an editor checks before believing an
 * AAF: the codec, how long it runs, its rate, how many audio tracks came with
 * it, how big it is — see `sizeOf`, which is not the same question for a
 * sequence as for a file — and the timecode it starts at. That last one is
 * what ruins a conform when it is wrong, so it is on screen rather than
 * implied.
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
  const size = sizeOf(asset);
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
      <Fact
        label={size.label}
        mono
        value={size.value}
        hint={size.partial ? "so far" : undefined}
      />
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

function Fact({
  label,
  value,
  mono,
  hint,
}: {
  label: string;
  value: string;
  mono?: boolean;
  hint?: string;
}) {
  return (
    <div className="flex justify-between gap-4 sm:block">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={mono ? "tc" : ""}>
        {value}
        {hint && <span className="ms-1.5 text-xs text-muted-foreground">{hint}</span>}
      </dd>
    </div>
  );
}

/** The same facts on one line, for a list of uploads. */
export function AssetMeta({ asset }: { asset: Asset }) {
  const size = sizeOf(asset);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      <span>{asset.codec}</span>
      <span>{formatDuration(framesToSeconds(asset.durationFrames, asset.rate))}</span>
      <span className="tc">
        {formatRate(asset.rate)} fps{asset.dropFrame ? " DF" : ""}
      </span>
      <span>{asset.audioTracks} audio</span>
      {/* Labelled here, unlike the file's own size, because on one unbroken
          line a number that is not the file's size has to say what it is. */}
      <span>
        {asset.mediaBytes == null ? "" : "media "}
        {size.value}
        {size.partial && " so far"}
      </span>
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
