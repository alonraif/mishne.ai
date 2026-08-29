# llm — provider-agnostic model access

Set one or more keys. Everything else has a default.

```bash
export ANTHROPIC_API_KEY=...     # any one of these is enough
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...
export XAI_API_KEY=...
```

Keys come from the environment and never from a file. With none set the
pipeline still runs — deterministically, with the control scorer and enumerated
spans — and says so.

## Choosing

```bash
python run.py rushes.mov --policy cost        # cheapest that clears each floor
python run.py rushes.mov --policy balanced    # default
python run.py rushes.mov --policy quality     # best tier available
```

Per task, when one stage deserves different treatment:

```bash
MISHNE_POLICY_SPANS=quality MISHNE_POLICY_SCORE=cost python run.py rushes.mov
MISHNE_MODEL_SPANS=anthropic/claude-opus-5 python run.py rushes.mov   # pin it
```

Measured on a 26-minute interview (42 calls: 1 brief, 35 span proposals, 6
scoring chunks):

| policy | model spend |
|---|---|
| quality | $0.50 |
| balanced | $0.50 |
| cost | $0.24 |

Model spend is a small part of a job. Transcription and compute dominate.

## The three tasks are not alike

| task | floor | why |
|---|---|---|
| `brief` | fast | Parses a sentence into a duration and a shape. Valid JSON is the whole requirement. |
| `spans` | mid | Decides which span of a long answer is a coherent thought, without straying off the legal cut points. Judgement plus obedience. |
| `score` | mid | Scores beats with enough spread for the solver to work with. |

`balanced` will not downgrade a task below what it asked for. To spend less,
ask for `cost` and mean it.

## Adding a model

`models.json` is data — replace it, or point `MISHNE_MODEL_CATALOG` elsewhere.
No release needed.

A model that is not in the catalog still runs. Its cost is recorded as
*unknown*, never as zero, so a missing price cannot read as free and cannot win
a cost-policy decision.

**The prices in `models.json` were verified on 2026-08-29 and will go stale.**
Every model identifier and price in this project's training-era memory was
already wrong when it was written; assume the same of anything here more than a
few weeks old.

## What is recorded

Every call: cost, latency, whether the response parsed, and — for span
proposal — how many proposals the silence gate refused. That last one is the
only quality signal available without an editor's own EDL to compare against:
it measures whether a model can follow a hard constraint, with no corpus and no
opinion.

It is reported per job and does **not** yet steer routing. Acting on it needs a
validation corpus; without one the router would demote a good model on four
unlucky calls. See ADR-0011.
