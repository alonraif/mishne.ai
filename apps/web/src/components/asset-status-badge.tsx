import { Badge } from "@/components/ui/badge";
import type { AssetStatus } from "@mishne/shared";
import { cn } from "@/lib/utils";

/**
 * An asset's state, which is now five things rather than two.
 *
 * `awaiting_media` is the one worth being careful about: it is a linked AAF
 * that probed cleanly and is waiting for the media it references. Nothing is
 * wrong — the upload worked, the sequence simply does not carry its own essence
 * — so it must not look like an error, and it must not look ready either,
 * because a job started against it would transcribe silence.
 */
const LABELS: Record<AssetStatus, string> = {
  uploading: "Uploading",
  probing: "Reading",
  ready: "Ready",
  awaiting_media: "Needs media",
  failed: "Failed",
};

export function AssetStatusBadge({ status }: { status: AssetStatus }) {
  const busy = status === "uploading" || status === "probing";
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5",
        busy && "border-stage-active/40 text-stage-active",
        status === "failed" && "border-stage-failed/40 text-stage-failed",
        status === "awaiting_media" && "border-stage-active/40 text-stage-active"
      )}
    >
      {busy && (
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-stage-active opacity-70" />
          <span className="relative inline-flex size-1.5 rounded-full bg-stage-active" />
        </span>
      )}
      {LABELS[status]}
    </Badge>
  );
}
