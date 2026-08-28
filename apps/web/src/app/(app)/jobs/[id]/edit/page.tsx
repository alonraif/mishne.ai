import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { jobById } from "@/lib/mock-data";
import { mockTranscript } from "@/lib/mock-transcript";
import { CutEditor } from "@/components/cut-editor";

export default async function EditPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const job = jobById(id);
  if (!job) notFound();

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={`/jobs/${job.id}`}
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" /> {job.id}
        </Link>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Build the cut</h1>
          {job.mode === "hybrid" && (
            <Badge variant="outline" className="gap-1 border-primary/40 text-primary">
              <Sparkles className="size-3" /> AI suggestion loaded
            </Badge>
          )}
          {job.mode === "manual" && <Badge variant="muted">Manual</Badge>}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {job.mode === "manual"
            ? "Pick the lines you want and put them in order. Nothing is pre-selected."
            : "The engine's selection is loaded. Change anything you like before assembly."}
        </p>
      </div>

      <CutEditor
        transcript={mockTranscript}
        mode={job.mode}
        targetDurationS={job.brief.targetDurationS}
      />
    </div>
  );
}
