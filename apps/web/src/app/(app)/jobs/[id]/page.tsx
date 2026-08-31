"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Download,
  FileText,
  ShieldCheck,
  AlertTriangle,
  Scissors,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { StatusBadge } from "@/components/status-badge";
import { JobStages } from "@/components/job-stages";
import { useState } from "react";
import { PageSkeleton, QueryState } from "@/components/query-state";
import { ApiError } from "@/lib/api";
import { apiGet } from "@/lib/dto";
import { useApi } from "@/lib/use-api";
import {
  formatBytes,
  formatCredits,
  formatDuration,
  type Artifact,
  type Asset,
  type Job,
  type Project,
} from "@mishne/shared";

/** A job in one of these will not change unless somebody does something. */
const SETTLED = ["complete", "failed", "cancelled", "awaiting_approval", "awaiting_edit"];

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  // This is the screen somebody leaves open while the job runs, so it is the
  // one that polls. Three seconds: a stage's `detail` string changes about that
  // often, and the request is one row plus its steps.
  const job = useApi<Job>(`/v1/jobs/${id}`, {
    poll: (j) => (SETTLED.includes(j.status) ? null : 3_000),
  });

  return (
    <QueryState query={job} missing="No such job." skeleton={<PageSkeleton />}>
      {(job) => <JobView job={job} />}
    </QueryState>
  );
}

function JobView({ job }: { job: Job }) {
  const project = useApi<Project>(`/v1/projects/${job.projectId}`);
  const assetsQuery = useApi<Asset[]>(`/v1/projects/${job.projectId}/assets`);
  // Artifacts appear when the job finishes, so this asks once the status says
  // there is something to ask for rather than 404-ing in a loop before then.
  const artifactsQuery = useApi<Artifact[]>(
    job.status === "complete" ? `/v1/jobs/${job.id}/artifacts` : null
  );

  // A job draws on every upload the editor chose, not one. Naming only the
  // first would quietly hide half of what the cut is made from.
  const assets = (assetsQuery.data ?? []).filter((a) => job.assetIds.includes(a.id));
  const artifacts = artifactsQuery.data ?? [];
  const done = job.steps.filter((s) => s.status === "done").length;
  const pct = job.steps.length ? Math.round((done / job.steps.length) * 100) : 0;

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={`/projects/${job.projectId}`}
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" /> {project.data?.name ?? "Project"}
        </Link>
        <div className="flex items-center gap-3">
          <h1 className="tc text-2xl font-semibold tracking-tight">{job.id}</h1>
          <StatusBadge status={job.status} />
        </div>
        <p className="mt-1 truncate text-sm text-muted-foreground" dir="ltr">
          {assets.map((a) => a.filename).join(" · ") || "\u00a0"}
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          {job.status === "awaiting_edit" && (
            <Card className="border-primary/40 bg-primary/5">
              <CardContent className="flex items-center gap-4 pt-5">
                <Scissors className="size-5 shrink-0 text-primary" />
                <div className="min-w-0 flex-1">
                  <div className="font-medium">Transcript is ready</div>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {job.mode === "manual"
                      ? "Mark the lines you want and put them in order."
                      : "A suggested cut is loaded. Adjust it, then assemble."}
                  </p>
                </div>
                <Button asChild>
                  <Link href={`/jobs/${job.id}/edit`}>Open editor</Link>
                </Button>
              </CardContent>
            </Card>
          )}

          {job.status === "failed" && (
            <Card className="border-destructive/30 bg-destructive/5">
              <CardContent className="flex gap-3 pt-5 text-sm">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />
                <div>
                  <div className="font-medium text-destructive">Job failed</div>
                  <p className="mt-1 text-muted-foreground">{job.error}</p>
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Compiled brief</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <p className="rounded-md bg-muted/50 p-3 italic text-muted-foreground">
                &ldquo;{job.notesRaw}&rdquo;
              </p>
              <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
                <Row label="Target" value={formatDuration(job.brief.targetDurationS)} mono />
                <Row label="Tolerance" value={`±${job.brief.durationToleranceS}s`} mono />
                <Row label="Structure" value={job.brief.narrativeShape.replace(/_/g, " ")} />
                <Row label="Pacing" value={job.brief.pacing} />
                <Row label="Handles" value={`${job.brief.handleFrames} frames`} mono />
                <Row label="Language" value={job.brief.language.toUpperCase()} />
              </dl>
              {job.brief.mustInclude.length > 0 && (
                <div>
                  <dt className="text-xs text-muted-foreground">Must include</dt>
                  <dd className="mt-1.5 flex flex-wrap gap-1.5">
                    {job.brief.mustInclude.map((m) => (
                      <Badge key={m} variant="used">{m}</Badge>
                    ))}
                  </dd>
                </div>
              )}
              {job.brief.mustExclude.length > 0 && (
                <div>
                  <dt className="text-xs text-muted-foreground">Must exclude</dt>
                  <dd className="mt-1.5 flex flex-wrap gap-1.5">
                    {job.brief.mustExclude.map((m) => (
                      <Badge key={m} variant="muted">{m}</Badge>
                    ))}
                  </dd>
                </div>
              )}
              {job.brief.clarifications.length > 0 && (
                <div className="rounded-md border border-border p-3">
                  <div className="text-xs font-medium text-muted-foreground">
                    Assumptions made
                  </div>
                  <ul className="mt-2 space-y-1.5">
                    {job.brief.clarifications.map((c) => (
                      <li key={c} className="text-xs text-muted-foreground">— {c}</li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>

          {artifacts.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Artifacts</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {artifacts.map((a) => (
                  <div
                    key={a.id}
                    className="flex items-center gap-3 rounded-md border border-border p-3"
                  >
                    <Badge variant="outline" className="tc uppercase">{a.kind}</Badge>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm">{a.filename}</div>
                      <div className="text-xs text-muted-foreground">
                        {a.targetNle} · {formatBytes(a.bytes)}
                      </div>
                    </div>
                    {a.validated && (
                      <span
                        className="flex items-center gap-1 text-xs text-stage-done"
                        title="Round-trip validated against the canonical timeline"
                      >
                        <ShieldCheck className="size-3.5" /> validated
                      </span>
                    )}
                    <DownloadButton jobId={job.id} artifactId={a.id} />
                  </div>
                ))}
                <Button asChild variant="secondary" className="mt-2 w-full">
                  <Link href={`/jobs/${job.id}/transcript`}>
                    <FileText /> View transcript and rationale
                  </Link>
                </Button>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                Progress
                <span className="tc text-sm font-normal text-muted-foreground">{pct}%</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <JobStages steps={job.steps} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Credits</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2.5 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Approved cap</span>
                <span className="tc">{formatCredits(job.estimate.cap)}</span>
              </div>
              {job.creditsSettled != null ? (
                <>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Actual</span>
                    <span className="tc">{formatCredits(job.creditsSettled)}</span>
                  </div>
                  <Separator />
                  <div className="flex justify-between text-stage-done">
                    <span>Released back</span>
                    <span className="tc">
                      {formatCredits(job.estimate.cap - job.creditsSettled)}
                    </span>
                  </div>
                </>
              ) : job.status === "failed" ? (
                <div className="flex justify-between text-stage-done">
                  <span>Refunded in full</span>
                  <span className="tc">{formatCredits(job.estimate.cap)}</span>
                </div>
              ) : (
                <div className="flex justify-between text-stage-active">
                  <span>Currently held</span>
                  <span className="tc">{formatCredits(job.estimate.cap)}</span>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function DownloadButton({ jobId, artifactId }: { jobId: string; artifactId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /**
   * Ask for a URL, then go to it.
   *
   * Not a plain link to the API: the download is audit-logged and the endpoint
   * hands back a short-lived S3 URL, so the request has to carry the session
   * and the navigation has to go somewhere else. `location.assign` on a URL
   * whose Content-Disposition is `attachment` downloads without leaving the
   * page.
   */
  const download = async () => {
    setBusy(true);
    setError(null);
    try {
      const { url } = await apiGet<{ url: string }>(
        `/v1/jobs/${jobId}/artifacts/${artifactId}/download`
      );
      window.location.assign(url);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <Button size="sm" variant="outline" onClick={download} disabled={busy}>
        <Download /> {busy ? "Preparing…" : "Download"}
      </Button>
      {error && (
        <span className="text-xs text-destructive" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}


function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4 sm:block">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={`capitalize ${mono ? "tc" : ""}`}>{value}</dd>
    </div>
  );
}
