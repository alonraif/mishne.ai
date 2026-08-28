import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { jobById } from "@/lib/mock-data";
import { mockTranscript } from "@/lib/mock-transcript";
import { TranscriptViewer } from "@/components/transcript-viewer";

export default async function TranscriptPage({
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
        <h1 className="text-2xl font-semibold tracking-tight">Transcript</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Every beat considered, what made the cut, and why.
        </p>
      </div>
      <TranscriptViewer transcript={mockTranscript} />
    </div>
  );
}
