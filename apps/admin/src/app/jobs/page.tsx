"use client";

/**
 * Every tenant's jobs, newest first — the screen for "it didn't work".
 *
 * The organisation view already lists an org's own jobs, which is the wrong
 * shape for support: it answers "what has this customer run" and the question
 * that actually arrives is "something failed", from a person who may not know
 * which organisation they are in. So this is cross-tenant and defaults to the
 * failures.
 *
 * No filenames and no job names, for the reason `admin/service.py` gives: the
 * name of a job defaults to the name of the first file in it, and what the
 * customer shot is not the operator's business. The org, the project and the
 * time are enough to find a job and talk to somebody about it.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { get, type AdminJob } from "@/lib/api";
import { Note, Panel, Select, When } from "@/lib/ui";

/** Terminal states first — this is a triage screen, not a report. */
const FILTERS = [
  { value: "failed", label: "Failed" },
  { value: "", label: "All" },
  { value: "active", label: "In flight" },
  { value: "awaiting_edit", label: "Awaiting edit" },
  { value: "complete", label: "Complete" },
] as const;

const TERMINAL = new Set(["complete", "failed", "cancelled"]);

function statusColour(status: string): string {
  if (status === "failed") return "var(--danger)";
  if (status === "complete") return "var(--ok)";
  return "var(--muted)";
}

/** The step name out of a job error, when there is one. */
function errorText(error: Record<string, unknown> | null): string {
  if (!error) return "";
  const code = error.code ?? error.type ?? "";
  const step = error.step ? ` at ${String(error.step)}` : "";
  return code ? `${String(code)}${step}` : JSON.stringify(error);
}

export default function JobsPage() {
  const [filter, setFilter] = useState<string>("failed");
  const [rows, setRows] = useState<AdminJob[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    const query =
      filter === "failed"
        ? "?failed=true&limit=200"
        : filter === "active"
          ? "?limit=200"
          : filter
            ? `?status=${filter}&limit=200`
            : "?limit=200";
    get<AdminJob[]>(`/jobs${query}`)
      .then((all) =>
        setRows(filter === "active" ? all.filter((j) => !TERMINAL.has(j.status)) : all)
      )
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [filter]);

  useEffect(() => {
    setRows(null);
    load();
  }, [load]);

  // A job in flight changes underneath the operator watching it.
  useEffect(() => {
    const timer = setInterval(load, 10_000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <main className="mx-auto max-w-6xl space-y-5 p-6">
      <Link href="/" className="text-xs underline" style={{ color: "var(--muted)" }}>
        ← all organisations
      </Link>
      <h1 className="text-lg font-semibold">Jobs</h1>
      <Note kind="error">{error}</Note>

      <Panel
        title={rows ? `${rows.length} job${rows.length === 1 ? "" : "s"}` : "Loading…"}
        right={
          <div className="w-44">
            <Select value={filter} onChange={(e) => setFilter(e.target.value)}>
              {FILTERS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </Select>
          </div>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead style={{ color: "var(--muted)" }}>
              <tr className="text-left">
                {["Started", "Organisation", "Project", "Mode", "Status", "Took", "Why it stopped"].map(
                  (h) => (
                    <th key={h} className="pb-2 font-normal">
                      {h}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody>
              {rows?.map((j) => (
                <tr key={j.id} className="border-t" style={{ borderColor: "var(--line)" }}>
                  <td className="py-1.5 pr-3">
                    <Link href={`/jobs/${j.id}`} className="underline">
                      <When iso={j.created_at} />
                    </Link>
                  </td>
                  <td className="py-1.5 pr-3">
                    <Link href={`/orgs/${j.org_id}`} className="underline">
                      {j.org_name ?? j.org_id}
                    </Link>
                  </td>
                  <td className="py-1.5 pr-3">{j.project_name ?? "—"}</td>
                  <td className="py-1.5 pr-3">{j.mode}</td>
                  <td className="py-1.5 pr-3" style={{ color: statusColour(j.status) }}>
                    {j.status}
                  </td>
                  <td className="py-1.5 pr-3 tabular">
                    {j.seconds === null ? "—" : `${j.seconds.toFixed(0)}s`}
                  </td>
                  <td className="py-1.5 text-xs" style={{ color: "var(--danger)" }}>
                    {errorText(j.error)}
                  </td>
                </tr>
              ))}
              {rows?.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-4 text-sm" style={{ color: "var(--muted)" }}>
                    Nothing here — which for the failed filter is the answer you want.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </main>
  );
}
