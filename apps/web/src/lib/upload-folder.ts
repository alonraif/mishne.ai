/**
 * A folder of linked media, uploaded file by file.
 *
 * "Export AAF with linked media" is what Media Composer does by default: a few
 * hundred kilobytes of pointers plus an `AAF Media/` folder beside it. The
 * sequence uploads in a second; the folder is four files and a gigabyte, or
 * seven hundred and seventy-five files and two. The uploader this wraps handles
 * one file at a time and handles it well — resumable, content-addressed,
 * straight to S3 — so this adds the only thing missing: a queue, and the
 * judgement about which files to put in it.
 *
 * ## Only what the sequence asked for
 *
 * The AAF is probed on arrival and `asset_media_requirements` records one row
 * per file it references and does not contain (ADR-0014). Handing this that
 * list means a folder with a year of rushes in it uploads the four files the
 * cut needs, not the year. It also means the customer can drop the whole export
 * folder — AAF and media together — and the AAF they already uploaded is not
 * uploaded again.
 *
 * ## Two files at a time, not four, and not one
 *
 * Each file is hashed in the browser before a byte is sent, and a 265 MB WAV
 * takes seconds to read. One at a time means the link goes idle during every
 * hash. Two means one file's hashing overlaps the other's sending, and each
 * keeps `uploadAsset`'s own four parts in flight — eight concurrent PUTs, which
 * saturates a link without starving the tab.
 *
 * ## The frame rate is not a question worth asking
 *
 * A WAV carries no frame rate and the API requires one for audio (ADR-0005),
 * which for 775 companions would be 775 identical answers. The sequence knows:
 * the rate is on the AAF asset, and it is the rate these files are cut at by
 * definition. It is passed, shown, and overridable — not asked.
 *
 * Wire types here are snake_case and deliberately not routed through
 * `lib/dto.ts`, matching `upload.ts`, which this builds on.
 */

import { isAudioFile, uploadAsset, type UploadProgress } from "./upload";

/** Files that are on disk and are not media. Dropping a folder collects them. */
const JUNK = [
  ".ds_store",      // macOS, in every folder that has ever been opened
  "thumbs.db",      // the Windows equivalent
  "desktop.ini",
  ".localized",
];

function isJunk(name: string): boolean {
  const lower = name.toLowerCase();
  // `._Foo.wav` is an AppleDouble sidecar: the resource fork of the file next
  // to it, a few kilobytes, and not the media whatever its extension says.
  return JUNK.includes(lower) || lower.startsWith("._") || lower.startsWith(".");
}

/** Case- and directory-insensitive, exactly as the API's `match_key_for` is. */
export function matchKey(name: string): string {
  return name.replace(/\\/g, "/").split("/").pop()!.trim().toLowerCase();
}

export interface QueuedFile {
  /** Stable within one queue: the basename, which is what is being matched on. */
  key: string;
  name: string;
  bytes: number;
  file: File;
  progress: UploadProgress | null;
  assetId?: string;
}

export interface FolderPlan {
  /** In the order they should be uploaded. */
  queue: QueuedFile[];
  /** Files dropped because no clip references them. A count, to be honest about. */
  unreferenced: number;
  /** `.DS_Store` and friends. Not worth mentioning to anybody. */
  junk: number;
  /** Wanted basenames the folder does not contain, lowercased. */
  stillMissing: string[];
}

/**
 * Decide what to upload from what was dropped.
 *
 * `wanted` is the outstanding basenames from `GET /assets/{id}/requirements`.
 * An empty `wanted` means nothing is known to be wanted — an ordinary media
 * folder rather than a sequence's companions — and everything that is not junk
 * goes in the queue.
 */
export function planFolder(files: File[], wanted: string[]): FolderPlan {
  const want = new Set(wanted.map(matchKey));
  const queue: QueuedFile[] = [];
  const seen = new Set<string>();
  let unreferenced = 0;
  let junk = 0;

  for (const file of files) {
    const key = matchKey(file.name);
    if (isJunk(file.name)) {
      junk += 1;
      continue;
    }
    if (want.size > 0 && !want.has(key)) {
      unreferenced += 1;
      continue;
    }
    // A folder tree can hold the same basename twice. The requirement is keyed
    // on the basename and cannot tell them apart, so neither can we: take the
    // first and count the rest as unreferenced rather than uploading both and
    // letting the last one win silently.
    if (seen.has(key)) {
      unreferenced += 1;
      continue;
    }
    seen.add(key);
    queue.push({ key, name: file.name, bytes: file.size, file, progress: null });
  }

  // Biggest last: the small files finish first and the requirement list starts
  // ticking off within seconds, which is the difference between a queue that
  // looks alive and one that looks stuck.
  queue.sort((a, b) => a.bytes - b.bytes);

  return {
    queue,
    unreferenced,
    junk,
    stillMissing: [...want].filter((k) => !seen.has(k)),
  };
}

export interface FolderUploadOptions {
  projectId: string;
  queue: QueuedFile[];
  /** Applied to every audio file. The sequence's rate — see the note above. */
  rate?: { num: number; den: number };
  /** Files in flight. Two, unless you know why you want otherwise. */
  concurrency?: number;
  onFileProgress?: (key: string, progress: UploadProgress) => void;
  onFileDone?: (key: string, assetId: string) => void;
  onFileFailed?: (key: string, error: string) => void;
  signal?: AbortSignal;
}

/**
 * Upload the queue. Resolves when every file has finished or failed.
 *
 * One file failing does not stop the others: on a folder of 775, stopping at
 * the first failure would mean the customer re-drops the folder and waits for
 * everything that already worked to be re-hashed. Failures are reported per
 * file and the caller offers a retry of just those.
 */
export async function uploadFolder(
  options: FolderUploadOptions
): Promise<{ done: string[]; failed: string[] }> {
  const { projectId, queue, rate, concurrency = 2, signal } = options;
  const done: string[] = [];
  const failed: string[] = [];
  let cursor = 0;

  const worker = async () => {
    while (cursor < queue.length) {
      if (signal?.aborted) return;
      const item = queue[cursor++];
      try {
        const assetId = await uploadAsset({
          projectId,
          file: item.file,
          rate: isAudioFile(item.name) ? rate : undefined,
          signal,
          onProgress: (p) => options.onFileProgress?.(item.key, p),
        });
        done.push(item.key);
        options.onFileDone?.(item.key, assetId);
      } catch (error) {
        if (signal?.aborted) return;
        failed.push(item.key);
        options.onFileFailed?.(
          item.key,
          error instanceof Error ? error.message : String(error)
        );
      }
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(concurrency, Math.max(queue.length, 1)) }, worker)
  );
  return { done, failed };
}

/**
 * Every file under what was dropped, subdirectories included.
 *
 * A drop gives entries, not files, and a dropped *folder* gives one directory
 * entry whose contents have to be read asynchronously. Without this, dropping
 * `AAF Media/` yields nothing at all — and dropping the export folder, which is
 * the natural thing to do because it holds the AAF too, yields nothing twice
 * over.
 *
 * `webkitGetAsEntry` is prefixed and universally implemented; the standard
 * `getAsFileSystemHandle` is not, and this needs no write access.
 */
export async function filesFromDrop(transfer: DataTransfer): Promise<File[]> {
  const entries = [...transfer.items]
    .map((item) => item.webkitGetAsEntry?.())
    .filter((entry): entry is FileSystemEntry => Boolean(entry));

  if (entries.length === 0) return [...transfer.files];

  const out: File[] = [];
  const walk = async (entry: FileSystemEntry): Promise<void> => {
    if (entry.isFile) {
      out.push(
        await new Promise<File>((resolve, reject) =>
          (entry as FileSystemFileEntry).file(resolve, reject)
        )
      );
      return;
    }
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    // readEntries returns a page at a time — 100 in Chromium — and an empty
    // array means the end. Reading once gives the first hundred of 775 files
    // and no error, which is the kind of bug that only shows up on a real job.
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
        reader.readEntries(resolve, reject)
      );
      if (batch.length === 0) break;
      for (const child of batch) await walk(child);
    }
  };

  for (const entry of entries) await walk(entry);
  return out;
}
