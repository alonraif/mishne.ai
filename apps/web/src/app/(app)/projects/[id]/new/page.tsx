"use client";

import { useParams } from "next/navigation";
import { PageSkeleton, QueryState } from "@/components/query-state";
import { NewJobFlow } from "@/components/new-job-flow";
import { useSession } from "@/components/session-provider";
import { useApi } from "@/lib/use-api";
import type { Asset, Project } from "@mishne/shared";

export default function NewJobPage() {
  const { id } = useParams<{ id: string }>();
  const { session } = useSession();
  const project = useApi<Project>(`/v1/projects/${id}`);
  // Polled while anything is still arriving. An upload finishes inside this
  // screen, and a probe takes a few seconds after that — without a poll the
  // wizard went on saying "nothing ready to cut yet" about the file the
  // customer had just watched upload, which reads as the upload having been
  // thrown away.
  const assets = useApi<Asset[]>(`/v1/projects/${id}/assets`, {
    poll: (rows) =>
      rows.some((a) => a.status === "uploading" || a.status === "probing")
        ? 2_000
        : null,
  });

  return (
    <QueryState query={project} missing="No such project." skeleton={<PageSkeleton />}>
      {(project) => (
        <NewJobFlow
          project={project}
          // Only material that has been probed can be priced or cut: an asset
          // still uploading has a placeholder rate of 1/1 and no duration.
          //
          // `awaiting_media` belongs here too. It is a probed sequence with a
          // real rate and a real duration that is waiting for some of the media
          // it references — a cut can be made without it, on the record
          // (ADR-0014). Filtering it out meant a linked AAF simply never
          // appeared in this list, which reads as an upload that vanished.
          // Companions are not source material. They are the files a linked
          // AAF references, uploaded to satisfy it, and they are only ever cut
          // through the sequence that names them — listing them here offers a
          // single microphone as something to transcribe, at the price of the
          // whole running time again.
          assets={(assets.data ?? []).filter(
            (a) =>
              !a.companionOf &&
              (a.status === "ready" || a.status === "awaiting_media")
          )}
          // How many are still being read, so the empty state can say "one
          // moment" rather than "nothing here".
          arriving={
            (assets.data ?? []).filter(
              (a) => a.status === "uploading" || a.status === "probing"
            ).length
          }
          onAssetsChanged={assets.refetch}
          // The live balance, from the session the app already holds, rather
          // than a second request for a number that is on screen anyway.
          balance={session.org.credit_balance}
          tierId={session.org.tier}
        />
      )}
    </QueryState>
  );
}
