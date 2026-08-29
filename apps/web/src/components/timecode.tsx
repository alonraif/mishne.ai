import { formatTimecode, type Rate } from "@mishne/shared";
import { cn } from "@/lib/utils";

export function Timecode({
  frames,
  rate,
  dropFrame = false,
  className,
}: {
  frames: number;
  rate: Rate;
  dropFrame?: boolean;
  className?: string;
}) {
  // dir="ltr" and bidi isolation are load-bearing, not decoration. A timecode
  // inside an RTL paragraph reorders around its colons without them, and
  // timecode is the thing an editor is scanning for.
  return (
    <span
      dir="ltr"
      className={cn("tc text-timecode [unicode-bidi:isolate]", className)}
    >
      {formatTimecode(frames, rate, dropFrame)}
    </span>
  );
}
