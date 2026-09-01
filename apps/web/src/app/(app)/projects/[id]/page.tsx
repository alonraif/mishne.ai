"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Plus, FileVideo, FileAudio, Layers } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AssetUpload } from "@/components/asset-upload";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { AssetStatusBadge } from "@/components/asset-status-badge";
import { Timecode } from "@/components/timecode";
import { MediaRequirements } from "@/components/media-requirements";
import { CardsSkeleton, PageSkeleton, QueryState } from "@/components/query-state";
import { useApi, type Query } from "@/lib/use-api";
import {
  JOB_MODE_LABEL,
  formatBytes,
  formatCredits,
  formatDuration,
  framesToSeconds,
  type Asset,
  type IngestMode,
  type Job,
  type Project,
} from "@mishne/shared";

const KIND_ICON = { video: FileVideo, audio: FileAudio, aaf: Layers } as const;

const INGEST_LABEL: Record<IngestMode, string> = {
  full_media: "Full media",
  audio_only: "Audio only",
  aaf_embedded: "AAF + embedded",
  // A sequence that points at media it does not contain. The customer has to
  // upload the referenced files too, and the asset waits in `awaiting_media`
  // until they arrive.
  aaf_linked: "AAF + linked",
};

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const project = useApi<Project>(`/v1/projects/${id}`);
  const assetsQuery = useApi<Asset[]>(`/v1/projects/${id}/assets`);
  // A job's status changes while the customer is looking at the list it is in.
  // Polling stops as soon as nothing is moving — see `use-api.ts`.
  const jobsQuery = useApi<Job[]>(`/v1/projects/${id}/jobs`, {
    poll: (list) => (list.some(isRunning) ? 5_000 : null),
  });

  return (
    <QueryState query={project} missing="No such project." skeleton={<PageSkeleton />}>
      {(project) => (
        <ProjectView id={id} project={project} assets={assetsQuery} jobs={jobsQuery} />
      )}
    </QueryState>
  );
}

/** Statuses that will change on their own if you wait. */
function isRunning(job: Job): boolean {
  return !["complete", "failed", "cancelled", "awaiting_approval", "awaiting_edit"].includes(
    job.status
  );
}

/**
 * The two or three facts worth showing about a job, chosen by its mode.
 *
 * A target length is a promise the engine is trying to keep, so it belongs on
 * an AI job and on a draft the engine proposed. It is meaningless on a
 * transcription: nothing is being selected, the customer marks the cut
 * themselves afterwards, and the number on the row was only ever the figure
 * they typed into a form that told them it would not affect the price. What a
 * transcription job is actually about is how much source went through it and
 * which language it was read in — the two things that decided what it cost.
 */
function jobFacts(job: Job): string[] {
  const language = job.brief.language.toUpperCase();
  const source = formatDuration(job.estimate.sourceHours * 3600);
  if (job.mode === "manual") {
    return [`${source} source`, language];
  }
  return [
    `target ${formatDuration(job.brief.targetDurationS)}`,
    job.brief.narrativeShape.replace(/_/g, " "),
    language,
  ];
}

function ProjectView({
  id,
  project,
  assets,
  jobs,
}: {
  id: string;
  project: Project;
  assets: Query<Asset[]>;
  jobs: Query<Job[]>;
}) {
  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/projects"
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" /> Projects
        </Link>
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {project.assetCount} assets · {project.jobCount} jobs ·{" "}
              <span className="tc">{formatCredits(project.creditsUsed)}</span> credits used
            </p>
          </div>
          <div className="flex gap-2">
            <AssetUpload projectId={id} />
            <Button asChild>
              <Link href={`/projects/${id}/new`}>
                <Plus /> New job
              </Link>
            </Button>
          </div>
        </div>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Source material</h2>
        <QueryState query={assets} missing="No source material yet." skeleton={<CardsSkeleton rows={2} />}>
          {(assets) => (
        <div className="grid gap-3">
          {assets.map((a) => {
            const Icon = KIND_ICON[a.kind];
            const seconds = framesToSeconds(a.durationFrames, a.rate);
            return (
              <div key={a.id} className="space-y-2">
              <Card className="flex items-center gap-4 p-4">
                <div className="grid size-10 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
                  <Icon className="size-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{a.filename}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span>{a.codec}</span>
                    <span>{formatDuration(seconds)}</span>
                    <span className="tc">
                      {(a.rate.num / a.rate.den).toFixed(3).replace(/\.?0+$/, "")} fps
                      {a.dropFrame ? " DF" : ""}
                    </span>
                    <span>{a.audioTracks} audio</span>
                    <span>{formatBytes(a.bytes)}</span>
                    <span className="flex items-center gap-1">
                      start <Timecode frames={a.startTcFrames} rate={a.rate} dropFrame={a.dropFrame} />
                    </span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge variant="muted">{INGEST_LABEL[a.ingestMode]}</Badge>
                  <AssetStatusBadge status={a.status} />
                </div>
              </Card>
              {a.status === "awaiting_media" && <MediaRequirements assetId={a.id} />}
              </div>
            );
          })}
        </div>
          )}
        </QueryState>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Job list</h2>
        <QueryState query={jobs} missing="No jobs yet." skeleton={<CardsSkeleton rows={2} />}>
          {(jobs) =>
          jobs.length === 0 ? (
          <Card className="p-10 text-center">
            <p className="text-sm text-muted-foreground">
              No jobs yet. Upload source material and describe the piece you want.
            </p>
            <Button asChild className="mt-4">
              <Link href={`/projects/${id}/new`}>
                <Plus /> New job
              </Link>
            </Button>
          </Card>
        ) : (
          <div className="grid gap-3">
            {jobs.map((j) => (
              <Link key={j.id} href={`/jobs/${j.id}`}>
                <Card className="p-4 transition-colors hover:border-primary/50 hover:bg-accent/30">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                        <span className="truncate text-sm font-medium">{j.name}</span>
                        <Badge variant="outline">{JOB_MODE_LABEL[j.mode]}</Badge>
                        <StatusBadge status={j.status} />
                      </div>
                      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        {jobFacts(j).map((fact) => (
                          <span key={fact}>{fact}</span>
                        ))}
                      </div>
                      {/* A transcribe-only job has no notes — there was nobody
                          to brief. An empty quotation mark where the brief
                          goes reads as a job someone forgot to describe. */}
                      {j.notesRaw && (
                        <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">
                          {j.notesRaw}
                        </p>
                      )}
                    </div>
                    <div className="shrink-0 text-right text-xs text-muted-foreground">
                      {j.creditsSettled != null ? (
                        <>
                          <span className="tc">{formatCredits(j.creditsSettled)}</span> credits
                        </>
                      ) : j.status === "failed" ? (
                        "refunded"
                      ) : (
                        <>
                          <span className="tc">{formatCredits(j.estimate.cap)}</span> held
                        </>
                      )}
                    </div>
                  </div>
                </Card>
              </Link>
            ))}
          </div>
          )
        }
        </QueryState>
      </section>
    </div>
  );
}
