"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { PageSkeleton, QueryState } from "@/components/query-state";
import { CutEditor } from "@/components/cut-editor";
import { useApi } from "@/lib/use-api";
import type { Job, Transcript } from "@mishne/shared";

export default function EditPage() {
  const { id } = useParams<{ id: string }>();
  const job = useApi<Job>(`/v1/jobs/${id}`);
  const transcript = useApi<Transcript>(`/v1/jobs/${id}/transcript`);

  return (
    <QueryState query={job} missing="No such job." skeleton={<PageSkeleton />}>
      {(job) => (
        <div className="space-y-6">
          <div>
            <Link
              href={`/jobs/${job.id}`}
              className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              {/* The job's own name, as on every other crumb in the app —
                  a breadcrumb is a label, and the id is not one. */}
              <ArrowLeft className="size-3.5" /> {job.name}
            </Link>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight">Build the cut</h1>
              {job.mode === "hybrid" && (
                <Badge variant="outline" className="gap-1 border-primary/40 text-primary">
                  <Sparkles className="size-3" /> AI suggestion loaded
                </Badge>
              )}
              {job.mode === "manual" && <Badge variant="muted">Manual</Badge>}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {job.mode === "manual"
                ? "Pick the lines you want and put them in order. Nothing is pre-selected."
                : "The engine's selection is loaded. Change anything you like before assembly."}
            </p>
          </div>

          <QueryState
            query={transcript}
            missing="The transcript is not ready yet."
            skeleton={<PageSkeleton />}
          >
            {(transcript) => (
              <CutEditor
                transcript={transcript}
                mode={job.mode}
                targetDurationS={job.brief.targetDurationS}
                jobId={job.id}
              />
            )}
          </QueryState>
        </div>
      )}
    </QueryState>
  );
}
