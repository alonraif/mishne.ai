"use client";

import { AlertTriangle, Check, Sparkles } from "lucide-react";
import {
  CREDIT_PACKS,
  TIERS,
  formatCredits,
  type LedgerKind,
} from "@mishne/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { CardsSkeleton, QueryState } from "@/components/query-state";
import { useSession } from "@/components/session-provider";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";
import type { LedgerEntry, Project } from "@mishne/shared";

/** `GET /v1/billing/projects` — netted across holds, settles and releases. */
interface ProjectSpend {
  projectId: string;
  credits: number;
  jobs: number;
  lastActivity: string | null;
}

/** `GET /v1/billing/balance/warning`. */
interface BalanceWarning {
  low: boolean;
  available: number;
  message?: string;
}

const KIND_STYLE: Record<LedgerKind, string> = {
  purchase: "text-stage-done",
  grant: "text-stage-done",
  refund: "text-stage-done",
  release: "text-stage-done",
  hold: "text-stage-active",
  settle: "text-foreground",
  adjustment: "text-muted-foreground",
};

export default function BillingPage() {
  const { session } = useSession();
  const org = session.org;
  const tier = TIERS[org.tier];
  const available = org.credit_balance;

  const ledger = useApi<LedgerEntry[]>("/v1/billing/ledger");
  const spend = useApi<ProjectSpend[]>("/v1/billing/projects");
  const projects = useApi<Project[]>("/v1/projects");
  // Built in C1 and rendered nowhere until now. A customer who finds out they
  // are out of credits when a job is refused has already uploaded the material.
  const warning = useApi<BalanceWarning>("/v1/billing/balance/warning");

  const nameFor = (id: string) =>
    projects.data?.find((p) => p.id === id)?.name ?? id;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Billing</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {org.name} · {tier.name} plan
        </p>
      </div>

      {warning.data?.low && (
        <Card className="flex items-center gap-3 border-primary/40 bg-primary/5 p-4">
          <AlertTriangle className="size-4 shrink-0 text-primary" />
          <p className="text-sm">
            {warning.data.message ??
              "This balance will not cover the jobs you have been running."}
          </p>
        </Card>
      )}

      {/* Balance */}
      <div className="grid gap-4 sm:grid-cols-3">
        <Card className="p-5">
          <div className="text-xs text-muted-foreground">Available</div>
          <div className="tc mt-1 text-3xl font-semibold">{formatCredits(available)}</div>
          <div className="mt-1 text-xs text-muted-foreground">credits</div>
        </Card>
        <Card className="p-5">
          <div className="text-xs text-muted-foreground">Held by running jobs</div>
          <div className="tc mt-1 text-3xl font-semibold text-stage-active">
            {formatCredits(org.credits_held)}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            released when jobs settle
          </div>
        </Card>
        <Card className="p-5">
          <div className="text-xs text-muted-foreground">Rate on {tier.name}</div>
          <div className="tc mt-1 text-3xl font-semibold">
            {tier.creditRatePerSourceHour}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">credits per source hour</div>
        </Card>
      </div>

      {/* Buy credits */}
      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Buy credits</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          {CREDIT_PACKS.map((p) => (
            <Card
              key={p.id}
              className={cn(
                "relative p-5",
                p.id === "pack_100" && "border-primary/50"
              )}
            >
              {p.bonus > 0 && (
                <Badge
                  variant={p.id === "pack_100" ? "default" : "secondary"}
                  className="absolute -top-2.5 left-5"
                >
                  {p.label}
                </Badge>
              )}
              <div className="text-3xl font-semibold">${p.amount}</div>
              <div className="mt-1.5 text-sm">
                <span className="tc font-medium">{p.credits}</span>{" "}
                <span className="text-muted-foreground">credits</span>
              </div>
              {p.bonus > 0 && (
                <div className="mt-1 flex items-center gap-1 text-xs text-stage-done">
                  <Sparkles className="size-3" /> {p.bonus} bonus credits
                </div>
              )}
              <Button
                className="mt-4 w-full"
                variant={p.id === "pack_100" ? "default" : "outline"}
              >
                Buy
              </Button>
            </Card>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Credits do not expire. 1 credit = US$1. Jobs are charged for actual usage,
          never more than the amount approved at submission.
        </p>
      </section>

      {/* Plans */}
      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Plan</h2>
        <div className="grid gap-4 lg:grid-cols-3">
          {Object.values(TIERS).map((t) => {
            const current = t.id === org.tier;
            return (
              <Card
                key={t.id}
                className={cn("flex flex-col p-5", current && "border-primary")}
              >
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold">{t.name}</h3>
                  {current && <Badge>Current</Badge>}
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{t.blurb}</p>
                <div className="mt-4">
                  {t.monthlyPrice === null ? (
                    <span className="text-2xl font-semibold">Custom</span>
                  ) : (
                    <>
                      <span className="text-2xl font-semibold">${t.monthlyPrice}</span>
                      <span className="text-sm text-muted-foreground">/month</span>
                    </>
                  )}
                </div>
                <div className="tc mt-1 text-sm text-muted-foreground">
                  {t.creditRatePerSourceHour} credits per source hour
                </div>
                <Separator className="my-4" />
                <ul className="flex-1 space-y-2">
                  {t.features.map((f) => (
                    <li key={f} className="flex gap-2 text-sm">
                      <Check className="mt-0.5 size-3.5 shrink-0 text-used" />
                      <span className="text-muted-foreground">{f}</span>
                    </li>
                  ))}
                </ul>
                <Button
                  variant={current ? "outline" : "default"}
                  disabled={current}
                  className="mt-5 w-full"
                >
                  {current ? "Current plan" : t.monthlyPrice === null ? "Contact sales" : "Upgrade"}
                </Button>
              </Card>
            );
          })}
        </div>
      </section>

      {/* Per-project usage */}
      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Usage by project</h2>
        <Card>
          <CardContent className="pt-5">
            <QueryState query={spend} missing="Nothing spent yet." skeleton={<CardsSkeleton rows={2} />}>
              {(rows) => {
                const used = rows.filter((r) => r.credits > 0);
                if (used.length === 0) {
                  return (
                    <p className="py-2 text-sm text-muted-foreground">
                      No project has been billed for yet.
                    </p>
                  );
                }
                // The largest bar is the largest project, not the balance: this
                // is a comparison between projects, not a budget.
                const max = Math.max(...used.map((r) => r.credits));
                return used.map((r) => (
                  <div key={r.projectId} className="py-2.5">
                    <div className="flex items-baseline justify-between text-sm">
                      <span>{nameFor(r.projectId)}</span>
                      <span className="tc text-muted-foreground">
                        {formatCredits(r.credits)}
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-secondary">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${(r.credits / max) * 100}%` }}
                      />
                    </div>
                  </div>
                ));
              }}
            </QueryState>
          </CardContent>
        </Card>
      </section>

      {/* Ledger */}
      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Recent activity</h2>
        <Card>
          <CardContent className="pt-5">
            <QueryState query={ledger} missing="No activity yet." skeleton={<CardsSkeleton rows={3} />}>
              {(entries) =>
                entries.length === 0 ? (
                  <p className="py-2 text-sm text-muted-foreground">
                    Nothing has been bought or spent yet.
                  </p>
                ) : (
            <div className="divide-y divide-border">
              {entries.map((e) => (
                <div key={e.id} className="flex items-center gap-4 py-3 text-sm">
                  <Badge variant="muted" className="w-20 justify-center capitalize">
                    {e.kind}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {e.description}
                  </span>
                  <span className="tc text-xs text-muted-foreground/70">
                    {new Date(e.createdAt).toLocaleDateString("en-GB", {
                      day: "2-digit",
                      month: "short",
                    })}
                  </span>
                  <span className={cn("tc w-20 text-right", KIND_STYLE[e.kind])}>
                    {e.delta > 0 ? "+" : ""}
                    {formatCredits(e.delta)}
                  </span>
                </div>
              ))}
            </div>
                )
              }
            </QueryState>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
