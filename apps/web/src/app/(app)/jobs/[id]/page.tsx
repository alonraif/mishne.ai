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
  Pencil,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { StatusBadge } from "@/components/status-badge";
import { JobStages } from "@/components/job-stages";
import { AssetFacts, KIND_ICON } from "@/components/asset-meta";
import { useState } from "react";
import { PageSkeleton, QueryState } from "@/components/query-state";
import { ApiError } from "@/lib/api";
import { apiGet, apiSend } from "@/lib/dto";
import { useApi } from "@/lib/use-api";
import {
  JOB_MODE_LABEL,
  JOB_NAME_MAX,
  formatBytes,
  formatCredits,
  formatDuration,
  framesToSeconds,
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
  const query = useApi<Job>(`/v1/jobs/${id}`, {
    poll: (j) => (SETTLED.includes(j.status) ? null : 3_000),
  });

  return (
    <QueryState query={query} missing="No such job." skeleton={<PageSkeleton />}>
      {(job) => <JobView job={job} onRenamed={query.refetch} />}
    </QueryState>
  );
}

function JobView({ job, onRenamed }: { job: Job; onRenamed: () => void }) {
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
  // A finished job is finished. The fraction is the honest answer while the
  // job is running, but rounding it can leave a complete job at 99%, and the
  // step list can be short of a stage the mode never ran.
  const pct =
    job.status === "complete"
      ? 100
      : job.steps.length
        ? Math.round((done / job.steps.length) * 100)
        : 0;
  // Runtime across every upload the job drew on, for the multi-asset case
  // where no single file's duration is the answer. Summed in seconds because
  // two reels can be at two rates; frame counts at different rates do not add.
  const sourceTotal = formatDuration(
    assets.reduce((s, a) => s + framesToSeconds(a.durationFrames, a.rate), 0)
  );
  // Transcribe-only. There is no selection, so the half of the brief that
  // describes one — target, tolerance, structure, pacing — describes nothing
  // this job did.
  const transcription = job.mode === "manual";

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={`/projects/${job.projectId}`}
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" /> {project.data?.name ?? "Project"}
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <JobTitle job={job} onRenamed={onRenamed} />
          <Badge variant="outline">{JOB_MODE_LABEL[job.mode]}</Badge>
          <StatusBadge status={job.status} />
        </div>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
          {/* The id is still worth showing — it is what a support conversation
              and every artifact filename are keyed on — just not as the
              heading, which is the customer's own name for the job. */}
          <span className="tc">{job.id}</span>
          <span className="truncate" dir="ltr">
            {assets.map((a) => a.filename).join(" · ")}
          </span>
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

          {/* One card, whatever the mode, because the mode decides how much of
              it there is to show. A transcribe-only job has a source and two
              settings; an AI one adds everything the brief compiler made of
              the notes, under its own heading rather than as the whole card. */}
          <Card>
            <CardHeader>
              <CardTitle>Job details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5 text-sm">
              {/* What was actually cut. Loading is not an empty section: the
                  assets are a second request and the label would sit over
                  nothing until it lands. */}
              {assets.length > 0 && (
                <div className="space-y-2">
                  <div className="text-xs text-muted-foreground">
                    Source
                    {assets.length > 1 && ` · ${assets.length} files, ${sourceTotal}`}
                  </div>
                  {assets.map((a) => {
                    const Icon = KIND_ICON[a.kind];
                    return (
                      <div key={a.id} className="rounded-md border border-border p-3">
                        <div className="flex items-center gap-2">
                          <Icon className="size-4 shrink-0 text-muted-foreground" />
                          {/* A filename is the customer's own text and can be
                              Hebrew — `dir="ltr"` keeps the extension at the
                              end where an editor looks for it. */}
                          <div className="truncate font-medium" dir="ltr">
                            {a.filename}
                          </div>
                        </div>
                        <div className="mt-3 border-t border-border pt-3">
                          <AssetFacts asset={a} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
                {/* Both of these are true in every mode: stage 9 applies
                    handles to a hand-marked cut too, and the language is what
                    routed the transcription. */}
                <Row label="Handles" value={`${job.brief.handleFrames} frames`} mono />
                <Row label="Language" value={job.brief.language.toUpperCase()} />
              </dl>

              {/* Everything below is what the brief compiler made of the
                  notes. A transcribe-only job has no selection, so a target, a
                  tolerance, a structure and a pacing describe nothing it did —
                  and the submission form never asked for them. */}
              {!transcription && (
                <div className="space-y-4 border-t border-border pt-4">
                  <div className="text-xs text-muted-foreground">Compiled brief</div>
                  {job.notesRaw.trim().length > 0 && (
                    <p className="rounded-md bg-muted/50 p-3 italic text-muted-foreground">
                      &ldquo;{job.notesRaw}&rdquo;
                    </p>
                  )}
                  <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
                    <Row label="Target" value={formatDuration(job.brief.targetDurationS)} mono />
                    <Row label="Tolerance" value={`±${job.brief.durationToleranceS}s`} mono />
                    <Row label="Structure" value={job.brief.narrativeShape.replace(/_/g, " ")} />
                    <Row label="Pacing" value={job.brief.pacing} />
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

/**
 * The job's name, and a way to change it.
 *
 * A name is chosen before the work has been seen and judged after it, so this
 * is on the job's own page rather than only in the form that created it. The
 * name is a label and never an identifier — the id under it is what links,
 * artifacts and support conversations are keyed on — which is why renaming is
 * allowed at any status, finished jobs included.
 *
 * Not optimistic. A rename is one field and the request is short, and a name
 * that appears to have saved and has not is the one outcome worth avoiding:
 * this is the string everyone else in the building will use for the cut.
 */
function JobTitle({ job, onRenamed }: { job: Job; onRenamed: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(job.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = () => {
    setName(job.name);
    setError(null);
    setEditing(true);
  };

  const save = async () => {
    const next = name.trim();
    // Nothing to say, or nothing to change: close rather than spend a request
    // and an audit row on a no-op.
    if (!next || next === job.name) {
      setEditing(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await apiSend<Job>(`/v1/jobs/${job.id}`, {
        method: "PATCH",
        json: { name: next },
      });
      // Re-read rather than patch the copy in hand: the endpoint returns the
      // job as it now stands, and one answer to "what is on screen" is the
      // whole point of `useApi`.
      onRenamed();
      setEditing(false);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : String(cause));
    } finally {
      setBusy(false);
    }
  };

  if (!editing) {
    return (
      <div className="flex min-w-0 items-center gap-1.5">
        <h1 className="truncate text-2xl font-semibold tracking-tight">{job.name}</h1>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0 text-muted-foreground"
          onClick={open}
          title="Rename this job"
          aria-label="Rename this job"
        >
          <Pencil className="size-3.5" />
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Input
          autoFocus
          value={name}
          maxLength={JOB_NAME_MAX}
          disabled={busy}
          aria-label="Job name"
          className="h-9 w-80 max-w-full text-base"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            // Enter and Escape, because this is a one-field form and reaching
            // for the mouse to leave it is the thing that makes inline editing
            // worse than a dialog.
            if (e.key === "Enter") void save();
            if (e.key === "Escape") setEditing(false);
          }}
        />
        <Button size="sm" onClick={() => void save()} disabled={busy || !name.trim()}>
          {busy ? "Saving…" : "Save"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={busy}>
          Cancel
        </Button>
      </div>
      {error && (
        <span className="text-xs text-destructive" role="alert">
          {error}
        </span>
      )}
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
