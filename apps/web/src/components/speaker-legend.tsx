"use client";

import { useState } from "react";
import { Check, Mic, Pencil, TriangleAlert, Waves } from "lucide-react";
import type { Speaker, SpeakerAttribution } from "@mishne/shared";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Stable colour per speaker, so the same voice reads the same everywhere. */
export const SPEAKER_COLORS = [
  "var(--primary)",
  "var(--used)",
  "var(--flag-retake)",
  "var(--flag-filler)",
  "var(--flag-lowconf)",
];

export function speakerColor(index: number) {
  return SPEAKER_COLORS[index % SPEAKER_COLORS.length];
}

function talkTime(ms: number) {
  const m = Math.floor(ms / 60000);
  const s = Math.round((ms % 60000) / 1000);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/**
 * Speaker legend with editable names.
 *
 * The pipeline can work out *how many* voices there are and which is which. It
 * cannot know their names — so this is where a person supplies them, and the
 * `confirmed` flag records that they did. Until then the row shows what was
 * actually detected ("Mic 2"), never a guess dressed up as a fact.
 */
export function SpeakerLegend({
  speakers,
  attribution,
  onRename,
  className,
}: {
  speakers: Speaker[];
  attribution: SpeakerAttribution;
  onRename: (id: string, label: string) => void;
  className?: string;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const unnamed = speakers.filter((s) => !s.confirmed).length;

  const commit = (id: string) => {
    onRename(id, draft.trim());
    setEditing(null);
  };

  return (
    <Card className={cn("p-4", className)}>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium">Speakers</h2>
        {unnamed > 0 && (
          <span className="text-xs text-muted-foreground">
            {unnamed} still unnamed
          </span>
        )}
      </div>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {speakers.map((sp, i) => {
          const isEditing = editing === sp.id;
          return (
            <div
              key={sp.id}
              className={cn(
                "flex items-center gap-2.5 rounded-md border p-2.5",
                sp.confirmed ? "border-border" : "border-dashed border-border"
              )}
            >
              <span
                className="size-2.5 shrink-0 rounded-full"
                style={{ background: speakerColor(i) }}
              />

              <div className="min-w-0 flex-1">
                {isEditing ? (
                  <Input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => commit(sp.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commit(sp.id);
                      if (e.key === "Escape") setEditing(null);
                    }}
                    placeholder={sp.defaultLabel}
                    className="h-7 text-sm"
                  />
                ) : (
                  <button
                    onClick={() => {
                      setDraft(sp.label);
                      setEditing(sp.id);
                    }}
                    className="group flex w-full items-center gap-1.5 text-left"
                  >
                    <span
                      dir="auto"
                      className={cn(
                        "truncate text-sm",
                        sp.confirmed
                          ? "font-medium"
                          : "italic text-muted-foreground"
                      )}
                    >
                      {sp.label || sp.defaultLabel}
                    </span>
                    <Pencil className="size-3 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                  </button>
                )}

                <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  {sp.source === "track" ? (
                    <>
                      <Mic className="size-2.5" />
                      {sp.defaultLabel}
                    </>
                  ) : (
                    <>
                      <Waves className="size-2.5" />
                      detected
                    </>
                  )}
                  <span>·</span>
                  <span className="tc">{talkTime(sp.speechMs)}</span>
                </div>
              </div>

              {sp.confirmed ? (
                <Check className="size-3.5 shrink-0 text-used" />
              ) : (
                <Badge variant="muted" className="shrink-0 text-[10px]">
                  name it
                </Badge>
              )}
            </div>
          );
        })}
      </div>

      {!attribution.reliable && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-flag-lowconf/35 bg-flag-lowconf/5 p-2.5 text-xs">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-flag-lowconf" />
          <div>
            <p className="font-medium text-flag-lowconf">
              Speaker attribution may be unreliable
            </p>
            {attribution.notes.map((n) => (
              <p key={n} className="mt-1 text-muted-foreground">{n}</p>
            ))}
          </div>
        </div>
      )}

      {attribution.reliable && attribution.notes.length > 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          {attribution.notes[0]}
        </p>
      )}
    </Card>
  );
}
