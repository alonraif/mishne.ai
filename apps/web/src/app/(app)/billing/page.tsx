import { Check, Sparkles } from "lucide-react";
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
import { mockLedger, mockOrg, mockProjects } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

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
  const tier = TIERS[mockOrg.tier];
  const available = mockOrg.creditBalance;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Billing</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {mockOrg.name} · {tier.name} plan
        </p>
      </div>

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
            {formatCredits(mockOrg.creditsHeld)}
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
            const current = t.id === mockOrg.tier;
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
            {mockProjects
              .filter((p) => p.creditsUsed > 0)
              .sort((a, b) => b.creditsUsed - a.creditsUsed)
              .map((p) => {
                const max = Math.max(...mockProjects.map((x) => x.creditsUsed));
                return (
                  <div key={p.id} className="py-2.5">
                    <div className="flex items-baseline justify-between text-sm">
                      <span>{p.name}</span>
                      <span className="tc text-muted-foreground">
                        {formatCredits(p.creditsUsed)}
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-secondary">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${(p.creditsUsed / max) * 100}%` }}
                      />
                    </div>
                  </div>
                );
              })}
          </CardContent>
        </Card>
      </section>

      {/* Ledger */}
      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Recent activity</h2>
        <Card>
          <CardContent className="pt-5">
            <div className="divide-y divide-border">
              {mockLedger.map((e) => (
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
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
