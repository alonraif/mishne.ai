import { cn } from "@/lib/utils";

/**
 * Placeholder mark: three bars of decreasing width — three hours down to ten
 * minutes. Swap for real brand work; this exists so screens aren't wordmark-less.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
        <rect x="1" y="3" width="18" height="3" rx="1.5" className="fill-primary" />
        <rect x="1" y="8.5" width="11" height="3" rx="1.5" className="fill-primary/60" />
        <rect x="1" y="14" width="5" height="3" rx="1.5" className="fill-primary/35" />
      </svg>
      <span className="font-semibold tracking-tight">mishne<span className="text-muted-foreground">.ai</span></span>
    </span>
  );
}
