"""Which model runs which stage.

## What the router can and cannot know

It can measure cost exactly, and it can measure compliance exactly: whether the
answer parsed as JSON, and whether it respected the constraints the stage
imposed. Span proposal is the sharp case — the stage hands the model a list of
legal cut points and drops anything off them, so the refusal count is a direct,
corpus-free measurement of whether a model can follow a hard instruction.

It cannot measure whether the cut is *good*. That needs an editor's own EDL to
compare against, which the project does not have yet (see ADR-0010). So the
router does not pretend: **the policy chooses, and the measurements are recorded
and reported.** Turning that evidence into automatic promotion and demotion is a
change to make when there is a corpus to validate it against, not before, or the
router will confidently demote a good model on four unlucky calls.

## The three tasks are not alike

    brief     one call, small, structured output          quality barely matters
    spans     one call per long beat, dozens per job      constraint-following
    score     one call per 40 beats                       calibrated judgement

`spans` is where a frontier model earns its price: deciding that a span is a
coherent thought is the whole job, and a model that cannot hold to CUT_POINTS
produces nothing usable. `brief` parses a sentence into a target duration and a
shape — the cheapest model that returns valid JSON is the right answer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import catalog, providers
from .base import CallRecord, Completion, Ledger, LLMError

POLICIES = ("quality", "balanced", "cost")


@dataclass(frozen=True)
class Task:
    """What a stage needs, and roughly how much of it it will use."""

    name: str
    min_tier: str            # the floor, whatever the policy says
    prefers: str             # the tier a "quality" policy reaches for
    est_input: int           # per call, for ranking only
    est_output: int

    @property
    def min_rank(self) -> int:
        return catalog.TIERS.index(self.min_tier)


TASKS = {
    # Parsing a sentence of notes into a duration and a shape. Cheap and easy;
    # the only requirement is valid JSON.
    "brief": Task("brief", min_tier="fast", prefers="mid",
                  est_input=1_200, est_output=400),
    # Deciding which span of a long answer is a coherent thought, without
    # straying off the legal cut points. Judgement plus obedience.
    # `prefers` was "frontier", which put every call on the most expensive
    # model available. Measured on a 25.7-minute interview that was 35 calls
    # and $1.04 — 84% of the job's entire model spend — for a task whose answer
    # is checked against CUT_POINTS anyway, and which scored 0 refusals out of
    # 47 proposals. Obedience is what this task needs and the mid tier has it.
    # `--policy quality` still reaches for frontier when somebody wants it.
    "spans": Task("spans", min_tier="mid", prefers="mid",
                  est_input=3_000, est_output=1_400),
    # Scoring beats against the brief, with enough spread for the solver to
    # have something to work with.
    #
    # `est_output` was 1_800, which assumed ~45 tokens per scored beat across a
    # 40-beat window. Measured on real material it is closer to 128 — a model
    # asked for a "one line" rationale writes three — and the window overran its
    # budget mid-answer. The window is 25 now and the rationale is capped in the
    # prompt, so this is 25 x 128 with a little room. `est_input` is unverified
    # and is the next thing to measure: it scales with transcript length, which
    # is the axis nobody has checked.
    # Same reasoning as spans, and the same measurement: sonnet-5 scored a
    # 26-minute interview for $0.19 where opus-5 would have been ~2.5x that.
    "score": Task("score", min_tier="mid", prefers="mid",
                  est_input=6_000, est_output=3_200),
}


def _policy_for(task: str, default: str) -> str:
    """Per-task override, then the run-wide policy. `MISHNE_POLICY_SPANS=quality`."""
    return os.environ.get(f"MISHNE_POLICY_{task.upper()}", default).lower()


def _pinned_for(task: str) -> str:
    """`MISHNE_MODEL_SPANS=anthropic/claude-opus-5` skips routing entirely."""
    return os.environ.get(f"MISHNE_MODEL_{task.upper()}", "")


def candidates(task: Task, policy: str = "balanced") -> list[catalog.Model]:
    """Every model this deployment could use for the task, best first.

    "Best" is what the policy says it is. All three orderings are total and
    deterministic, so two runs with the same keys and the same policy pick the
    same model — which is what makes the reproducibility record meaningful.
    """
    have = set(providers.available())
    pool = [m for m in catalog.load()
            if m.provider in have and m.tier_rank >= task.min_rank]
    if not pool:
        return []

    cost = lambda m: m.blended_cost(task.est_input, task.est_output)  # noqa: E731
    prefers = catalog.TIERS.index(task.prefers)

    if policy == "quality":
        # Highest tier wins; cheapest breaks the tie.
        pool.sort(key=lambda m: (-m.tier_rank, cost(m), m.id))
    elif policy == "cost":
        pool.sort(key=lambda m: (cost(m), -m.tier_rank, m.id))
    else:
        # Balanced: the task's preferred tier first, then cheapest within it.
        # Deliberately not "cheapest overall" — a task that declares it wants a
        # frontier model for a reason should not be silently downgraded because
        # a fast model is a tenth of the price. The way to spend less here is to
        # ask for the cost policy and mean it.
        pool.sort(key=lambda m: (abs(m.tier_rank - prefers), cost(m), m.id))
    return pool


@dataclass
class Router:
    """Picks a model per task, calls it, falls over, and records what happened."""

    policy: str = "balanced"
    ledger: Ledger = None

    def __post_init__(self):
        if self.policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}")
        if self.ledger is None:
            self.ledger = Ledger()

    def plan(self, task_name: str) -> list[catalog.Model]:
        task = TASKS[task_name]
        pinned = _pinned_for(task_name)
        if pinned:
            provider, _, model_id = pinned.rpartition("/")
            return [catalog.find(model_id, provider)]
        return candidates(task, _policy_for(task_name, self.policy))

    def available_for(self, task_name: str) -> bool:
        return bool(self.plan(task_name))

    def mark_unparsed(self, completion: Completion) -> None:
        """Tell the ledger the answer could not be used.

        `ok` records that the call returned; only the calling stage knows
        whether the answer was usable, and until this existed the ledger said
        `1/1 ok` for a call that produced nothing and still cost money. That
        gap mattered: parse compliance is one of the few things ADR-0011 can
        measure without a corpus, and it was the one thing not being recorded.
        """
        record = self.ledger.by_seq(completion.record_seq)
        if record is None:
            for candidate in reversed(self.ledger.calls):
                if candidate.model == completion.model and candidate.ok:
                    record = candidate
                    break
        if record is not None:
            record.parsed = False
            record.stop_reason = completion.stop_reason

    def complete(self, task_name: str, *, system: str, user: str,
                 max_tokens: int = 4096, violations: int = 0,
                 proposals: int = 0) -> Completion:
        """Run the task on the best available model, falling over on failure.

        Failover crosses vendors on purpose. An outage at one vendor should cost
        a slower job, not a failed one — and every model that actually ran is
        recorded, so a job produced by two of them says so rather than claiming
        the one it started with.

        A non-retryable failure — a bad model id, a malformed request — stops
        the chain. Those fail identically everywhere, and walking three keys to
        discover that wastes the operator's money and hides the real error.
        """
        plan = self.plan(task_name)
        if not plan:
            raise LLMError(
                f"no model available for '{task_name}'. Set one of "
                f"{', '.join(c.api_key_env for c in providers.PROVIDERS.values())}"
                f", or pass --spans none / --scorer heuristic to run offline.",
                retryable=False)

        first, last_error = plan[0], None
        for model in plan[:3]:
            try:
                provider = providers.get(model.provider)
            except LLMError as exc:
                last_error = exc
                continue
            try:
                out = provider.complete(model=model.id, system=system,
                                        user=user, max_tokens=max_tokens)
            except LLMError as exc:
                self.ledger.add(CallRecord(
                    task=task_name, provider=model.provider, model=model.id,
                    ok=False, error=type(exc).__name__))
                last_error = exc
                if not exc.retryable:
                    raise
                continue

            cost = model.cost_for(out.input_tokens, out.output_tokens)
            out.record_seq = self.ledger.add(CallRecord(
                task=task_name, provider=model.provider, model=model.id,
                ok=True, latency_ms=out.latency_ms,
                input_tokens=out.input_tokens, output_tokens=out.output_tokens,
                cost_usd=cost or 0.0, priced=cost is not None,
                stop_reason=out.stop_reason,
                violations=violations,
                proposals=proposals,
                fell_back_from=("" if model is first
                                else f"{first.provider}/{first.id}"))).seq
            return out

        raise LLMError(f"every model for '{task_name}' failed; "
                       f"last: {last_error}", retryable=False)

    def estimate(self, calls_per_task: dict[str, int]) -> dict:
        """What a job will cost in model spend, before it runs.

        The product shows the customer a credit estimate and asks them to
        approve it before the job starts (ADR-0006), and that estimate has to
        come from the model that will actually run — which is a routing
        decision, so it belongs here. Uses the per-task token estimates in
        `TASKS`, which are averages: the number to quote a customer is this
        multiplied by a safety margin, not this.

        A model with no price in the catalog makes the total unknowable rather
        than cheap, and this says so instead of quietly under-quoting.
        """
        rows, total, unpriced = [], 0.0, False
        for task_name, n in calls_per_task.items():
            task = TASKS[task_name]
            plan = self.plan(task_name)
            if not plan or n <= 0:
                continue
            model = plan[0]
            each = model.cost_for(task.est_input, task.est_output)
            if each is None:
                unpriced = True
                rows.append({"task": task_name, "calls": n,
                             "model": f"{model.provider}/{model.id}",
                             "usd": None})
                continue
            total += each * n
            rows.append({"task": task_name, "calls": n,
                         "model": f"{model.provider}/{model.id}",
                         "usd": round(each * n, 6)})
        return {"rows": rows, "usd": None if unpriced else round(total, 6),
                "policy": self.policy}

    def note_violations(self, task_name: str, violations: int,
                        proposals: int, completion=None) -> None:
        """Attach a constraint result to the call that has just been recorded.

        The count is only known after the stage has checked the answer, which is
        necessarily after `complete` returned. Kept as a separate step rather
        than a callback because the stage, not the router, decides what counts
        as a violation.
        """
        record = (self.ledger.by_seq(completion.record_seq)
                  if completion is not None else None)
        if record is None:
            # No completion given: the old backwards scan, kept for callers
            # that have not been threaded through. Correct only while one call
            # per task is in flight.
            for candidate in reversed(self.ledger.calls):
                if candidate.task == task_name and candidate.ok:
                    record = candidate
                    break
        if record is not None:
            record.violations += violations
            record.proposals += proposals
