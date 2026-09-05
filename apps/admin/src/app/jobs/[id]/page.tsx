"use client";

/**
 * One job, stage by stage.
 *
 * `job_steps` has recorded the per-step status, error, duration, cache hit and
 * model cost since B3 and nothing has ever displayed them: the customer's
 * progress panel shows a label per stage, and the back-office showed a job's
 * status and no more. Diagnosing a failure meant opening a database session,
 * which is not a thing anyone does while a customer is waiting.
 *
 * The two questions this screen answers, in order: which step stopped, and what
 * did it say. Everything else — timings, cache hits, cost — is on the same
 * table because the second question is usually followed by "and why was it
 * slow".
 */

import { useCallback, useEffect, useState, use } from "react";
import Link from "next/link";
import { get, type AdminJobDetail } from "@/lib/api";
import { Credits, Note, Panel, When } from "@/lib/ui";

const TERMINAL = new Set(["complete", "failed", "cancelled"]);

function stepColour(status: string): string {
  if (status === "failed") return "var(--danger)";
  if (status === "done") return "var(--ok)";
  if (status === "active") return "var(--accent)";
  return "var(--muted)";
}

function bytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1)} GB`;
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs" style={{ color: "var(--muted)" }}>
        {label}
      </div>
      <div className="text-sm">{children}</div>
    </div>
  );
}

export default function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [job, setJob] = useState<AdminJobDetail | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    get<AdminJobDetail>(`/jobs/${id}`)
      .then(setJob)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);

  useEffect(load, [load]);

  // Only while there is something to watch. Polling a finished job for ever is
  // a request per ten seconds per open tab, for an answer that cannot change.
  useEffect(() => {
    if (job && TERMINAL.has(job.status)) return;
    const timer = setInterval(load, 5_000);
    return () => clearInterval(timer);
  }, [job, load]);

  const modelCost = job
    ? job.steps.reduce((sum, s) => sum + (s.model_cost_micros || 0), 0) / 1e6
    : 0;

  return (
    <main className="mx-auto max-w-6xl space-y-5 p-6">
      <Link href="/jobs" className="text-xs underline" style={{ color: "var(--muted)" }}>
        ← all jobs
      </Link>
      <Note kind="error">{error}</Note>

      {job && (
        <>
          <header className="space-y-1">
            <h1 className="text-lg font-semibold tabular">{job.id}</h1>
            <p className="text-sm" style={{ color: "var(--muted)" }}>
              <Link href={`/orgs/${job.org_id}`} className="underline">
                {job.org_name ?? job.org_id}
              </Link>
              {" · "}
              {job.project_name ?? job.project_id ?? "no project"}
              {" · "}
              {job.mode}
            </p>
          </header>

          {job.status === "failed" && (
            <Note kind="error">
              {job.failed_step
                ? `Stopped at "${job.failed_step}". ${JSON.stringify(job.error ?? {})}`
                : JSON.stringify(job.error ?? {})}
            </Note>
          )}

          <Panel title="Job">
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <Detail label="Status">
                <span style={{ color: stepColour(job.status === "complete" ? "done" : job.status) }}>
                  {job.status}
                </span>
              </Detail>
              <Detail label="Submitted">
                <When iso={job.created_at} />
              </Detail>
              <Detail label="Finished">
                <When iso={job.finished_at} />
              </Detail>
              <Detail label="Took">
                <span className="tabular">
                  {job.seconds == null ? "—" : `${job.seconds.toFixed(1)}s`}
                </span>
              </Detail>
              <Detail label="Approved cap">
                <Credits value={job.approved_cap} />
              </Detail>
              <Detail label="Settled">
                <Credits value={job.credits_settled} />
              </Detail>
              <Detail label="Model spend">
                <span className="tabular">${modelCost.toFixed(4)}</span>
              </Detail>
              <Detail label="Media gaps">
                {job.media_gaps && Object.keys(job.media_gaps).length ? (
                  <span style={{ color: "var(--danger)" }}>
                    {Object.keys(job.media_gaps).length} asset(s)
                  </span>
                ) : (
                  "none"
                )}
              </Detail>
            </div>
          </Panel>

          <Panel title="Stages">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead style={{ color: "var(--muted)" }}>
                  <tr className="text-left">
                    {["#", "Stage", "Status", "Took", "Cache", "Detail"].map((h) => (
                      <th key={h} className="pb-2 font-normal">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {job.steps.map((s) => (
                    <tr key={s.idx} className="border-t" style={{ borderColor: "var(--line)" }}>
                      <td className="py-1.5 pr-3 tabular" style={{ color: "var(--muted)" }}>
                        {s.idx}
                      </td>
                      <td className="py-1.5 pr-3">{s.name}</td>
                      <td className="py-1.5 pr-3" style={{ color: stepColour(s.status) }}>
                        {s.status}
                        {s.attempt > 1 && (
                          <span style={{ color: "var(--muted)" }}> ·{s.attempt}</span>
                        )}
                      </td>
                      <td className="py-1.5 pr-3 tabular">
                        {s.seconds ? `${s.seconds.toFixed(2)}s` : "—"}
                      </td>
                      <td className="py-1.5 pr-3" style={{ color: "var(--muted)" }}>
                        {s.from_cache ? "hit" : ""}
                      </td>
                      <td className="py-1.5 text-xs">
                        <span style={{ color: "var(--muted)" }}>{s.detail}</span>
                        {s.error && (
                          <div style={{ color: "var(--danger)" }}>{JSON.stringify(s.error)}</div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          <div className="grid gap-5 md:grid-cols-2">
            <Panel title={`Sources (${job.assets.length})`}>
              <table className="w-full text-sm">
                <tbody>
                  {job.assets.map((a) => (
                    <tr key={a.id} className="border-t" style={{ borderColor: "var(--line)" }}>
                      <td className="py-1.5 pr-3 tabular text-xs">{a.id}</td>
                      <td className="py-1.5 pr-3">{a.kind}</td>
                      <td className="py-1.5 pr-3">{bytes(a.bytes)}</td>
                      <td className="py-1.5 pr-3">{a.status}</td>
                      <td
                        className="py-1.5 text-xs"
                        style={{
                          color: a.proxy_status === "ready" ? "var(--muted)" : "var(--danger)",
                        }}
                      >
                        preview {a.proxy_status}
                      </td>
                    </tr>
                  ))}
                  {!job.assets.length && (
                    <tr>
                      <td className="py-2 text-sm" style={{ color: "var(--muted)" }}>
                        none
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </Panel>

            <Panel title={`Deliverables (${job.artifacts.length})`}>
              <table className="w-full text-sm">
                <tbody>
                  {job.artifacts.map((a) => (
                    <tr key={a.id} className="border-t" style={{ borderColor: "var(--line)" }}>
                      <td className="py-1.5 pr-3 uppercase">{a.kind}</td>
                      <td className="py-1.5 pr-3">{bytes(a.bytes)}</td>
                      <td
                        className="py-1.5 text-xs"
                        style={{ color: a.validated ? "var(--ok)" : "var(--danger)" }}
                      >
                        {a.validated ? "validated" : "not validated"}
                      </td>
                    </tr>
                  ))}
                  {!job.artifacts.length && (
                    <tr>
                      <td className="py-2 text-sm" style={{ color: "var(--muted)" }}>
                        none — this job produced nothing to hand over
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </Panel>
          </div>
        </>
      )}
    </main>
  );
}
