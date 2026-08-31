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
  const assets = useApi<Asset[]>(`/v1/projects/${id}/assets`);

  return (
    <QueryState query={project} missing="No such project." skeleton={<PageSkeleton />}>
      {(project) => (
        <NewJobFlow
          project={project}
          // Only material that has been probed can be priced or cut: an asset
          // still uploading has a placeholder rate of 1/1 and no duration.
          assets={(assets.data ?? []).filter((a) => a.status === "ready")}
          // The live balance, from the session the app already holds, rather
          // than a second request for a number that is on screen anyway.
          balance={session.org.credit_balance}
          tierId={session.org.tier}
        />
      )}
    </QueryState>
  );
}
