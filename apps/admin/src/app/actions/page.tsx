"use client";

/**
 * Everything the back-office has done, newest first.
 *
 * Separate from any organisation's own audit log on purpose: that one is the
 * customer's record of their own people and is disclosed to them; this is our
 * record of what we did across all of them.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { get, type Action } from "@/lib/api";
import { Note, Panel, When } from "@/lib/ui";

export default function ActionsPage() {
  const [rows, setRows] = useState<Action[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    get<Action[]>("/actions?limit=200")
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <main className="mx-auto max-w-6xl space-y-5 p-6">
      <Link href="/" className="text-xs underline" style={{ color: "var(--muted)" }}>
        ← all organisations
      </Link>
      <h1 className="text-lg font-semibold">Action log</h1>
      <Note kind="error">{error}</Note>
      <Panel title={rows ? `${rows.length} entries` : "Loading…"}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead style={{ color: "var(--muted)" }}>
              <tr className="text-left">
                {["When", "Who", "Action", "Organisation", "Reason", "Detail"].map((h) => (
                  <th key={h} className="pb-2 font-normal">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows?.map((a) => (
                <tr key={a.id} className="border-t" style={{ borderColor: "var(--line)" }}>
                  <td className="py-1.5 pr-3">
                    <When iso={a.created_at} />
                  </td>
                  <td className="py-1.5 pr-3">{a.admin_email ?? "—"}</td>
                  <td className="py-1.5 pr-3">{a.action}</td>
                  <td className="py-1.5 pr-3">
                    {a.target_org_id ? (
                      <Link href={`/orgs/${a.target_org_id}`} className="underline">
                        {a.target_org_id}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-1.5 pr-3">{a.reason}</td>
                  <td className="py-1.5 text-xs" style={{ color: "var(--muted)" }}>
                    {Object.keys(a.detail ?? {}).length ? JSON.stringify(a.detail) : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </main>
  );
}
