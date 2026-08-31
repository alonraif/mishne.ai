"use client";

/**
 * What a linked AAF is still waiting for.
 *
 * A sequence that references media it does not contain sits in
 * `awaiting_media` until the referenced files arrive. Until now nothing
 * rendered that, so the customer saw an upload that finished and then did
 * nothing — which reads as a broken product rather than as a question.
 *
 * The list is ordered by how many clips each missing file unblocks, because
 * that is the order worth uploading in: the file that unblocks forty clips is
 * the one to ask for first. `GET /v1/assets/{id}/requirements` already sorts it
 * that way; this renders it and does not re-sort.
 *
 * It polls while anything is outstanding, because the thing it is waiting for
 * is an upload happening in another part of this same screen.
 */

import { CheckCircle2, CircleDashed } from "lucide-react";
import { Card } from "@/components/ui/card";
import { useApi } from "@/lib/use-api";

interface MediaRequirement {
  basename: string;
  clipCount: number;
  satisfied: boolean;
  satisfiedByAssetId: string | null;
}

interface AssetRequirements {
  assetId: string;
  status: string;
  outstanding: number;
  requirements: MediaRequirement[];
}

export function MediaRequirements({ assetId }: { assetId: string }) {
  const query = useApi<AssetRequirements>(`/v1/assets/${assetId}/requirements`, {
    poll: (r) => (r.outstanding > 0 ? 10_000 : null),
  });

  const data = query.data;
  if (!data || data.requirements.length === 0) return null;

  return (
    <Card className="border-primary/30 bg-primary/5 p-4">
      <div className="text-sm font-medium">
        {data.outstanding === 0
          ? "All referenced media has arrived"
          : `Waiting for ${data.outstanding} referenced ${
              data.outstanding === 1 ? "file" : "files"
            }`}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        This sequence points at media it does not contain. Upload the files
        below — the ones at the top unblock the most of the timeline.
      </p>
      <ul className="mt-3 space-y-1.5">
        {data.requirements.map((r) => (
          <li key={r.basename} className="flex items-center gap-2 text-xs">
            {r.satisfied ? (
              <CheckCircle2 className="size-3.5 shrink-0 text-stage-done" />
            ) : (
              <CircleDashed className="size-3.5 shrink-0 text-muted-foreground" />
            )}
            <span
              className={r.satisfied ? "text-muted-foreground line-through" : ""}
              dir="ltr"
            >
              {r.basename}
            </span>
            <span className="ml-auto shrink-0 text-muted-foreground">
              {r.clipCount} {r.clipCount === 1 ? "clip" : "clips"}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
