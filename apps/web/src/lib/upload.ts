/**
 * Resumable, direct-to-S3 upload.
 *
 * The file never touches the API: it is hashed in the browser, the API hands
 * back one presigned URL per part, and the parts go straight to S3. A three-hour
 * ProRes master is 200 GB, and proxying that through an application server is
 * both an enormous bandwidth bill and a guaranteed timeout.
 *
 * What "resumable" means here, precisely:
 *
 * * **A dropped connection** retries the failed part, with backoff. Only that
 *   part — a part is 64 MiB, so a retry costs seconds, not the whole upload.
 * * **Expired URLs** are re-minted. The presign TTL is 900 seconds and a large
 *   upload outlives it; that is the ordinary middle of a big upload, not an
 *   error.
 * * **A closed laptop** resumes on the next visit. Asking to create the asset
 *   again returns the same asset and the same multipart upload, and
 *   `GET /upload-parts` says which parts S3 already holds — so nothing is
 *   re-sent and no client-side bookkeeping has to survive the refresh.
 */

import { api, ApiError } from "./api";
import { hashFile } from "./sha256";

export type UploadPhase =
  | "hashing"
  | "uploading"
  | "completing"
  | "done"
  | "cancelled"
  | "failed";

export interface UploadProgress {
  phase: UploadPhase;
  /** 0..1 over the phase's own work, so a hash of a 200 GB file has a bar too. */
  fraction: number;
  bytesSent: number;
  totalBytes: number;
  partsDone: number;
  totalParts: number;
  /** Parts currently being re-sent after a failure. Worth showing; it explains a stall. */
  retrying: number;
  assetId?: string;
  error?: string;
}

interface PartUrl {
  part_number: number;
  url: string;
  offset: number;
  length: number;
}

interface PresignedUpload {
  asset_id: string;
  upload_id: string;
  part_size: number;
  total_parts: number;
  parts: PartUrl[];
  expires_in_s: number;
}

interface UploadState {
  asset_id: string;
  upload_id: string;
  part_size: number;
  total_parts: number;
  total_bytes: number;
  uploaded: Array<{ part_number: number; etag: string; size: number }>;
}

export interface UploadOptions {
  projectId: string;
  file: File;
  /** Required for audio-only files, which carry no frame rate of their own. */
  rate?: { num: number; den: number };
  /** How many parts are in flight at once. Four saturates most links without starving the tab. */
  concurrency?: number;
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
}

const MAX_ATTEMPTS = 5;

/** Audio extensions, which is the one case the API needs a declared rate for. */
export const AUDIO_EXTENSIONS = [
  ".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg", ".opus", ".caf",
];

export function isAudioFile(name: string): boolean {
  const lower = name.toLowerCase();
  return AUDIO_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export async function uploadAsset(options: UploadOptions): Promise<string> {
  const { projectId, file, rate, concurrency = 4, onProgress, signal } = options;
  const report = (p: Partial<UploadProgress> & { phase: UploadPhase }) =>
    onProgress?.({
      fraction: 0,
      bytesSent: 0,
      totalBytes: file.size,
      partsDone: 0,
      totalParts: 0,
      retrying: 0,
      ...p,
    });

  // ── hash ────────────────────────────────────────────────────────────────
  // The digest is the asset's identity: it is what makes a retry idempotent and
  // what lets the same rushes be transcribed once across two projects.
  report({ phase: "hashing", fraction: 0 });
  let checksum: string;
  let created: PresignedUpload;
  try {
    checksum = await hashFile(
      file,
      (read, total) => report({ phase: "hashing", fraction: read / (total || 1) }),
      signal
    );

    // ── ask for the parts ─────────────────────────────────────────────────
    created = await api<PresignedUpload>(`/v1/projects/${projectId}/assets`, {
      method: "POST",
      json: {
        filename: file.name,
        bytes: file.size,
        checksum,
        ...(rate ? { rate } : {}),
      },
    });
  } catch (error) {
    // Everything before the first part goes out used to throw silently: the
    // caller's `onProgress` had only ever heard `hashing`, so the control sat
    // at "Reading the file, 100%" for ever with no message and no way back.
    // Anything that can fail has to report that it did.
    if (signal?.aborted) {
      report({ phase: "cancelled" });
      throw error;
    }
    // A file already uploaded to this project is not a failure — it is this
    // exact file, already here. The API says so with the id in a header, which
    // is the whole reason it sends one.
    if (error instanceof ApiError && error.status === 409) {
      const existing = error.headers?.get("X-Asset-Id");
      if (existing) {
        report({
          phase: "done",
          fraction: 1,
          bytesSent: file.size,
          totalBytes: file.size,
          assetId: existing,
        });
        return existing;
      }
    }
    report({
      phase: "failed",
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }

  // Whatever S3 already holds from a previous attempt. On a first upload this
  // is empty and costs one request; on a resumed one it is the whole point.
  let already: Map<number, string>;
  try {
    const state = await api<UploadState>(`/v1/assets/${created.asset_id}/upload-parts`);
    already = new Map(state.uploaded.map((p) => [p.part_number, p.etag]));
  } catch {
    already = new Map();
  }

  const etags = new Map<number, string>(already);
  let bytesSent = 0;
  for (const part of created.parts) {
    if (etags.has(part.part_number)) bytesSent += part.length;
  }
  let retrying = 0;
  const emit = (phase: UploadPhase = "uploading") =>
    report({
      phase,
      fraction: file.size ? bytesSent / file.size : 1,
      bytesSent,
      totalBytes: file.size,
      partsDone: etags.size,
      totalParts: created.total_parts,
      retrying,
      assetId: created.asset_id,
    });
  emit();

  // ── send what is missing ────────────────────────────────────────────────
  const queue = created.parts.filter((p) => !etags.has(p.part_number));
  let cursor = 0;
  let freshUrls: Map<number, string> | null = null;

  const nextUrl = async (part: PartUrl): Promise<string> => {
    if (!freshUrls) return part.url;
    const url = freshUrls.get(part.part_number);
    if (url) return url;
    // Re-mint in one request for everything still outstanding rather than one
    // request per part: on a 60 GB upload that is one round trip instead of
    // hundreds.
    const outstanding = queue
      .filter((p) => !etags.has(p.part_number))
      .map((p) => p.part_number);
    const refreshed = await api<PresignedUpload>(
      `/v1/assets/${created.asset_id}/upload-urls`,
      { method: "POST", json: { part_numbers: outstanding } }
    );
    freshUrls = new Map(refreshed.parts.map((p) => [p.part_number, p.url]));
    return freshUrls.get(part.part_number) ?? part.url;
  };

  const worker = async () => {
    while (cursor < queue.length) {
      if (signal?.aborted) throw new DOMException("aborted", "AbortError");
      const part = queue[cursor++];
      const blob = file.slice(part.offset, part.offset + part.length);

      for (let attempt = 1; ; attempt++) {
        try {
          const etag = await putPart(await nextUrl(part), blob, signal);
          etags.set(part.part_number, etag);
          bytesSent += part.length;
          emit();
          break;
        } catch (error) {
          if (signal?.aborted) throw error;
          if (attempt >= MAX_ATTEMPTS) throw error;
          // A 403 here is almost always an expired signature rather than a
          // permission problem — the TTL is 900s and this upload is longer
          // than that — so the next attempt asks for new URLs.
          if (error instanceof ApiError && error.status === 403) freshUrls = new Map();
          retrying += 1;
          emit();
          await sleep(Math.min(2 ** attempt * 250, 8000) * (0.5 + Math.random()));
          retrying -= 1;
        }
      }
    }
  };

  try {
    await Promise.all(
      Array.from({ length: Math.min(concurrency, Math.max(queue.length, 1)) }, worker)
    );
  } catch (error) {
    if (signal?.aborted) {
      // Stop paying for the parts that did land. The lifecycle rule would get
      // them in seven days; the user cancelled now.
      await api(`/v1/assets/${created.asset_id}/upload`, { method: "DELETE" }).catch(
        () => undefined
      );
      report({ phase: "cancelled", assetId: created.asset_id });
      throw error;
    }
    report({
      phase: "failed",
      assetId: created.asset_id,
      error: error instanceof Error ? error.message : String(error),
      bytesSent,
      partsDone: etags.size,
      totalParts: created.total_parts,
    });
    throw error;
  }

  // ── complete ────────────────────────────────────────────────────────────
  emit("completing");
  await api(`/v1/assets/${created.asset_id}/complete`, {
    method: "POST",
    json: {
      parts: [...etags.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([part_number, etag]) => ({ part_number, etag })),
    },
  });
  report({
    phase: "done",
    fraction: 1,
    bytesSent: file.size,
    totalBytes: file.size,
    partsDone: etags.size,
    totalParts: created.total_parts,
    assetId: created.asset_id,
  });
  return created.asset_id;
}

/**
 * One part, by XHR rather than fetch.
 *
 * Not nostalgia: `fetch` cannot report upload progress, and on a 64 MiB part
 * over a hotel connection the difference between a bar that moves and one that
 * does not is whether the user believes the page has hung.
 */
function putPart(url: string, blob: Blob, signal?: AbortSignal): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url, true);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        // S3 returns the part's etag in a header, and it is required — quoted —
        // at CompleteMultipartUpload.
        const etag = xhr.getResponseHeader("ETag");
        if (!etag) {
          reject(new ApiError(xhr.status, "S3 did not return an ETag for the part"));
          return;
        }
        resolve(etag);
      } else {
        reject(new ApiError(xhr.status, `part upload failed: ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "the network request failed"));
    xhr.ontimeout = () => reject(new ApiError(0, "the part upload timed out"));
    const abort = () => xhr.abort();
    signal?.addEventListener("abort", abort, { once: true });
    xhr.onloadend = () => signal?.removeEventListener("abort", abort);
    xhr.send(blob);
  });
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
