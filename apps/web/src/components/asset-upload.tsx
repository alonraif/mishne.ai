"use client";

/**
 * The upload control: pick a file, watch it go, resume it when the wifi drops.
 *
 * Everything interesting happens in `lib/upload.ts`; this is the part the user
 * sees, and its job is to be honest about a process that can take an hour.
 * Three things are shown that a simple percentage would hide:
 *
 * * **Hashing is its own phase.** On a 200 GB file, reading it to compute the
 *   content hash takes minutes before a single byte is sent, and a bar sitting
 *   at zero reads as a hang.
 * * **Retries are visible.** A stalled bar with no explanation is the worst
 *   state a long upload can be in; "re-sending 2 parts" is a wait somebody can
 *   live with.
 * * **Resuming says so.** Coming back to a part-finished upload and seeing it
 *   start at 60% is only reassuring if the page says that is what happened.
 */

import { useCallback, useRef, useState } from "react";
import { AlertTriangle, Check, RotateCcw, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { isAudioFile, uploadAsset, type UploadProgress } from "@/lib/upload";

/** The rates an audio-only upload can declare. Rational, never a float. */
const RATES: Array<{ label: string; num: number; den: number }> = [
  { label: "23.976", num: 24000, den: 1001 },
  { label: "24", num: 24, den: 1 },
  { label: "25", num: 25, den: 1 },
  { label: "29.97", num: 30000, den: 1001 },
  { label: "30", num: 30, den: 1 },
  { label: "50", num: 50, den: 1 },
  { label: "59.94", num: 60000, den: 1001 },
];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export function AssetUpload({
  projectId,
  onUploaded,
}: {
  projectId: string;
  onUploaded?: (assetId: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);
  const seenSending = useRef(false);
  const [file, setFile] = useState<File | null>(null);
  const [rate, setRate] = useState(RATES[2]);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [resumed, setResumed] = useState(false);

  const needsRate = file !== null && isAudioFile(file.name);
  const busy =
    progress !== null &&
    (progress.phase === "hashing" ||
      progress.phase === "uploading" ||
      progress.phase === "completing");

  const start = useCallback(
    async (chosen: File) => {
      abort.current = new AbortController();
      seenSending.current = false;
      setResumed(false);
      try {
        const assetId = await uploadAsset({
          projectId,
          file: chosen,
          rate: isAudioFile(chosen.name) ? { num: rate.num, den: rate.den } : undefined,
          signal: abort.current.signal,
          onProgress: (p) => {
            // The FIRST report of the sending phase already carrying parts
            // means S3 had some: this is a resume, and the user should be told
            // rather than left wondering why the bar started at 60%.
            if (p.phase === "uploading" && !seenSending.current) {
              seenSending.current = true;
              if (p.partsDone > 0) setResumed(true);
            }
            setProgress(p);
          },
        });
        onUploaded?.(assetId);
      } catch {
        // `uploadAsset` reports `failed` or `cancelled` through `onProgress`
        // before it throws, so there is nothing to do with the exception and
        // rethrowing would only produce an unhandled rejection.
        //
        // That was not true of anything thrown between hashing and the first
        // part — the create call, most often a 409 — and this comment asserted
        // it anyway: the control sat at "Reading the file, 100%" for ever with
        // no message and no way back. `upload.ts` now reports before it throws
        // on that path too, and a 409 is treated as the answer it is.
      }
    },
    [projectId, rate, onUploaded]
  );

  const phaseLabel = (p: UploadProgress) => {
    switch (p.phase) {
      case "hashing":
        return "Reading the file";
      case "uploading":
        return p.retrying > 0
          ? `Re-sending ${p.retrying} part${p.retrying === 1 ? "" : "s"}`
          : `Uploading part ${Math.min(p.partsDone + 1, p.totalParts)} of ${p.totalParts}`;
      case "completing":
        return "Assembling";
      case "done":
        return "Uploaded — probing";
      case "cancelled":
        return "Cancelled";
      case "failed":
        return p.error ?? "Upload failed";
    }
  };

  return (
    <div className="space-y-3">
      <input
        ref={input}
        type="file"
        className="hidden"
        onChange={(event) => {
          const chosen = event.target.files?.[0] ?? null;
          setProgress(null);
          setFile(chosen);
          // An audio file cannot start until a rate has been chosen: there is
          // none in the file, and guessing one is a cut that is a frame out
          // everywhere.
          if (chosen && !isAudioFile(chosen.name)) void start(chosen);
        }}
      />

      {!busy && (
        <Button variant="outline" onClick={() => input.current?.click()}>
          <Upload /> Upload
        </Button>
      )}

      {needsRate && !busy && progress?.phase !== "done" && (
        <div className="space-y-2 rounded-md border border-border p-3">
          <Label className="text-xs">
            {file.name} has no picture, so it carries no frame rate. Which rate is the
            sequence?
          </Label>
          <div className="flex flex-wrap gap-1.5">
            {RATES.map((r) => (
              <button
                key={r.label}
                onClick={() => setRate(r)}
                className={cn(
                  "tc rounded border px-2 py-1 text-xs transition-colors",
                  r.label === rate.label
                    ? "border-primary bg-primary/10 text-foreground"
                    : "border-border text-muted-foreground hover:bg-accent/40"
                )}
              >
                {r.label}
              </button>
            ))}
          </div>
          <Button size="sm" onClick={() => void start(file)}>
            Start upload
          </Button>
        </div>
      )}

      {progress && (
        <div className="space-y-2 rounded-md border border-border p-3">
          <div className="flex items-center justify-between gap-3 text-sm">
            <span className="truncate">{file?.name}</span>
            <span
              className={cn(
                "shrink-0 text-xs",
                progress.phase === "failed" ? "text-destructive" : "text-muted-foreground"
              )}
            >
              {phaseLabel(progress)}
            </span>
          </div>

          <Progress value={Math.round(progress.fraction * 100)} />

          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span className="tc">
              {progress.phase === "hashing"
                ? `${Math.round(progress.fraction * 100)}%`
                : `${formatBytes(progress.bytesSent)} of ${formatBytes(progress.totalBytes)}`}
            </span>
            <span className="flex items-center gap-2">
              {resumed && progress.phase === "uploading" && (
                <span className="text-muted-foreground">resumed where it stopped</span>
              )}
              {busy && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => abort.current?.abort()}
                  aria-label="Cancel upload"
                >
                  <X className="size-3.5" /> Cancel
                </Button>
              )}
              {progress.phase === "failed" && file && (
                <Button size="sm" variant="outline" onClick={() => void start(file)}>
                  <RotateCcw className="size-3.5" /> Resume
                </Button>
              )}
              {progress.phase === "done" && <Check className="size-4 text-stage-done" />}
              {progress.phase === "failed" && (
                <AlertTriangle className="size-4 text-destructive" />
              )}
            </span>
          </div>

          {progress.phase === "failed" && (
            <p className="text-xs text-muted-foreground">
              Nothing was lost. Resuming sends only the parts that did not arrive.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
