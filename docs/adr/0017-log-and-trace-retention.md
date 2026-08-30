# ADR-0017 — Logs and traces are retained on a different clock from the audit log

*Status: accepted. 30 Aug 2026, workstream C3.*

## Context

`docs/architecture/04-security.md` sets retention for three things: raw media
and derived audio for 30 days after a job completes, artifacts and transcripts
for a year, the audit log for three. It says nothing about operational logs and
traces, which did not exist as a system when it was written.

They need their own answer, because they are governed by a different fact about
themselves: **they contain no customer content, by construction and now by
test.** `logging.scrub` blocks by exact key and by suffix, walks nested
structures, and `telemetry._Span.set` runs span attributes through the same
predicate rather than around it (`tests/test_telemetry.py`).

That changes what the retention question even is. For media, retention is a
privacy and liability decision — how long do we hold footage that could end a
customer's embargo. For logs it is an operations and cost decision: how far back
do you need to look to answer "when did this start", and what does keeping that
cost.

Conflating the two produces the wrong answer in both directions. Treating logs
as sensitive means throwing away the history you need to diagnose a regression.
Treating the audit log as an operational log means the record that footage was
accessed expires before the footage does — which inverts the one rule 04 is
explicit about: *the record that footage was accessed must outlive the footage.*

## Decision

Four clocks, not one.

| | Retained | Why that number |
|---|---|---|
| **Operational logs** | 90 days | Long enough to answer "did this change with the release three sprints ago". Short enough to be cheap. |
| **Traces** | 30 days | A trace is diagnostic, not historical; the questions it answers are asked within days. The aggregates that outlive it are metrics. |
| **Cost and step records** (`job_steps`, `job_llm_calls`) | The life of the job | They are part of the job, not telemetry about it. C1 prices from them and a customer may query a charge; they are deleted when the job is, and what deleting a job means is C4's. |
| **Audit log** | 3 years | Unchanged from 04. Append-only, and outlives the media by design. |

**Access follows the same split.** Operational logs and traces are operator-only
— there is nothing in them a customer could want, precisely because the content
rule holds. The audit log is readable by a customer's `owner` role for their own
org, because "who in my organisation downloaded this cut" is a question they are
entitled to ask and one a broadcast buyer's security review will ask on their
behalf.

**Sampling is a setting, not a constant.** `otel_sample_ratio` defaults to 1.0.
Job volume is low enough that 100% is affordable today and will not stay that
way, and the day it stops being affordable should be a config change rather than
a release.

## What is decided here and what is not

Decided: the four clocks, the access split, and that sampling is configurable.

**Not decided, and deliberately: the vendor.** Anything OTel-compatible.
`telemetry.py` names no product; the exporter is `otlp` against an endpoint from
settings, or `console`, or `none`. The cost of choosing late is an environment
variable and the cost of not instrumenting compounds weekly, so the
instrumentation shipped and the choice did not.

## Consequences

**The enforcement is infrastructure that does not exist yet.** There is no
Terraform, no AWS account, and no deployment of any kind (see `docs/HANDOVER.md`).
A retention period is a log group setting and a backend's retention policy, so
what this ADR ships is the decision and the numbers, to be applied when there is
something to apply them to. Writing it down now is cheap; reconstructing why 90
days was chosen after somebody has been paying for 400 is not.

**The content rule is load-bearing for all of it.** Every number above is
justified by "logs contain no customer content". If that stops being true, this
ADR is void and log retention becomes a privacy decision on the media clock —
which is why the rule is tested rather than assumed, and why `scrub` blocks by
suffix as well as by name.

**One asymmetry is deliberate.** Traces expire before the logs that reference
the same job. That is correct: a trace is a detailed view of one execution and a
log line is a durable fact about it, and keeping the expensive one as long as
the cheap one buys very little.
