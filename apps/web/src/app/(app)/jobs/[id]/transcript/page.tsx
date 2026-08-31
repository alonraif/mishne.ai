"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PageSkeleton, QueryState } from "@/components/query-state";
import { TranscriptViewer } from "@/components/transcript-viewer";
import { useApi } from "@/lib/use-api";
import type { Transcript } from "@mishne/shared";

export default function TranscriptPage() {
  const { id } = useParams<{ id: string }>();
  // Transcription belongs to the upload, not the job (ADR-0008); the API
  // assembles the per-asset rows into one response, so this is one read.
  const transcript = useApi<Transcript>(`/v1/jobs/${id}/transcript`);

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={`/jobs/${id}`}
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" /> {id}
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Transcript</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Every beat considered, what made the cut, and why.
        </p>
      </div>
      <QueryState
        query={transcript}
        missing="This job has no transcript yet."
        skeleton={<PageSkeleton />}
      >
        {(transcript) => <TranscriptViewer transcript={transcript} />}
      </QueryState>
    </div>
  );
}
