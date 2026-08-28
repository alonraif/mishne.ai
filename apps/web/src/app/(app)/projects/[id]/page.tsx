import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, Plus, Upload, FileVideo, FileAudio, Layers } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { Timecode } from "@/components/timecode";
import {
  assetsForProject,
  jobsForProject,
  projectById,
} from "@/lib/mock-data";
import { formatBytes, formatCredits, formatDuration, framesToSeconds } from "@mishne/shared";

const KIND_ICON = { video: FileVideo, audio: FileAudio, aaf: Layers } as const;

const INGEST_LABEL = {
  full_media: "Full media",
  audio_only: "Audio only",
  aaf_embedded: "AAF + embedded",
} as const;

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const project = projectById(id);
  if (!project) notFound();

  const assets = assetsForProject(id);
  const jobs = jobsForProject(id);

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
              {assets.length} assets · {jobs.length} jobs ·{" "}
              <span className="tc">{formatCredits(project.creditsUsed)}</span> credits used
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline">
              <Upload /> Upload
            </Button>
            <Button asChild>
              <Link href={`/projects/${id}/new`}>
                <Plus /> New rough cut
              </Link>
            </Button>
          </div>
        </div>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Source material</h2>
        <div className="grid gap-3">
          {assets.map((a) => {
            const Icon = KIND_ICON[a.kind];
            const seconds = framesToSeconds(a.durationFrames, a.rate);
            return (
              <Card key={a.id} className="flex items-center gap-4 p-4">
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
                  {a.status === "uploading" ? (
                    <Badge variant="outline" className="border-stage-active/40 text-stage-active">
                      Uploading 61%
                    </Badge>
                  ) : (
                    <Badge variant="outline">Ready</Badge>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Rough cuts</h2>
        {jobs.length === 0 ? (
          <Card className="p-10 text-center">
            <p className="text-sm text-muted-foreground">
              No rough cuts yet. Upload source material and describe the piece you want.
            </p>
            <Button asChild className="mt-4">
              <Link href={`/projects/${id}/new`}>
                <Plus /> New rough cut
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
                      <div className="flex items-center gap-2.5">
                        <span className="tc text-sm font-medium">{j.id}</span>
                        <StatusBadge status={j.status} />
                      </div>
                      <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">
                        {j.notesRaw}
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="text-sm">
                        target {formatDuration(j.brief.targetDurationS)}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
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
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
