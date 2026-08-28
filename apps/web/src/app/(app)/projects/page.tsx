import Link from "next/link";
import { Plus, Film, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { mockProjects } from "@/lib/mock-data";
import { formatCredits } from "@mishne/shared";

export default function ProjectsPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {mockProjects.length} projects · {formatCredits(
              mockProjects.reduce((a, p) => a + p.creditsUsed, 0)
            )}{" "}
            credits used to date
          </p>
        </div>
        <Button>
          <Plus /> New project
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {mockProjects.map((p) => (
          <Link key={p.id} href={`/projects/${p.id}`}>
            <Card className="h-full p-5 transition-colors hover:border-primary/50 hover:bg-accent/30">
              <div className="flex items-start justify-between gap-3">
                <h2 className="font-medium leading-snug">{p.name}</h2>
                {p.jobCount === 0 && <Badge variant="muted">New</Badge>}
              </div>
              <div className="mt-4 flex items-center gap-4 text-sm text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <Film className="size-3.5" /> {p.assetCount}
                </span>
                <span className="flex items-center gap-1.5">
                  <Clock className="size-3.5" /> {p.jobCount} jobs
                </span>
              </div>
              <div className="mt-4 flex items-baseline justify-between border-t border-border pt-3">
                <span className="text-xs text-muted-foreground">Credits used</span>
                <span className="tc text-sm font-medium">
                  {formatCredits(p.creditsUsed)}
                </span>
              </div>
            </Card>
          </Link>
        ))}

        <button className="flex min-h-[168px] items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground">
          <Plus className="mr-2 size-4" /> New project
        </button>
      </div>
    </div>
  );
}
