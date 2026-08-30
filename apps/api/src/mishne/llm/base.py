"""One interface for every language model the platform can use.

## Why this exists

Three stages call a model — the brief, span proposal, and scoring — and each had
a vendor SDK imported directly inside it. That makes the vendor a property of
the pipeline rather than a deployment choice, and it makes "use the cheap model
for scoring and the good one for proposals" impossible to express.

## Why the model list is not in the code

While building this I searched for current pricing and found that my own
training data contained none of the model names now shipping, from any of the
four vendors. Every price and every identifier I would have written from memory
was wrong.

That is the normal condition, not an accident of timing: this list changes every
few weeks. So the catalog is a **data file** the operator can replace without a
release, and the code treats an unknown model as usable-but-unpriced rather than
as an error. See `catalog.py`.

## What a provider must do, and what it must not

A provider turns a system prompt and a user prompt into text, and reports what
that cost. It does not retry across vendors — that is the router's job, because
only the router knows what else is available — and it does not parse the result,
because what counts as a valid answer is the calling stage's business.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class LLMError(RuntimeError):
    """A call failed. Carries whether trying a different model could help."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    def cost_usd(self, price_in: float, price_out: float) -> float:
        """Prices are per million tokens, as every vendor quotes them."""
        return (self.input_tokens * price_in
                + self.output_tokens * price_out) / 1_000_000

    def json(self):
        """The JSON the model was asked for, or raise LLMError.

        Models wrap JSON in prose or a fenced block often enough that stripping
        it belongs here rather than in three copies at the call sites. A model
        that cannot produce parseable JSON for a task is a fact worth recording,
        which is why this raises something the router can count rather than
        returning None.
        """
        text = re.sub(r"^```(?:json)?|```$", "", self.text.strip(),
                      flags=re.M).strip()
        # Some models preface with a sentence. Take the outermost array/object.
        if not text.startswith(("[", "{")):
            match = re.search(r"[\[{].*[\]}]", text, re.S)
            if match:
                text = match.group(0)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"{self.provider}/{self.model} did not return valid JSON: "
                f"{exc}") from exc


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(self, *, model: str, system: str, user: str,
                 max_tokens: int = 4096,
                 temperature: float = 0.0) -> Completion:
        ...


@dataclass
class ProviderConfig:
    """How to reach one vendor. Keys come from the environment, never a file."""

    name: str
    api_key_env: str
    base_url: str = ""

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    @property
    def available(self) -> bool:
        return bool(self.api_key)


@dataclass
class CallRecord:
    """What one call actually did. The evidence half of the routing story.

    Editorial quality cannot be measured without ground truth we do not have.
    Compliance and cost can be measured exactly, for free, on every real job:
    whether the answer parsed, whether it respected the constraints the stage
    imposed, what it cost and how long it took. That is what this records.

    `violations` is the sharpest of these and is stage-specific. Span proposal
    hands the model a list of legal cut points and drops anything off them, so
    the count of dropped proposals is a direct measure of whether a model can
    follow a hard constraint — no judgement, no corpus, no opinion.
    """

    task: str
    provider: str
    model: str
    ok: bool
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    #: False when the catalog has no price for this model. `cost_usd` is then
    #: 0.0 because there is no other honest number to put there, and a consumer
    #: that cannot tell it from a genuinely free call under-charges silently —
    #: which is the whole reason `Model.cost_for` returns None rather than 0.
    priced: bool = True
    #: The exception TYPE, never a provider's message. A vendor quotes the
    #: prompt back in an error string often enough that the message is customer
    #: content, and this record is written into the job's manifest.
    error: str = ""
    violations: int = 0
    proposals: int = 0
    fell_back_from: str = ""

    def to_dict(self) -> dict:
        """The non-empty fields, for the job manifest.

        Booleans are kept even when False, which `v not in ("", 0, 0.0)` did not
        do: `False == 0` in Python, so `ok=False` and `priced=False` — the two
        fields whose False is the entire point of recording them — were dropped
        from every manifest they appeared in.
        """
        return {
            k: v
            for k, v in self.__dict__.items()
            if isinstance(v, bool) or v not in ("", 0, 0.0)
        }


@dataclass
class Ledger:
    """Every call a job made, for the run report and for `model_versions`."""

    calls: list[CallRecord] = field(default_factory=list)

    def add(self, record: CallRecord) -> CallRecord:
        self.calls.append(record)
        return record

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    def by_task(self) -> dict[str, list[CallRecord]]:
        out: dict[str, list[CallRecord]] = {}
        for c in self.calls:
            out.setdefault(c.task, []).append(c)
        return out

    def models_used(self) -> dict[str, list[str]]:
        """Task -> the models that actually ran it, failover included.

        A job that fell over to a second vendor mid-way was produced by both,
        and the reproducibility record has to say so or it is a lie.
        """
        out: dict[str, list[str]] = {}
        for c in self.calls:
            if not c.ok:
                continue
            key = f"{c.provider}/{c.model}"
            out.setdefault(c.task, [])
            if key not in out[c.task]:
                out[c.task].append(key)
        return out

    def summary(self) -> list[str]:
        lines = []
        for task, calls in self.by_task().items():
            ok = [c for c in calls if c.ok]
            models = ", ".join(sorted({f"{c.provider}/{c.model}" for c in ok}))
            cost = sum(c.cost_usd for c in calls)
            latency = sum(c.latency_ms for c in ok) / max(1, len(ok))
            line = (f"{task:<9} {models or '—'} · {len(ok)}/{len(calls)} ok · "
                    f"${cost:.4f} · {latency:.0f}ms avg")
            viol = sum(c.violations for c in calls)
            prop = sum(c.proposals for c in calls)
            if prop:
                line += (f" · {viol}/{prop} proposals refused by the "
                         f"silence gate")
            lines.append(line)
        return lines
