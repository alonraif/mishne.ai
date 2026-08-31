"use client";

/**
 * Reading from the API, in a client component, because it has to be.
 *
 * The session is an httpOnly cookie set for the API's origin (see
 * `session-provider.tsx`). A server component rendering inside Next has no
 * legitimate way to read it, so every credentialed read happens in the browser
 * — which is why the screens that used to be `async function Page()` reading a
 * fixture are now client components reading this hook.
 *
 * ## No data-fetching library
 *
 * The whole requirement is: fetch on mount, re-fetch on demand, and poll while
 * something is moving. That is this file. A cache-and-revalidate library would
 * bring a dependency, a cache whose staleness rules somebody has to learn, and
 * a second answer to "what is on screen right now" — for an app whose screens
 * each read one or two endpoints.
 *
 * ## Polling, not a socket
 *
 * A job is minutes long and its steps change every few seconds. Polling is a
 * `setTimeout` and an endpoint that already exists; a stream is a connection to
 * keep alive through a load balancer, a reconnect path, and a second way for
 * the UI to be wrong. `poll` returns the delay in milliseconds given the data
 * in hand, or null to stop — so a finished job stops asking, which is the part
 * a fixed interval gets wrong.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import { apiGet } from "./dto";

export interface Query<T> {
  data: T | null;
  error: ApiError | null;
  /** True until the first response, either way. Not true during a re-poll: a
   *  screen that flashes a skeleton every three seconds is worse than one that
   *  shows the previous answer while it asks again. */
  loading: boolean;
  refetch: () => void;
}

export function useApi<T>(
  path: string | null,
  options: { poll?: (data: T) => number | null } = {}
): Query<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [nonce, setNonce] = useState(0);

  // Kept in a ref so a caller can pass an inline arrow without re-running the
  // effect on every render — which would cancel and restart the request each
  // time and never settle.
  const poll = options.poll;
  const pollRef = useRef(poll);
  pollRef.current = poll;

  const refetch = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (path === null) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const run = async () => {
      try {
        const next = await apiGet<T>(path);
        if (cancelled) return;
        setData(next);
        setError(null);
        const delay = pollRef.current?.(next) ?? null;
        if (delay !== null) timer = setTimeout(run, delay);
      } catch (cause) {
        if (cancelled) return;
        // A thrown non-ApiError is a bug in this app, not a failed request, and
        // swallowing it into an error banner would hide it.
        if (!(cause instanceof ApiError)) throw cause;
        setError(cause);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    setLoading(true);
    void run();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [path, nonce]);

  return { data, error, loading, refetch };
}
