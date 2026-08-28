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
  return (
    <span className={cn("tc text-timecode", className)}>
      {formatTimecode(frames, rate, dropFrame)}
    </span>
  );
}
