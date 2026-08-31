"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, Film, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { CardsSkeleton, QueryState } from "@/components/query-state";
import { ApiError } from "@/lib/api";
import { apiSend } from "@/lib/dto";
import { useApi } from "@/lib/use-api";
import { formatCredits, type Project } from "@mishne/shared";

export default function ProjectsPage() {
  const projects = useApi<Project[]>("/v1/projects");
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  /**
   * A project is a name and nothing else, so the form is a name and nothing
   * else. Straight into it afterwards: nobody creates a project to look at an
   * empty one — they create it because they have footage to put in it.
   */
  const create = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreating(true);
    setError(null);
    try {
      const project = await apiSend<Project>("/v1/projects", {
        json: { name: trimmed },
      });
      router.push(`/projects/${project.id}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : String(cause));
      setCreating(false);
    }
  };

  const nameField = (
    <div className="flex items-center gap-2">
      <Input
        autoFocus
        value={name}
        placeholder="Project name"
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void create();
          if (e.key === "Escape") setNaming(false);
        }}
      />
      <Button onClick={create} disabled={creating || !name.trim()}>
        {creating ? "Creating…" : "Create"}
      </Button>
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {projects.data ? (
              <>
                {projects.data.length} projects ·{" "}
                {formatCredits(projects.data.reduce((a, p) => a + p.creditsUsed, 0))}{" "}
                credits used to date
              </>
            ) : (
              " "
            )}
          </p>
        </div>
        {naming ? (
          nameField
        ) : (
          <Button onClick={() => setNaming(true)}>
            <Plus /> New project
          </Button>
        )}
      </div>

      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}

      <QueryState
        query={projects}
        missing="No projects here."
        skeleton={<CardsSkeleton rows={3} />}
      >
        {(list) => (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {list.map((p) => (
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

            <button
              onClick={() => setNaming(true)}
              className="flex min-h-[168px] items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
            >
              <Plus className="mr-2 size-4" /> New project
            </button>
          </div>
        )}
      </QueryState>
    </div>
  );
}
