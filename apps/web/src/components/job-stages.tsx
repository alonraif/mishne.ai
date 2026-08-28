import { Check, X, Loader2 } from "lucide-react";
import type { JobStep } from "@mishne/shared";
import { cn } from "@/lib/utils";

export function JobStages({ steps }: { steps: JobStep[] }) {
  return (
    <ol className="space-y-0">
      {steps.map((s, i) => {
        const last = i === steps.length - 1;
        return (
          <li key={s.name} className="relative flex gap-3 pb-4 last:pb-0">
            {!last && (
              <span
                className={cn(
                  "absolute left-[11px] top-6 h-full w-px",
                  s.status === "done" ? "bg-stage-done/40" : "bg-border"
                )}
              />
            )}
            <span
              className={cn(
                "relative z-10 grid size-[22px] shrink-0 place-items-center rounded-full border",
                s.status === "done" && "border-stage-done/50 bg-stage-done/15 text-stage-done",
                s.status === "active" && "border-stage-active/50 bg-stage-active/15 text-stage-active",
                s.status === "failed" && "border-stage-failed/50 bg-stage-failed/15 text-stage-failed",
                s.status === "pending" && "border-border bg-background text-stage-pending"
              )}
            >
              {s.status === "done" && <Check className="size-3" />}
              {s.status === "active" && <Loader2 className="size-3 animate-spin" />}
              {s.status === "failed" && <X className="size-3" />}
              {s.status === "pending" && <span className="size-1.5 rounded-full bg-current" />}
            </span>
            <div className="min-w-0 pt-0.5">
              <div
                className={cn(
                  "text-sm",
                  s.status === "pending" ? "text-muted-foreground" : "text-foreground",
                  s.status === "active" && "font-medium"
                )}
              >
                {s.label}
              </div>
              {s.detail && (
                <div className="mt-0.5 text-xs text-muted-foreground">{s.detail}</div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
