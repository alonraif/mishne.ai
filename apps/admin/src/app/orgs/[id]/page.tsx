"use client";

/**
 * One organisation: what it is, what it owes, and the four things an operator
 * can do to it. Every one of those four asks for a reason before it will go
 * through, because the reason is the record — see `platform_actions`.
 */

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { get, patch, post, type Action, type OrgDetail } from "@/lib/api";
import { Button, Credits, Field, Input, Note, Panel, Select, When } from "@/lib/ui";

export default function OrgPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [history, setHistory] = useState<Action[]>([]);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  const load = useCallback(async () => {
    try {
      setOrg(await get<OrgDetail>(`/orgs/${id}`));
      setHistory(await get<Action[]>(`/actions?org_id=${id}`));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Every mutation goes through here, so every one of them reloads and every
   *  one of them reports the same way. */
  const act = async (fn: () => Promise<unknown>, said: string) => {
    setError("");
    setDone("");
    try {
      await fn();
      setDone(said);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  if (!org) {
    return (
      <main className="mx-auto max-w-6xl p-6">
        <Note kind="error">{error}</Note>
        {!error && <p style={{ color: "var(--muted)" }}>Loading…</p>}
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-6xl space-y-5 p-6">
      <header className="flex items-baseline justify-between">
        <div>
          <Link href="/" className="text-xs underline" style={{ color: "var(--muted)" }}>
            ← all organisations
          </Link>
          <h1 className="mt-1 text-lg font-semibold">{org.name}</h1>
          <p className="text-xs" style={{ color: "var(--muted)" }}>
            {org.id} · {org.tier} · {org.retention_days} day retention · created{" "}
            <When iso={org.created_at} />
          </p>
        </div>
        <div className="text-right">
          <div className="tabular text-2xl font-semibold">
            <Credits value={org.available} />
          </div>
          <div className="text-xs" style={{ color: "var(--muted)" }}>
            available · <Credits value={org.held} /> held
          </div>
        </div>
      </header>

      {org.suspended_at && (
        <Note kind="error">
          Suspended{org.suspended_reason ? `: ${org.suspended_reason}` : ""}. Nobody in
          this organisation can sign in.
        </Note>
      )}
      <Note kind="error">{error}</Note>
      <Note kind="ok">{done}</Note>

      <div className="grid gap-5 lg:grid-cols-2">
        <Credit org={org} act={act} />
        <Plan org={org} act={act} />
        <Suspension org={org} act={act} />
        <Danger org={org} act={act} />
      </div>

      <Panel title={`Ledger — last ${org.ledger.length}`}>
        <Rows
          head={["When", "Kind", "Delta", "Balance", "Description"]}
          rows={org.ledger.map((e) => [
            <When key="w" iso={e.created_at} />,
            e.kind,
            <Credits key="d" value={e.delta} />,
            <Credits key="b" value={e.balance_after} />,
            e.description,
          ])}
        />
      </Panel>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title={`Members — ${org.members.length}`}>
          <Rows
            head={["Email", "Role", "Joined"]}
            rows={org.members.map((m) => [
              m.email,
              m.role,
              <When key="w" iso={m.created_at} />,
            ])}
          />
        </Panel>
        <Panel title={`Projects — ${org.projects.length}`}>
          <Rows
            head={["Name", "Jobs", "Created"]}
            rows={org.projects.map((p) => [
              p.name + (p.archived ? " (archived)" : ""),
              String(p.job_count),
              <When key="w" iso={p.created_at} />,
            ])}
          />
        </Panel>
      </div>

      <Panel title={`Recent jobs — ${org.recent_jobs.length}`}>
        <Rows
          head={["Job", "Status", "Mode", "Cap", "Charged", "Created"]}
          rows={org.recent_jobs.map((j) => [
            j.id,
            j.status,
            j.mode,
            <Credits key="c" value={j.approved_cap} />,
            <Credits key="s" value={j.credits_settled} />,
            <When key="w" iso={j.created_at} />,
          ])}
        />
      </Panel>

      <Panel title="What we have done to this organisation">
        <Rows
          head={["When", "Who", "Action", "Reason"]}
          rows={history.map((a) => [
            <When key="w" iso={a.created_at} />,
            a.admin_email ?? "—",
            a.action,
            a.reason,
          ])}
        />
      </Panel>
    </main>
  );
}

type Act = (fn: () => Promise<unknown>, said: string) => Promise<void>;

function Credit({ org, act }: { org: OrgDetail; act: Act }) {
  const [credits, setCredits] = useState("");
  const [reason, setReason] = useState("");
  const amount = Number(credits);
  const valid = credits !== "" && Number.isFinite(amount) && reason.trim().length >= 3;

  return (
    <Panel title="Credits">
      <div className="space-y-3">
        <Field
          label="Amount"
          hint="Negative is a correction. It cannot take the balance below zero."
        >
          <Input
            inputMode="decimal"
            placeholder="100"
            value={credits}
            onChange={(e) => setCredits(e.target.value)}
          />
        </Field>
        <Field label="Reason" hint="Recorded permanently. The customer never sees it.">
          <Input
            placeholder="launch partner, agreed on the call"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        <div className="flex items-center justify-between">
          <span className="text-xs" style={{ color: "var(--muted)" }}>
            {valid
              ? `${org.available.toFixed(2)} → ${(org.available + amount).toFixed(2)}`
              : " "}
          </span>
          <Button
            tone="accent"
            disabled={!valid}
            onClick={() =>
              act(
                () => post(`/orgs/${org.id}/credits`, { credits: amount, reason }),
                `${amount >= 0 ? "Added" : "Removed"} ${Math.abs(amount)} credits.`
              ).then(() => {
                setCredits("");
                setReason("");
              })
            }
          >
            Apply
          </Button>
        </div>
      </div>
    </Panel>
  );
}

function Plan({ org, act }: { org: OrgDetail; act: Act }) {
  const [tier, setTier] = useState(org.tier);
  const [days, setDays] = useState(String(org.retention_days));
  const [reason, setReason] = useState("");
  const ok = reason.trim().length >= 3;

  return (
    <Panel title="Plan and retention">
      <div className="space-y-3">
        <Field label="Tier">
          <Select value={tier} onChange={(e) => setTier(e.target.value as OrgDetail["tier"])}>
            <option value="starter">starter</option>
            <option value="pro">pro</option>
            <option value="studio">studio</option>
          </Select>
        </Field>
        <Field
          label="Retention (days)"
          hint="How long customer media is kept. The lifecycle rules read this."
        >
          <Input value={days} onChange={(e) => setDays(e.target.value)} inputMode="numeric" />
        </Field>
        <Field label="Reason">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <div className="flex gap-2">
          <Button
            disabled={!ok || tier === org.tier}
            onClick={() =>
              act(() => patch(`/orgs/${org.id}/tier`, { tier, reason }), `Tier is now ${tier}.`)
            }
          >
            Change tier
          </Button>
          <Button
            disabled={!ok || Number(days) === org.retention_days}
            onClick={() =>
              act(
                () =>
                  patch(`/orgs/${org.id}/retention`, {
                    retention_days: Number(days),
                    reason,
                  }),
                `Retention is now ${days} days.`
              )
            }
          >
            Change retention
          </Button>
        </div>
      </div>
    </Panel>
  );
}

function Suspension({ org, act }: { org: OrgDetail; act: Act }) {
  const [reason, setReason] = useState("");
  const ok = reason.trim().length >= 3;
  const suspended = Boolean(org.suspended_at);

  return (
    <Panel title="Access">
      <div className="space-y-3">
        <p className="text-xs" style={{ color: "var(--muted)" }}>
          Suspending signs everyone out immediately and refuses new sign-ins with
          the reason below. Nothing is deleted.
        </p>
        <Field label="Reason">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <Button
          tone={suspended ? "normal" : "danger"}
          disabled={!ok}
          onClick={() =>
            act(
              () => post(`/orgs/${org.id}/${suspended ? "unsuspend" : "suspend"}`, { reason }),
              suspended ? "Access restored." : "Suspended."
            )
          }
        >
          {suspended ? "Restore access" : "Suspend"}
        </Button>
      </div>
    </Panel>
  );
}

function Danger({ org, act }: { org: OrgDetail; act: Act }) {
  const [confirm, setConfirm] = useState("");
  const [reason, setReason] = useState("");
  const ok = confirm.trim() === org.name && reason.trim().length >= 3;

  return (
    <Panel title="Delete">
      <div className="space-y-3">
        <p className="text-xs" style={{ color: "var(--muted)" }}>
          Removes projects, assets, jobs, transcripts, members and sessions. The
          credit ledger and the audit log are kept — they are append-only and
          what this customer was charged does not stop mattering when they leave.
          Media in object storage is removed by the retention rules, not by this.
        </p>
        <Field label={`Type the name to confirm: ${org.name}`}>
          <Input value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </Field>
        <Field label="Reason">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <Button
          tone="danger"
          disabled={!ok}
          onClick={() =>
            act(
              () => post(`/orgs/${org.id}/delete`, { confirm_name: confirm, reason }),
              "Deleted."
            )
          }
        >
          Delete this organisation
        </Button>
      </div>
    </Panel>
  );
}

function Rows({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  if (rows.length === 0)
    return (
      <p className="text-sm" style={{ color: "var(--muted)" }}>
        Nothing yet.
      </p>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead style={{ color: "var(--muted)" }}>
          <tr className="text-left">
            {head.map((h) => (
              <th key={h} className="pb-2 font-normal">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, i) => (
            <tr key={i} className="border-t" style={{ borderColor: "var(--line)" }}>
              {cells.map((c, j) => (
                <td key={j} className="py-1.5 pr-3 align-top">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
