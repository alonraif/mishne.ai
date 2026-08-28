import { notFound } from "next/navigation";
import { projectById, assetsForProject, mockOrg } from "@/lib/mock-data";
import { NewJobFlow } from "@/components/new-job-flow";

export default async function NewJobPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const project = projectById(id);
  if (!project) notFound();

  return (
    <NewJobFlow
      project={project}
      assets={assetsForProject(id).filter((a) => a.status === "ready")}
      balance={mockOrg.creditBalance}
      tierId={mockOrg.tier}
    />
  );
}
