"use client";

/**
 * What a screen shows while it is asking, and when the answer is no.
 *
 * One component rather than seven copies, because the three cases each have a
 * right answer and it is not obvious:
 *
 * **Loading** is a skeleton in the shape of the thing being loaded, not a
 * spinner in the middle of an empty page — the layout should not jump when the
 * data lands.
 *
 * **404 is not an error.** A job id somebody typed wrong, or a project that
 * belonged to another org, is an ordinary answer and gets a plain sentence.
 * `notFound()` would be the server-component way to say it; these screens are
 * client components (see `use-api.ts`), and rendering the message here keeps
 * the app chrome and the back link the customer needs.
 *
 * **A retryable failure gets a retry button; a 403 does not.** `ApiError`
 * already knows the difference. Offering "try again" for a permission error
 * teaches people to click it, and it will never work.
 */

import { AlertTriangle, RotateCw, SearchX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ApiError } from "@/lib/api";
import type { Query } from "@/lib/use-api";

export function QueryState<T>({
  query,
  missing = "That is not here.",
  skeleton,
  children,
}: {
  query: Query<T>;
  /** What to say on a 404, in the words of the thing being looked for. */
  missing?: string;
  skeleton?: React.ReactNode;
  children: (data: T) => React.ReactNode;
}) {
  // Data first: during a re-poll there is both data and, if the last poll
  // failed, an error. Showing the last good answer beats blanking the screen
  // because one request in a series did not land.
  if (query.data !== null) return <>{children(query.data)}</>;
  if (query.loading) return <>{skeleton ?? <CardsSkeleton />}</>;
  if (query.error) return <ErrorState error={query.error} missing={missing} onRetry={query.refetch} />;
  return null;
}

export function ErrorState({
  error,
  missing,
  onRetry,
}: {
  error: ApiError;
  missing: string;
  onRetry: () => void;
}) {
  if (error.status === 404) {
    return (
      <Card className="flex flex-col items-center gap-3 p-12 text-center">
        <SearchX className="size-6 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">{missing}</p>
      </Card>
    );
  }
  return (
    <Card className="flex flex-col items-center gap-3 border-destructive/30 bg-destructive/5 p-12 text-center">
      <AlertTriangle className="size-6 text-destructive" />
      <div>
        <p className="text-sm font-medium text-destructive">
          {error.status === 0 ? "Could not reach the server" : "That did not work"}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">{error.detail}</p>
      </div>
      {error.retryable && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RotateCw /> Try again
        </Button>
      )}
    </Card>
  );
}

export function CardsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="grid gap-3">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-[76px] w-full rounded-lg" />
      ))}
    </div>
  );
}

export function PageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-4 w-80" />
      </div>
      <CardsSkeleton />
    </div>
  );
}
