"""Credit ledger.

Append-only. Balance is a projection of the ledger, never a mutable column —
a mutable balance is how double-charges and silent drift happen.

Job lifecycle:

    submit   -> hold(cap)          available -= cap
    success  -> settle(actual)     hold released, actual debited
    failure  -> refund(cap)        hold released in full, nothing charged
    cancel   -> release(cap)       hold released in full

Settlement charges min(actual, cap). The approved estimate is a ceiling: the
user is never billed more than they agreed to at submission, which is what makes
consumption pricing tolerable.

Stubbed against fixtures until the database exists. Real implementation:
serializable transaction, unique constraint on (job_id, kind) for idempotency,
and Stripe webhook dedupe on event id.

See docs/adr/0006-credit-hold-settle-ledger.md.
"""

from __future__ import annotations

from dataclasses import dataclass


class InsufficientCredits(Exception):
    def __init__(self, required: float, available: float) -> None:
        self.required = required
        self.available = available
        super().__init__(f"requires {required} credits, {available} available")


@dataclass
class Balance:
    available: float
    held: float

    @property
    def total(self) -> float:
        return self.available + self.held


def hold(org_id: str, job_id: str, project_id: str, cap: float, balance: Balance) -> Balance:
    """Reserve credits at submission.

    Holding at submission rather than debiting at completion is what stops a
    user with 5 credits from starting ten concurrent jobs.
    """
    if balance.available < cap:
        raise InsufficientCredits(cap, balance.available)
    return Balance(available=balance.available - cap, held=balance.held + cap)


def settle(org_id: str, job_id: str, actual: float, cap: float, balance: Balance) -> Balance:
    """Charge actual usage on success, capped at the approved figure."""
    charged = min(actual, cap)
    return Balance(available=balance.available + (cap - charged), held=balance.held - cap)


def refund(org_id: str, job_id: str, cap: float, balance: Balance) -> Balance:
    """Return the whole hold. A job that failed is never charged."""
    return Balance(available=balance.available + cap, held=balance.held - cap)


def purchase(org_id: str, credits: float, balance: Balance) -> Balance:
    return Balance(available=balance.available + credits, held=balance.held)
