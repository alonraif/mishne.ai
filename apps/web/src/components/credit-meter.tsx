import Link from "next/link";
import { formatCredits } from "@mishne/shared";
import { Wallet } from "lucide-react";
import { cn } from "@/lib/utils";

export function CreditMeter({
  balance,
  held,
  className,
}: {
  balance: number;
  held: number;
  className?: string;
}) {
  const low = balance < 30;
  return (
    <Link
      href="/billing"
      className={cn(
        "group flex items-center gap-2.5 rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent",
        low && "border-flag-lowconf/40",
        className
      )}
    >
      <Wallet className={cn("size-4", low ? "text-flag-lowconf" : "text-muted-foreground")} />
      <span className="tc font-medium">{formatCredits(balance)}</span>
      <span className="text-xs text-muted-foreground">credits</span>
      {held > 0 && (
        <span className="text-xs text-muted-foreground/70" title="Held by in-flight jobs">
          · {formatCredits(held)} held
        </span>
      )}
    </Link>
  );
}
