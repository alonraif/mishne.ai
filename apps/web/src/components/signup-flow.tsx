"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Check, Sparkles } from "lucide-react";
import { CREDIT_PACKS, TIERS, type CreditPackId, type TierId } from "@mishne/shared";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

const STEPS = ["Account", "Plan", "Credits"] as const;

export function SignupFlow() {
  const [step, setStep] = useState(0);
  const [tier, setTier] = useState<TierId>("pro");
  const [pack, setPack] = useState<CreditPackId>("pack_100");

  const selectedPack = CREDIT_PACKS.find((p) => p.id === pack)!;
  const selectedTier = TIERS[tier];

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-2">
        {STEPS.map((label, i) => (
          <div key={label} className="flex flex-1 items-center gap-2">
            <div
              className={cn(
                "flex items-center gap-2 text-sm",
                i === step ? "text-foreground" : "text-muted-foreground"
              )}
            >
              <span
                className={cn(
                  "grid size-5 place-items-center rounded-full border text-[11px]",
                  i < step && "border-stage-done/50 bg-stage-done/15 text-stage-done",
                  i === step && "border-primary bg-primary text-primary-foreground",
                  i > step && "border-border"
                )}
              >
                {i < step ? <Check className="size-3" /> : i + 1}
              </span>
              {label}
            </div>
            {i < STEPS.length - 1 && <div className="h-px flex-1 bg-border" />}
          </div>
        ))}
      </div>

      {step === 0 && (
        <div className="mx-auto max-w-sm space-y-4">
          <h1 className="text-2xl font-semibold tracking-tight">Create your account</h1>
          <div className="grid gap-2">
            <Label htmlFor="name">Your name</Label>
            <Input id="name" placeholder="Alon Raif" />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="org">Company or studio</Label>
            <Input id="org" placeholder="Northline Post" />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="email">Work email</Label>
            <Input id="email" type="email" placeholder="you@studio.tv" />
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="space-y-4">
          <div className="text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Choose a plan</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              The plan sets your rate and limits. Jobs are paid for with credits.
            </p>
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            {Object.values(TIERS).map((t) => {
              const on = t.id === tier;
              return (
                <button key={t.id} onClick={() => setTier(t.id)} className="text-left">
                  <Card
                    className={cn(
                      "flex h-full flex-col p-5 transition-colors",
                      on ? "border-primary bg-primary/5" : "hover:border-primary/40"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <h3 className="font-semibold">{t.name}</h3>
                      {on && <Check className="size-4 text-primary" />}
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
                  </Card>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="mx-auto max-w-2xl space-y-6">
          <div className="text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Add credits</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Credits pay for jobs and never expire. On {selectedTier.name}, a
              three-hour interview costs about{" "}
              <span className="tc">
                {Math.ceil(3 * selectedTier.creditRatePerSourceHour + 1)}
              </span>{" "}
              credits.
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            {CREDIT_PACKS.map((p) => {
              const on = p.id === pack;
              return (
                <button key={p.id} onClick={() => setPack(p.id)} className="text-left">
                  <Card
                    className={cn(
                      "relative h-full p-5 transition-colors",
                      on ? "border-primary bg-primary/5" : "hover:border-primary/40"
                    )}
                  >
                    {p.bonus > 0 && (
                      <Badge variant={on ? "default" : "secondary"} className="absolute -top-2.5 left-5">
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
                        <Sparkles className="size-3" /> {p.bonus} bonus
                      </div>
                    )}
                    <div className="mt-3 text-xs text-muted-foreground">
                      ≈ {Math.floor(p.credits / (3 * selectedTier.creditRatePerSourceHour + 1))}{" "}
                      three-hour jobs
                    </div>
                  </Card>
                </button>
              );
            })}
          </div>

          <Card className="p-5">
            <div className="flex items-baseline justify-between text-sm">
              <span className="text-muted-foreground">{selectedTier.name} plan</span>
              <span className="tc">
                {selectedTier.monthlyPrice === null
                  ? "Custom"
                  : `$${selectedTier.monthlyPrice}/mo`}
              </span>
            </div>
            <div className="mt-2 flex items-baseline justify-between text-sm">
              <span className="text-muted-foreground">
                Credit pack · {selectedPack.credits} credits
              </span>
              <span className="tc">${selectedPack.amount}</span>
            </div>
            <Separator className="my-3" />
            <div className="flex items-baseline justify-between">
              <span className="font-medium">Due today</span>
              <span className="tc text-xl font-semibold">
                ${(selectedTier.monthlyPrice ?? 0) + selectedPack.amount}
              </span>
            </div>
          </Card>
        </div>
      )}

      <div className="flex items-center justify-between">
        <Button
          variant="ghost"
          onClick={() => setStep((s) => Math.max(0, s - 1))}
          disabled={step === 0}
        >
          <ArrowLeft /> Back
        </Button>
        {step < 2 ? (
          <Button onClick={() => setStep((s) => s + 1)}>
            Continue <ArrowRight />
          </Button>
        ) : (
          <Button asChild>
            <Link href="/projects">
              <Check /> Create account
            </Link>
          </Button>
        )}
      </div>
    </div>
  );
}
