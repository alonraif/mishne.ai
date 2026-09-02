"use client";

/**
 * The preview rendition for one asset: where it is, and a URL to play it.
 *
 * Two things this has to get right, and both are about the URL being a
 * credential rather than a location.
 *
 * **It expires.** `proxy_presign_ttl_seconds` is six hours, long enough for a
 * working session, and a session can outlast it. When it goes, the media
 * element reports a *decode* error — nothing about credentials, nothing in the
 * console about 403 — so `reload` is wired to the element's `error` event and
 * mints a fresh one. That is the whole recovery path and it is invisible.
 *
 * **It is never persisted.** Not to localStorage, not into a link. It grants
 * read access to the customer's footage to anyone holding it, and the API
 * writes an audit row each time one is minted.
 */

import { useCallback } from "react";
import type { AssetProxy } from "@mishne/shared";
import { useApi } from "./use-api";

/** How often to ask again while the transcode is still running. */
const POLL_MS = 5_000;

export interface Preview {
  status: AssetProxy["status"];
  kind: AssetProxy["kind"];
  url: string | null;
  /** True while there is a reason to expect this to become playable. */
  building: boolean;
  /** Mint a fresh URL — on expiry, or after a failure the user retries. */
  reload: () => void;
}

export function usePreview(assetId: string | null): Preview {
  const query = useApi<AssetProxy>(
    assetId ? `/v1/assets/${assetId}/proxy` : null,
    {
      // Stop asking the moment the answer stops being able to change.
      // `unsupported` and `none` are verdicts, not stages.
      poll: (p) =>
        p.status === "pending" || p.status === "running" ? POLL_MS : null,
    }
  );

  const { refetch } = query;
  const reload = useCallback(() => refetch(), [refetch]);

  // The reader keeps the previous answer on screen while the next one is in
  // flight — right for a job page that would otherwise flash a skeleton every
  // few seconds, wrong here. On a multi-reel job that stale answer belongs to
  // the reel that *was* showing, and handing it to the player means playing
  // the wrong footage, seeked to a position taken from a different reel.
  const answer =
    query.data && query.data.assetId === assetId ? query.data : null;

  const status = answer?.status ?? "pending";
  return {
    status,
    kind: answer?.kind ?? "",
    url: answer?.url ?? null,
    building: status === "pending" || status === "running",
    reload,
  };
}
