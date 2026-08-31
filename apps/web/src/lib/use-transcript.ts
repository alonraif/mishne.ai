"use client";

/**
 * A transcript the viewer can rename and merge voices in.
 *
 * Shared by `transcript-viewer` and `cut-editor` because both show the speaker
 * legend and both must behave identically when somebody uses it — two copies of
 * "what happens when a merge fails" is how they stop behaving identically.
 *
 * ## Why the whole transcript comes back
 *
 * A rename touches one label. A **merge changes every beat's speaker id**: the
 * id the UI groups a voice by is derived server-side from the merge rows
 * (`repository._canonical`), so a client that patched its own copy would be a
 * second implementation of that rule, in a language that cannot see the rows it
 * depends on. Both endpoints return the transcript as it now stands and this
 * replaces its copy with it.
 *
 * ## Renaming is optimistic, merging is not
 *
 * Typing a name should feel instant and is trivially reversible if the request
 * fails — the old label comes back and the field is still there. A merge
 * rearranges the whole legend and half the beats; showing that before the
 * server has agreed, and then undoing it, is worse than a moment's wait.
 */

import { useState } from "react";
import { ApiError } from "./api";
import { apiSend } from "./dto";
import type { Speaker, Transcript } from "@mishne/shared";

export interface TranscriptEditing {
  transcript: Transcript;
  rename: (speakerId: string, label: string) => void;
  merge: (canonicalId: string, otherId: string) => void;
  /** Set when the last change did not reach the server. */
  error: string | null;
  merging: boolean;
}

export function useTranscript(
  initial: Transcript,
  jobId?: string
): TranscriptEditing {
  const [transcript, setTranscript] = useState<Transcript>(initial);
  const [error, setError] = useState<string | null>(null);
  const [merging, setMerging] = useState(false);

  const withSpeakers = (speakers: Speaker[]): Transcript => ({
    ...transcript,
    speakers,
  });

  const rename = (speakerId: string, label: string) => {
    const before = transcript.speakers;
    setTranscript(
      withSpeakers(
        before.map((s) =>
          s.id === speakerId ? { ...s, label, confirmed: label.length > 0 } : s
        )
      )
    );
    setError(null);
    if (!jobId) return;

    void apiSend<Transcript>(`/v1/jobs/${jobId}/speakers/${speakerId}`, {
      method: "PATCH",
      json: { label },
    })
      .then(setTranscript)
      .catch((cause) => {
        // Put the old name back. A name that looks saved and is not is the one
        // outcome worth avoiding here — the next person to open this reads it
        // as the truth about who said what.
        setTranscript(withSpeakers(before));
        setError(cause instanceof ApiError ? cause.detail : String(cause));
      });
  };

  const merge = (canonicalId: string, otherId: string) => {
    if (!jobId) return;
    setMerging(true);
    setError(null);
    void apiSend<Transcript>(`/v1/jobs/${jobId}/speakers/merge`, {
      json: { speaker_ids: [canonicalId, otherId] },
    })
      .then(setTranscript)
      .catch((cause) =>
        setError(cause instanceof ApiError ? cause.detail : String(cause))
      )
      .finally(() => setMerging(false));
  };

  return { transcript, rename, merge, error, merging };
}
