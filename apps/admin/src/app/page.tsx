"use client";

/**
 * Sign in, then the list of organisations. One screen, because the operator's
 * first question is always "which customer" and a separate landing page would
 * be a click between them and it.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AdminError, get, post, type Org, type Overview } from "@/lib/api";
import { Button, Credits, Field, Input, Note, Panel, When } from "@/lib/ui";

export default function Home() {
  const [me, setMe] = useState<{ email: string } | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    get<{ email: string }>("/auth/me")
      .then(setMe)
      .catch((e) => {
        // A 401 is the normal state before signing in, not something to shout
        // about. Anything else — the API being down, above all — is worth
        // saying out loud, because the alternative is a login form that
        // silently never works.
        if (!(e instanceof AdminError) || e.status !== 401) setError(String(e.message));
      })
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <Centre>…</Centre>;
  if (!me) return <SignIn onDone={setMe} outerError={error} />;
  return <Orgs email={me.email} />;
}

function Centre({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-dvh place-items-center p-6">
      <div className="w-full max-w-sm space-y-4">{children}</div>
    </main>
  );
}

function SignIn({
  onDone,
  outerError,
}: {
  onDone: (me: { email: string }) => void;
  outerError: string;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onDone(await post<{ email: string }>("/auth/login", { email, password }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setBusy(false);
    }
  };

  return (
    <Centre>
      <h1 className="text-lg font-semibold">mishne.ai back-office</h1>
      <p className="text-sm" style={{ color: "var(--muted)" }}>
        Platform administration. This is not a customer sign-in.
      </p>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Email">
          <Input
            autoFocus
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field label="Password">
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <Button tone="accent" disabled={busy || !email || !password} className="w-full">
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
      <Note kind="error">{error || outerError}</Note>
    </Centre>
  );
}

function Orgs({ email }: { email: string }) {
  const [orgs, setOrgs] = useState<Org[] | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async (query: string) => {
    try {
      const [rows, totals] = await Promise.all([
        get<Org[]>(`/orgs?q=${encodeURIComponent(query)}`),
        get<Overview>("/overview"),
      ]);
      setOrgs(rows);
      setOverview(totals);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => void load(q), q ? 200 : 0);
    return () => clearTimeout(timer);
  }, [q, load]);

  return (
    <main className="mx-auto max-w-6xl space-y-5 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold">Organisations</h1>
        <div className="flex items-center gap-3 text-xs" style={{ color: "var(--muted)" }}>
          <Link href="/actions" className="underline">
            Action log
          </Link>
          <span>{email}</span>
          <button
            className="underline"
            onClick={async () => {
              await post("/auth/logout");
              location.reload();
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      {overview && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <Stat label="Organisations" value={String(overview.orgs)} />
          <Stat label="Suspended" value={String(overview.suspended)} />
          <Stat label="Credits outstanding" value={overview.credits_outstanding.toFixed(2)} />
          <Stat label="Held by running jobs" value={overview.credits_held.toFixed(2)} />
          <Stat label="Jobs in flight" value={String(overview.jobs_running)} />
        </div>
      )}

      <Note kind="error">{error}</Note>

      <Panel
        title={orgs ? `${orgs.length} shown` : "Loading…"}
        right={
          <div className="w-64">
            <Input
              placeholder="Search by name or id"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead style={{ color: "var(--muted)" }}>
              <tr className="text-left">
                <th className="pb-2 font-normal">Name</th>
                <th className="pb-2 font-normal">Tier</th>
                <th className="pb-2 text-right font-normal">Available</th>
                <th className="pb-2 text-right font-normal">Held</th>
                <th className="pb-2 text-right font-normal">Users</th>
                <th className="pb-2 text-right font-normal">Projects</th>
                <th className="pb-2 text-right font-normal">Jobs</th>
                <th className="pb-2 font-normal">Created</th>
              </tr>
            </thead>
            <tbody>
              {orgs?.map((o) => (
                <tr key={o.id} className="border-t" style={{ borderColor: "var(--line)" }}>
                  <td className="py-2">
                    <Link href={`/orgs/${o.id}`} className="underline">
                      {o.name}
                    </Link>
                    <div className="text-xs" style={{ color: "var(--muted)" }}>
                      {o.id}
                    </div>
                    {o.suspended_at && (
                      <div className="text-xs" style={{ color: "var(--danger)" }}>
                        suspended{o.suspended_reason ? `: ${o.suspended_reason}` : ""}
                      </div>
                    )}
                  </td>
                  <td className="py-2">{o.tier}</td>
                  <td className="py-2 text-right">
                    <Credits value={o.available} />
                  </td>
                  <td className="py-2 text-right">
                    <Credits value={o.held} />
                  </td>
                  <td className="py-2 text-right tabular">{o.user_count}</td>
                  <td className="py-2 text-right tabular">{o.project_count}</td>
                  <td className="py-2 text-right tabular">{o.job_count}</td>
                  <td className="py-2 text-xs" style={{ color: "var(--muted)" }}>
                    <When iso={o.created_at} />
                  </td>
                </tr>
              ))}
              {orgs?.length === 0 && (
                <tr>
                  <td colSpan={8} className="py-6 text-center" style={{ color: "var(--muted)" }}>
                    Nothing matches.
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

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="rounded-lg border p-3"
      style={{ borderColor: "var(--line)", background: "var(--panel)" }}
    >
      <div className="text-xs" style={{ color: "var(--muted)" }}>
        {label}
      </div>
      <div className="tabular mt-1 text-xl font-semibold">{value}</div>
    </div>
  );
}
