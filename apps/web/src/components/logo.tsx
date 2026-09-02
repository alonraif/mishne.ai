import { cn } from "@/lib/utils";

/**
 * The brand mark: three bars of decreasing width — three hours of rushes down
 * to a ten-minute cut, which is the whole product in one glyph.
 *
 * The same mark is drawn again, by hand, in `src/app/icon.svg` for the browser
 * tab. A favicon renders outside the document and so cannot read the `@theme`
 * tokens; it carries literal hex and its own ground. If the geometry or the
 * palette changes here, change it there in the same commit.
 *
 * Mark and wordmark scale together: the glyph and the gap are sized in `em`, so
 * a caller's `text-lg` grows both and the lockup keeps its proportions. The
 * 1.25em / 0.5em pair is 20px and 8px at a 16px root, which is where this
 * started.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-[0.5em]", className)}>
      <svg
        width="1.25em"
        height="1.25em"
        viewBox="0 0 20 20"
        aria-hidden="true"
        className="shrink-0"
      >
        <rect x="1" y="3" width="18" height="3" rx="1.5" className="fill-primary" />
        <rect x="1" y="8.5" width="11" height="3" rx="1.5" className="fill-primary/60" />
        <rect x="1" y="14" width="5" height="3" rx="1.5" className="fill-primary/35" />
      </svg>
      <span className="font-semibold tracking-tight">mishne<span className="text-muted-foreground">.ai</span></span>
    </span>
  );
}
