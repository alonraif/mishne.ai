# Design instructions

The visual language of mishne.ai, and the rules that keep it consistent.

**[apps/web/src/app/globals.css](../apps/web/src/app/globals.css) is the source of
truth for every value in this document.** Retune the palette there rather than
hardcoding a colour in a component — the whole point is that it can be retuned in
one place. If this file and the tokens disagree, the tokens win, and fix this file.

The product is a professional post-production tool, so the visual language leans
dark, dense and precise — closer to an NLE than to a SaaS dashboard. Dark is the
primary theme. Light is supported and must stay usable.

## The mark

Three bars of decreasing width: three hours of rushes down to a ten-minute cut,
which is the whole product in one glyph. **The mark is settled. Do not redraw it.**

Drawn on a 20×20 grid, bars 18 / 11 / 5 wide, height 3, `rx` 1.5, at rows y 3 /
8.5 / 14.

| Bar | Fill |
|---|---|
| First | `--primary` at 100% |
| Second | `--primary` at 60% |
| Third | `--primary` at 35% |

The opacity ladder carries the meaning. Three bars at one opacity read as a
hamburger menu.

**It lives twice.** [`components/logo.tsx`](../apps/web/src/components/logo.tsx)
takes its colour from the `--primary` token.
[`app/icon.svg`](../apps/web/src/app/icon.svg) renders outside the document and
cannot read the tokens, so it carries literal `#7183f5` on a filled ground with
white bars at 100 / 72 / 45%. **Change both files in the same commit.**

### Wordmark and lockup

Lowercase always, in `--font-sans`, semibold, tracking `-0.02em`. The suffix
`.ai` takes `--muted-foreground`: it is part of the name, not part of the
emphasis.

Mark and wordmark scale together. The glyph is `1.25em` and the gap `0.5em`, so a
caller's `text-lg` grows both and the lockup keeps its proportions. Never set the
glyph in pixels next to em-sized text.

- **Clearspace** on all four sides equals one bar height — `0.1875em` of the
  lockup's font size, rounded up to the nearest 4px step in layout.
- **Minimum size** is 13px for the lockup, 16px for the mark alone. Below 16px
  the third bar stops reading as a bar; use the filled-ground icon instead.
- **Never** rotate, stretch, reweight, uppercase, recolour the bars
  independently, place the lockup on a gradient, or break the em-locked gap.

## Core palette

Every colour is authored in oklch and named. Neutrals sit at hue 285 with chroma
at or under 0.01; brand sits at 274. Nothing else in the chrome gets a hue of its
own. Softer states come from the same token at 15 / 25 / 40 / 60% — a new hex in a
component is the bug.

| Token | Dark | Light |
|---|---|---|
| `--background` | `0.165 0.006 285` | `0.99 0.002 285` |
| `--foreground` | `0.95 0.003 285` | `0.18 0.008 285` |
| `--card` | `0.202 0.007 285` | `1 0 0` |
| `--popover` | `0.222 0.008 285` | `1 0 0` |
| `--primary` | `0.65 0.17 274` | `0.52 0.185 274` |
| `--primary-foreground` | `0.14 0.01 285` | `0.99 0.002 285` |
| `--secondary` / `--muted` | `0.25 0.008 285` | `0.96 0.004 285` |
| `--muted-foreground` | `0.66 0.01 285` | `0.53 0.012 285` |
| `--accent` | `0.30 0.035 274` | `0.95 0.012 274` |
| `--destructive` | `0.62 0.20 25` | `0.58 0.21 27` |
| `--border` | `0.28 0.008 285` | `0.91 0.004 285` |
| `--input` | `0.30 0.008 285` | `0.91 0.004 285` |

Indigo-violet, not blue: distinctive without being loud. It lightens in dark
theme (0.52 → 0.65) and `--primary-foreground` goes near-black with it, because a
light indigo needs dark text on it.

## Domain semantics

The colours that carry meaning rather than mood. They answer three questions an
editor asks constantly: did this make the cut, why was it flagged, and where is
the job.

### Used vs not used

| Token | Dark | Where |
|---|---|---|
| `--used` | `0.68 0.15 155` | Markers, checks, cut duration |
| `--used-surface` | `0.28 0.05 155` | Beat row ground, at 25% |
| `--used-foreground` | `0.86 0.09 155` | Text on a used surface |
| `--unused-foreground` | `0.50 0.008 285` | Transcript that did not make it |

Green means *in the cut*, never *success*. Unused material is dimmed, never
hidden — the transcript's job is to show what was left behind.

### Beat flags

| Token | Dark | Flags |
|---|---|---|
| `--flag-filler` | `0.70 0.10 60` | `filler`, `false_start` |
| `--flag-retake` | `0.68 0.13 300` | `retake`, `crosstalk` |
| `--flag-lowconf` | `0.70 0.15 40` | `low_confidence`, `off_mic` |

A flag is an observation, not a verdict: outline badge, coloured text, border at
35%, no filled ground. A flagged beat can still be in the cut.

### Job stage states

| Token | Dark | State |
|---|---|---|
| `--stage-pending` | `0.42 0.008 285` | Not started |
| `--stage-active` | `0.76 0.15 70` | Running, awaiting approval |
| `--stage-done` | `0.68 0.15 155` | Complete |
| `--stage-failed` | `0.65 0.19 25` | Failed |

Amber is the only colour that moves. Anything animated is in flight; anything
static is settled.

### Speaker colours

A fixed five-colour cycle so the same voice reads the same everywhere, aliased
from existing tokens (`SPEAKER_COLORS` in
[`speaker-legend.tsx`](../apps/web/src/components/speaker-legend.tsx)):
`--primary`, `--used`, `--flag-retake`, `--flag-filler`, `--flag-lowconf`. No
sixth colour is invented for a sixth speaker; the cycle repeats. An unattributed
voice gets no dot and italic muted text — never a guess dressed up as a fact.

## Typography

Two stacks, both system. No webfont is loaded: the product must paint instantly on
a machine already busy rendering video, and the native UI face is what an editor's
other tools use.

- `--font-sans` — all interface text and all prose. Weights 400, 500 and 600
  only. Never 700.
- `--font-mono` — timecode, durations, credits, scores, filenames, IDs, token
  names. Anything a person compares column to column.

| Role | Size / line / tracking |
|---|---|
| Display | 68 / 1.02 / `-0.035em` / 600 |
| h2 | 34 / 1.15 / `-0.025em` / 600 |
| Lead | 17 / 1.6, `--muted-foreground` |
| Card title | 15 / leading-none / 600 |
| Body | 14 — buttons, nav, labels, transcript lines |
| Small | 12 — secondary metadata, card descriptions, badges |
| Micro | 11 / 10 — gutter timecode, reel name, flag badge. The floor. |

Radius is one token, `--radius: 0.5rem`, with derived steps at 4, 6, 8 and 12px.
Buttons and inputs take `md`; cards and panels take `lg`. Nothing is fully rounded
except status dots and avatars.

## Timecode

The one piece of typography with rules of its own. It is what an editor is
scanning for, and the `.tc` class is load-bearing, not decoration:
`--font-mono`, `tabular-nums`, tracking `-0.01em`, `direction: ltr`,
`unicode-bidi: isolate`, `display: inline-block`. Colour is `--timecode`.

- **Never reflows** — tabular figures, so digits change without the row moving.
- **Never reorders** — the bidi isolation is the important half. `10:02:14:00` in
  an RTL paragraph reorders around its colons without it.
- **Rational, never float** — frames and a rate, never `23.976`. Drop-frame is a
  display convention only; never do arithmetic on drop-frame strings.
- **Per reel, never per job** — a timecode is only meaningful against its own
  reel. Show the reel when a cut has more than one.

## Iconography

Lucide, 16px, currentColor. One set, no exceptions, no custom glyphs beyond the
brand mark. An icon never appears without a label unless the control is a
single-purpose button with an `aria-label`.

Icon colour follows the state token, not the icon: a wallet in a low-credit meter
is `--flag-lowconf`; the same wallet at a healthy balance is
`--muted-foreground`.

In use: `film`, `clock`, `file-video`, `file-audio`, `layers`, `folder-open`,
`upload`, `mic`, `waves`, `sparkles`, `filter`, `quote`, `check`,
`circle-dashed`, `triangle-alert`, `rotate-ccw`, `wallet`, `shield-check`,
`loader-2`, `log-out`, `plus`, `arrow-left`, `arrow-right`, `pencil`, `link-2`,
`x`, `trash-2`, `mail`.

## Components

Primitives follow shadcn/ui conventions and are copied in, fully editable, in
`apps/web/src/components/ui/`. App-specific components sit one level up in
`apps/web/src/components/`.

**Buttons** are 36px tall, radius 6, 14px medium, gap 8, with a 150ms
background-colour transition; `sm` is 32px / 12px, `icon` is 36×36. Variants:
`default`, `destructive`, `outline`, `secondary`, `ghost`, `link`. Disabled is
50% opacity with pointer events off.

**Badges** are radius 6, `2px 8px`, 12px medium. Variants: `default`,
`secondary`, `outline`, `used`, `muted`, `destructive`. A running job's status
badge carries a pinging dot and a settled one does not — the dot is the whole
difference, because the label alone will not tell a queue apart from a finished
list at a glance.

**Header chrome** is sticky, 56px tall, over a 1400px content column with 24px
gutters. The ground is `--background` at 85% with a blur behind it, so material
scrolls under rather than out of sight.

**Job stages** are a 22px-node vertical list with a 1px connector that takes
`--stage-done` at 40% behind completed steps and `--border` otherwise. The active
step is medium weight; pending steps are muted.

**Transcript beats** are the product's signature surface: four fixed columns —
timecode gutter at 92px, used marker at 20px, body, score at 40px. The gutter
never moves, so a person can scan a page of beats down one edge. A used row takes
`--used-surface` at 25% with a `--used` border at 25%; an unused row is
transparent with a border on hover only.

## Motion

Motion is reserved for state, never for delight. If something is moving, work is
happening.

| Animation | Timing | Meaning |
|---|---|---|
| Spin | 1s linear, infinite | A stage is running. Only ever on `loader-2`. |
| Ping | 1s ease-out, infinite, 70% | A job is in flight. Status badges only. |
| Pulse | 2s, infinite | Skeleton while a query is in flight. |
| Colour | 150ms | The only transition on interactive chrome. |

No transforms, no scale-on-hover, no easing curves worth naming. **Nothing
animates a layout** — a dense tool that reflows while a person is reading a
timecode is worse than one that does not move at all. A skeleton is the shape of
the thing that is coming, never a spinner over a whole page.

## Voice and tone

We write to a working editor who knows their craft better than we do. Plain,
exact, unhurried. State what the thing does and what it costs. Claim less than the
product delivers.

**The one-liner.** Upload raw footage or an AAF sequence, describe the piece you
want, get back an editable rough cut — AAF, FCPXML, EDL — plus a transcript
showing exactly what was used and why.

**The limit we lead with.** mishne.ai does not produce a fine cut. It removes the
heaviest lift in post: getting from three hours of raw material down to the ten
minutes that will actually make the cut. The editor takes it from there. Say this
early and say it unprompted — naming the boundary is what makes the claim credible
to the person who would otherwise assume we are overselling.

- **Explainable, not magic.** The AI never touches pixels. Decisions are made on
  text and emitted as a timeline that references the original media by timecode.
  Every claim about quality comes with the transcript that proves it.
- **Craft words, not SaaS words.** Rushes, reel, beat, span, handles, relink,
  drop-frame. Not assets, content, workflows, solutions, seamless, effortless.
- **Admit uncertainty.** Where the system does not know, the copy says so —
  "Mic 2", not a guessed name. A shortcoming a person can see beats a confident
  error they cannot.

| Write this | Not this |
|---|---|
| Ready to edit | Your cut is ready! 🎬 |
| Not selected. Score fell below the threshold for the target duration. | Our AI didn't think this bit was interesting enough. |
| Held by in-flight jobs | Reserved funds |
| Media is missing for four clips. You can submit anyway. | Oops — something went wrong. |
| View only — ask an owner for upload access. | You don't have permission to do that. |

No emoji, anywhere. No exclamation marks. Sentence case in every heading, label
and button. British spelling in prose, American in code identifiers, never mixed
inside one sentence.

## Five things that are always true

1. **A colour comes from a token.** If the value you want does not exist, add it
   to `globals.css` and name it after what it means, not what it looks like.
2. **Anything numeric a person compares is mono and tabular.**
3. **Every screen works in RTL.** Logical properties, `text-start` not
   `text-left`, and bidi isolation on every number and filename.
4. **Density over comfort.** This is a tool used for hours at a stretch by
   someone who wants more on screen, not less.
5. **No customer content anywhere it does not belong** — not in a log, not in a
   screenshot, not in a marketing example. IDs, durations, counts and status only.
