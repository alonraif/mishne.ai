"use client";

/**
 * What a linked AAF is still waiting for — and where it arrives.
 *
 * A sequence that references media it does not contain sits in
 * `awaiting_media` until the referenced files turn up. Until this existed the
 * customer saw an upload that finished and then did nothing, which reads as a
 * broken product rather than as a question. Now it is a question with the
 * answer attached: the list says which files, and the folder they are in can be
 * dropped straight onto it.
 *
 * The list is ordered by how many clips each missing file unblocks, because
 * that is the order worth uploading in. `GET /v1/assets/{id}/requirements`
 * already sorts it that way; this renders it and does not re-sort.
 *
 * ## Why the drop target lives here and not on the upload control
 *
 * This component is the only place that knows *which* files are wanted. Handing
 * that list to `planFolder` is what lets somebody drop a folder holding a year
 * of rushes — or the whole export folder, AAF included — and upload the four
 * files this cut actually needs. The generic upload control has no such list
 * and should not grow one.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, CircleDashed, FolderOpen, RotateCcw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { useApi } from "@/lib/use-api";
import {
  filesFromDrop,
  planFolder,
  uploadFolder,
  type QueuedFile,
} from "@/lib/upload-folder";
import type { UploadProgress } from "@/lib/upload";
import type { Asset } from "@mishne/shared";

interface MediaRequirement {
  basename: string;
  clipCount: number;
  satisfied: boolean;
  satisfiedByAssetId: string | null;
}

interface AssetRequirements {
  assetId: string;
  status: string;
  outstanding: number;
  requirements: MediaRequirement[];
}



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

export function MediaRequirements({
  projectId,
  assetId,
  onSatisfied,
  onUploaded,
}: {
  projectId: string;
  assetId: string;
  /** Called when the last outstanding file arrives, so the caller's own view
   *  of the asset — a job wizard's list, say — stops saying it is not ready. */
  onSatisfied?: () => void;
  /** Called as each file lands. Every one of them is a new asset in the
   *  project, and a caller showing that project's assets cannot learn about
   *  them any other way: a list poll can only watch rows it already has, and
   *  these did not exist when it last read. */
  onUploaded?: (assetId: string) => void;
}) {
  const query = useApi<AssetRequirements>(`/v1/assets/${assetId}/requirements`, {
    poll: (r) => (r.outstanding > 0 ? 10_000 : null),
  });
  // The sequence's own frame rate, which is the rate its companions are cut at.
  // A WAV carries none and the API insists on one for audio (ADR-0005); asking
  // the customer 775 times for a number the AAF already knows is not a question.
  const asset = useApi<Asset>(`/v1/assets/${assetId}`);

  // `onUploaded` fires per file, and a folder can be 775 of them — each one a
  // full re-read of the project's asset list. Coalesced on a trailing timer so
  // a four-file drop still feels immediate and a 775-file one does not turn
  // into 775 requests for a list that is growing by one row at a time.
  const notify = useRef<ReturnType<typeof setTimeout> | null>(null);
  const announce = useCallback(
    (fn?: () => void) => {
      if (!fn) return;
      if (notify.current) clearTimeout(notify.current);
      notify.current = setTimeout(() => {
        notify.current = null;
        fn();
      }, 1_000);
    },
    []
  );
  useEffect(() => () => {
    if (notify.current) clearTimeout(notify.current);
  }, []);

  const picker = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [skipped, setSkipped] = useState<{ unreferenced: number } | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);

  const data = query.data;
  // The endpoint is the authority on what is left, and it is polled, so this
  // fires whether the files arrived through the queue below or somewhere else
  // entirely — another tab, or the CLI.
  const settled = useRef(false);
  useEffect(() => {
    if (!data || data.requirements.length === 0) return;
    if (data.outstanding === 0 && !settled.current) {
      settled.current = true;
      onSatisfied?.();
    }
    if (data.outstanding > 0) settled.current = false;
  }, [data, onSatisfied]);

  const wanted = (data?.requirements ?? [])
    .filter((r) => !r.satisfied)
    .map((r) => r.basename);

  const start = useCallback(
    async (files: File[]) => {
      const plan = planFolder(files, wanted);
      setSkipped({ unreferenced: plan.unreferenced });
      setErrors({});
      if (plan.queue.length === 0) return;

      setQueue(plan.queue);
      setBusy(true);
      abort.current = new AbortController();
      const rate = asset.data?.rate;
      try {
        await uploadFolder({
          projectId,
          queue: plan.queue,
          rate,
          signal: abort.current.signal,
          onFileDone: (key, uploadedAssetId) => {
            setQueue((q) =>
              q.map((f) => (f.key === key ? { ...f, assetId: uploadedAssetId } : f))
            );
            announce(onUploaded && (() => onUploaded(uploadedAssetId)));
          },
          onFileProgress: (key, progress) =>
            setQueue((q) =>
              q.map((f) => (f.key === key ? { ...f, progress } : f))
            ),
          onFileFailed: (key, error) =>
            setErrors((e) => ({ ...e, [key]: error })),
        });
      } finally {
        setBusy(false);
        // The requirements are satisfied server-side as each upload completes,
        // so the authority on what is left is the endpoint, not this component.
        query.refetch();
      }
    },
    [projectId, wanted, asset.data, query, onUploaded, announce]
  );

  const retryFailed = useCallback(() => {
    const again = queue.filter((f) => errors[f.key]);
    if (again.length > 0) void start(again.map((f) => f.file));
  }, [queue, errors, start]);

  if (!data || data.requirements.length === 0) return null;

  const totalBytes = queue.reduce((n, f) => n + f.bytes, 0);
  const sentBytes = queue.reduce((n, f) => n + (f.progress?.bytesSent ?? 0), 0);
  const failedCount = Object.keys(errors).length;

  return (
    <Card className="border-primary/30 bg-primary/5 p-4">
      <div className="text-sm font-medium">
        {data.outstanding === 0
          ? "All referenced media has arrived"
          : `Waiting for ${data.outstanding} referenced ${
              data.outstanding === 1 ? "file" : "files"
            }`}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        This sequence points at media it does not contain. Drop the folder your
        editor exported alongside it — usually called <span dir="ltr">AAF Media</span>{" "}
        — and only the files below will be uploaded.
      </p>

      <ul className="mt-3 space-y-1.5">
        {data.requirements.map((r) => (
          <li key={r.basename} className="flex items-center gap-2 text-xs">
            {r.satisfied ? (
              <CheckCircle2 className="size-3.5 shrink-0 text-stage-done" />
            ) : (
              <CircleDashed className="size-3.5 shrink-0 text-muted-foreground" />
            )}
            <span
              className={cn("truncate", r.satisfied && "text-muted-foreground line-through")}
              dir="ltr"
            >
              {r.basename}
            </span>
            <span className="ml-auto shrink-0 text-muted-foreground">
              {r.clipCount} {r.clipCount === 1 ? "clip" : "clips"}
            </span>
          </li>
        ))}
      </ul>

      {data.outstanding > 0 && (
        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            if (busy) return;
            void filesFromDrop(event.dataTransfer).then(start);
          }}
          className={cn(
            "mt-4 rounded-md border border-dashed p-4 text-center transition-colors",
            dragging ? "border-primary bg-primary/10" : "border-border"
          )}
        >
          <input
            ref={picker}
            type="file"
            multiple
            // Both spellings: `webkitdirectory` is what every browser
            // implements and `directory` is what the specification calls it.
            // React lowercases unknown attributes, which is what these are.
            {...{ webkitdirectory: "", directory: "" }}
            className="hidden"
            onChange={(event) => {
              const files = [...(event.target.files ?? [])];
              event.target.value = "";
              if (files.length > 0) void start(files);
            }}
          />
          <p className="text-xs text-muted-foreground">
            Drop the media folder here
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-2"
            disabled={busy}
            onClick={() => picker.current?.click()}
          >
            <FolderOpen /> Choose folder
          </Button>
        </div>
      )}

      {skipped && skipped.unreferenced > 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          {skipped.unreferenced}{" "}
          {skipped.unreferenced === 1 ? "file was" : "files were"} in that folder
          without being referenced by this sequence, and {skipped.unreferenced === 1
            ? "was"
            : "were"}{" "}
          skipped.
        </p>
      )}

      {queue.length > 0 && (
        <div className="mt-4 space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">
              {queue.filter((f) => f.progress?.phase === "done").length} of{" "}
              {queue.length} uploaded
            </span>
            <span className="flex items-center gap-2">
              <span className="tc text-muted-foreground">
                {formatBytes(sentBytes)} of {formatBytes(totalBytes)}
              </span>
              {busy && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => abort.current?.abort()}
                  aria-label="Cancel the remaining uploads"
                >
                  <X className="size-3.5" /> Cancel
                </Button>
              )}
              {!busy && failedCount > 0 && (
                <Button size="sm" variant="outline" onClick={retryFailed}>
                  <RotateCcw className="size-3.5" /> Retry {failedCount}
                </Button>
              )}
            </span>
          </div>
          <Progress
            value={totalBytes ? Math.round((sentBytes / totalBytes) * 100) : 0}
          />
          <ul className="space-y-1">
            {queue.map((f) => (
              <li key={f.key} className="flex items-center gap-2 text-xs">
                <span className="truncate" dir="ltr">
                  {f.name}
                </span>
                <span
                  className={cn(
                    "ml-auto shrink-0",
                    errors[f.key] ? "text-destructive" : "text-muted-foreground"
                  )}
                >
                  {errors[f.key] ? "failed" : phaseLabel(f.progress)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function phaseLabel(progress: UploadProgress | null): string {
  if (!progress) return "queued";
  switch (progress.phase) {
    case "hashing":
      // Named, because on a 265 MB WAV it is seconds of a bar that is not the
      // upload yet, and an unexplained pause reads as a stall.
      return `reading ${Math.round(progress.fraction * 100)}%`;
    case "uploading":
      return `${Math.round(progress.fraction * 100)}%`;
    case "completing":
      return "assembling";
    case "done":
      return "uploaded";
    case "cancelled":
      return "cancelled";
    case "failed":
      return "failed";
  }
}
