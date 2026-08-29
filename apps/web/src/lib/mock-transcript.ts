/**
 * Transcript fixture for the transcript page and the cut editor.
 * 25 fps, source TC from 10:00:00:00.
 *
 * A representative excerpt: a real 3-hour interview produces several hundred
 * beats and the page virtualizes them. These 26 exercise every state — used,
 * unused, each flag, each speaker, high and low scores — which is what the
 * design needs.
 *
 * Beat durations are derived from word count rather than hand-written, so they
 * stay plausible against the text.
 */

import {
  RATE_25,
  type Beat,
  type Speaker,
  type SpeakerAttribution,
  type Transcript,
} from "@mishne/shared";

const F = 25;
const tc = (h: number, m: number, s: number) => (h * 3600 + m * 60 + s) * F;

type Seed = [
  start: number,
  speaker: string,
  text: string,
  used: boolean,
  score: number,
  flags: Beat["flags"],
  rationale?: string,
];

/**
 * Speakers as the pipeline actually produces them: attributed automatically,
 * named by a person.
 *
 * Deliberately a mixed state — two named and confirmed, one still carrying the
 * microphone it came from. A mock where every speaker already has a name would
 * imply the system works names out on its own, which it does not and cannot.
 */
const SPEAKERS: Speaker[] = [
  { id: "T1", source: "track", defaultLabel: "Mic 1", label: "Margret Olsen",
    confirmed: true, trackIndex: 1, wordCount: 412, speechMs: 214_000 },
  { id: "T2", source: "track", defaultLabel: "Mic 2", label: "Jonas Berg",
    confirmed: true, trackIndex: 2, wordCount: 268, speechMs: 151_000 },
  { id: "T3", source: "track", defaultLabel: "Mic 3", label: "",
    confirmed: false, trackIndex: 3, wordCount: 47, speechMs: 22_000 },
];

const ATTRIBUTION: SpeakerAttribution = {
  speakers: SPEAKERS,
  crosstalkWords: 38,
  unattributedWords: 4,
  reliable: true,
  notes: [
    "38 words (5%) had two mics at similar levels — attributed to the louder one.",
  ],
};

const SEEDS: Seed[] = [
  [tc(10, 2, 14), "T3", "Just tell me when you're comfortable and we'll start whenever you like.", false, 4, [], undefined],
  [tc(10, 2, 33), "T1", "Um, yeah. Yeah, I'm — I'm fine. Go ahead.", false, 3, ["filler", "false_start"], undefined],
  [tc(10, 3, 2), "T1", "My father kept his boat in the east basin for forty-one years. The Sigrún. She's still there, tied up, and she hasn't been out since March.", false, 88, [], "Strong delivery, but superseded — the subject gave the same line again at 10:03:43 with the corrected figure. Same redundancy cluster; only one member can be selected."],
  [tc(10, 3, 26), "T1", "Sorry, can I say that again? I want to get the years right.", false, 2, ["false_start"], undefined],
  [tc(10, 3, 43), "T1", "My father kept his boat in the east basin for forty-three years. The Sigrún. She hasn't left the harbour since March, and she won't again.", true, 96, ["retake"], "Later take of the same line, higher confidence and the corrected figure. Preferred over the earlier delivery."],
  [tc(10, 5, 12), "T1", "People keep saying the harbour is closing. It isn't closing. It's being closed. There's a difference and everybody here knows exactly what it is.", true, 98, [], "The strongest line in the interview. Quotable, sharp, and it frames the closure as a decision rather than an event — which is the angle the notes asked for."],
  [tc(10, 6, 30), "T1", "The dredging costs came in at, I think it was, four point two million? Something like that. And that was the number that did it.", false, 41, ["low_confidence"], undefined],
  [tc(10, 9, 5), "T2", "I've been harbour master for nineteen years. I have signed off on every vessel that's come through that gate. And in February I signed the notice that says they can't.", true, 92, [], "Jonas's credential and the turn in one beat. Works as the second voice without needing separate setup."],
  [tc(10, 11, 18), "T2", "The quota system changed in twenty-three, and then again in twenty-five, and the tonnage thresholds moved with it, so what you had was a fleet that was compliant on Monday and non-compliant on Tuesday without anybody doing anything differently.", false, 58, [], undefined],
  [tc(10, 14, 2), "T2", "No, I don't blame the council. I blame the arithmetic. The council just read it out.", true, 89, [], "Gives the piece a second register — resigned rather than angry. Balances Margret's edge."],
  [tc(10, 18, 44), "T1", "There were sixty-two boats working out of here when I took over from my father. Sixty-two. There are nine.", true, 95, [], "The scale of the decline in a single comparison. Numbers the viewer can hold."],
  [tc(10, 21, 10), "T3", "And what happens to the nine?", true, 71, [], "Short interviewer question retained because the answer that follows depends on it."],
  [tc(10, 22, 0), "T1", "Two are going to Þórshöfn. Three are being sold south. The rest of us are, well. We're waiting to see what the compensation looks like, and nobody will tell us.", true, 91, [], "Direct answer to the retained question. Concrete outcomes, and the unresolved ending gives the section somewhere to go."],
  [tc(10, 26, 15), "T1", "I mean the compensation, sorry, the — the transition package, that's what they call it. The transition package.", false, 34, ["false_start", "filler"], undefined],
  [tc(10, 31, 40), "T2", "You can measure a harbour by the ice plant. If the ice plant runs, the harbour is alive. Ours stopped in April and nobody has asked me to start it again.", true, 93, [], "Vivid, specific detail that does the emotional work without stating it. Strong candidate for the closing section."],
  [tc(10, 35, 12), "T2", "The council vote was on the fourteenth and it went through eleven to two.", false, 12, [], undefined],
  [tc(10, 38, 50), "T1", "My daughter asked me last week whether she should learn the boat. And I didn't have an answer for her. That's the first time that's happened.", true, 97, [], "Emotional peak. Personal, forward-looking, and it lands the consequence on the next generation."],
  [tc(10, 44, 20), "T1", "Would I do it again? Yes. Obviously yes. That's not — that was never the question.", true, 88, [], "Natural closing beat. Resolves without resolving, which suits the piece."],
  [tc(10, 52, 30), "T2", "There's a lot of paperwork involved in closing something. More than opening it, I'd say. Much more.", false, 52, [], undefined],
  [tc(11, 4, 10), "T1", "The east basin freezes first. Always has. My father used to say you could set your calendar by it.", false, 64, [], undefined],
  [tc(11, 12, 44), "T2", "I'll be the last one out. Somebody has to lock it.", true, 90, [], "Final line of the cut. Short, definitive, and it closes the harbour master's arc."],
  [tc(11, 20, 0), "T3", "Is there anything you want to add that I haven't asked about?", false, 8, [], undefined],
  [tc(11, 20, 50), "T1", "No. No, I think that's — I think that's it, really.", false, 11, ["filler"], undefined],
  [tc(11, 34, 12), "T1", "[overlapping] — well no, but that's exactly what I — sorry, go on.", false, 6, ["crosstalk", "false_start"], undefined],
  [tc(11, 48, 30), "T2", "The gate itself is from nineteen sixty-eight. It still works. Everything here still works, that's the thing.", false, 69, [], undefined],
  [tc(12, 2, 15), "T1", "[off mic] You want me to say that bit about the ice again?", false, 5, ["off_mic"], undefined],
];

/**
 * Conversational interview delivery sits around 2.6 words per second. Deriving
 * beat length from the text keeps durations plausible instead of arbitrary.
 */
const WORDS_PER_SECOND = 2.6;
const MIN_BEAT_SECONDS = 1.6;

function durationFrames(text: string): number {
  const words = text.trim().split(/\s+/).length;
  return Math.round(Math.max(MIN_BEAT_SECONDS, words / WORDS_PER_SECOND) * F);
}

const beats: Beat[] = SEEDS.map(([start, speaker, text, used, score, flags, rationale], i) => ({
  id: `beat_${String(i + 1).padStart(3, "0")}`,
  idx: i,
  speaker,
  startFrames: start,
  endFrames: start + durationFrames(text),
  text,
  flags,
  used,
  score,
  rationale,
}));

// Assign cut order to the used beats, in source order.
let order = 0;
for (const b of beats) if (b.used) b.orderIdx = order++;

const cutDurationFrames = beats
  .filter((b) => b.used)
  .reduce((a, b) => a + (b.endFrames - b.startFrames), 0);

export const mockTranscript: Transcript = {
  jobId: "job_8f23",
  assetId: "ast_9d41",
  language: "en",
  rate: RATE_25,
  dropFrame: false,
  speakers: SPEAKERS,
  attribution: ATTRIBUTION,
  beats,
  sourceDurationFrames: 267_750,
  cutDurationFrames,
};
