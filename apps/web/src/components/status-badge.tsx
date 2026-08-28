import { Badge } from "@/components/ui/badge";
import type { JobStatus } from "@mishne/shared";
import { cn } from "@/lib/utils";

const LABELS: Record<JobStatus, string> = {
  estimating: "Estimating",
  awaiting_approval: "Awaiting approval",
  awaiting_edit: "Ready to edit",
  queued: "Queued",
  preparing: "Preparing",
  transcribing: "Transcribing",
  analyzing: "Analyzing",
  selecting: "Selecting",
  assembling: "Assembling",
  validating: "Validating",
  complete: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function StatusBadge({ status }: { status: JobStatus }) {
  const running = !["complete", "failed", "cancelled", "awaiting_approval", "awaiting_edit"].includes(status);
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5",
        status === "complete" && "border-stage-done/40 text-stage-done",
        status === "failed" && "border-stage-failed/40 text-stage-failed",
        status === "awaiting_approval" && "border-stage-active/40 text-stage-active",
        status === "awaiting_edit" && "border-primary/50 text-primary",
        running && "border-stage-active/40 text-stage-active"
      )}
    >
      {running && (
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-stage-active opacity-70" />
          <span className="relative inline-flex size-1.5 rounded-full bg-stage-active" />
        </span>
      )}
      {LABELS[status]}
    </Badge>
  );
}
