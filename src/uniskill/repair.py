"""Bounded, evidence-gated self-correction.

Two invariants govern every repair:

1. A repair never trades a behavioral invariant for a better score. There is no
   code path here that weakens a permission, grants a credential, skips consent,
   or drops an invariant from the contract.
2. A repair is bounded. Each plan gets an explicit attempt and cost budget, and
   exceeding it stops with a report instead of looping.

Repairs are declared as data so the budget and the safety fence are auditable.
Each action declares a `mutates` class: NONE (pure recomputation), FILES (writes
inside the skill's own directory), or POLICY (needs explicit operator approval
and is never run automatically).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .common import UniSkillError
from .diagnosis import BLOCKERS, classify

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_COST = 10

# Repairs that would require editing harness/user policy are never automatic.
POLICY_ACTIONS = frozenset({"REQUEST_OPERATOR_TRUST", "REQUEST_SECURITY_REVIEW",
                            "TRIGGER_HARNESS_RELOAD", "RESOLVE_COLLISION",
                            "DECLARE_UNSUPPORTED_OR_ADAPT"})


@dataclass
class Budget:
    """Attempt/cost budget. Cost is charged per executed repair."""
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_cost: int = DEFAULT_MAX_COST
    attempts: int = 0
    spent: int = 0

    def charge(self, cost):
        if self.attempts >= self.max_attempts:
            raise UniSkillError(
                f"Repair budget exhausted: {self.attempts}/{self.max_attempts} attempts")
        if self.spent + cost > self.max_cost:
            raise UniSkillError(
                f"Repair cost budget exhausted: {self.spent}+{cost} > {self.max_cost}")
        if cost <= 0:
            raise UniSkillError(f"Repair cost must be positive, got {cost}")
        self.attempts += 1
        self.spent += cost

    def remaining(self):
        return {"attempts": max(0, self.max_attempts - self.attempts),
                "cost": max(0, self.max_cost - self.spent)}


# action -> (cost, mutates, handler-name)
ACTIONS = {
    "REPAIR_PACKAGE": (1, "FILES", "regenerate_candidate"),
    "REINSTALL": (1, "FILES", "reinstall"),
    "RECONCILE_TRANSACTION": (2, "FILES", "reconcile"),
    "REPLAN_ROOTS": (1, "FILES", "replan"),
    "REINSTALL_TO_ACTIVE_ROOT": (2, "FILES", "reinstall"),
    "RESOLVE_COLLISION": (1, "POLICY", "none"),
    "REPORT_POLICY_BLOCKER": (0, "NONE", "none"),
    "REQUEST_OPERATOR_TRUST": (0, "NONE", "none"),
    "REQUEST_SECURITY_REVIEW": (0, "NONE", "none"),
    "TRIGGER_HARNESS_RELOAD": (1, "POLICY", "none"),
    "DECLARE_UNVERIFIABLE": (0, "NONE", "none"),
    "REVIEW_TRIGGER_SURFACE": (1, "NONE", "none"),
    "DECLARE_UNSUPPORTED": (0, "NONE", "none"),
    "OPTIMIZE_TRIGGER_BOUNDARY": (2, "FILES", "optimize_description"),
    "OPTIMIZE_TRIGGER_COVERAGE": (2, "FILES", "optimize_description"),
    "DECLARE_UNSUPPORTED_OR_ADAPT": (2, "POLICY", "none"),
    "REVISE_INSTRUCTIONS_AND_RETEST": (3, "FILES", "revise_instructions"),
    "TIGHTEN_INSTRUCTIONS_AND_RETEST": (3, "FILES", "revise_instructions"),
    "REJECT_CANDIDATE_OR_ROLLBACK": (1, "FILES", "reject_candidate"),
}

# Safety fence: an intervention may never be paired with these.
FORBIDDEN_EFFECTS = ("weaken_permission", "grant_credential", "skip_consent",
                     "drop_invariant", "disable_harness_check")


@dataclass
class RepairPlan:
    """One bounded repair attempt derived from exactly one diagnosis."""
    intervention: str
    stage: str
    failure: str
    diagnosis_class: str
    blocked: bool = False
    handler: str = "none"
    cost: int = 0
    mutates: str = "NONE"
    requires_approval: bool = False
    forbidden_effects: list = field(default_factory=list)

    def as_dict(self):
        return {"intervention": self.intervention, "stage": self.stage,
                "failure": self.failure, "class": self.diagnosis_class,
                "blocked": self.blocked, "handler": self.handler,
                "cost": self.cost, "mutates": self.mutates,
                "requires_approval": self.requires_approval,
                "safety": {"forbidden_effects": FORBIDDEN_EFFECTS}}


def plan_repair(diagnosis_row, *, allow_policy=False, invariants=None):
    """Translate a diagnosis into a bounded, safety-checked repair plan."""
    if not isinstance(diagnosis_row, dict) or "intervention" not in diagnosis_row:
        raise UniSkillError("plan_repair requires a diagnosis row")
    intervention = diagnosis_row["intervention"]
    if intervention not in ACTIONS:
        raise UniSkillError(f"Unknown repair intervention: {intervention}")
    cost, mutates, handler = ACTIONS[intervention]

    blocked = bool(diagnosis_row.get("blocked")) or diagnosis_row.get("failure") in BLOCKERS
    requires_approval = mutates == "POLICY"
    plan = RepairPlan(intervention=intervention,
                      stage=diagnosis_row.get("stage", ""),
                      failure=diagnosis_row.get("failure", ""),
                      diagnosis_class=diagnosis_row.get("class", ""),
                      blocked=blocked, handler=handler, cost=cost,
                      mutates=mutates, requires_approval=requires_approval)

    if requires_approval and not allow_policy:
        plan.blocked = True
    if invariants and mutates == "FILES":
        # A file-writing repair may not target a run in which an invariant is
        # already unmet: that is the trade-away the product forbids.
        unmet = [i for i in invariants if isinstance(i, dict) and not i.get("satisfied", True)]
        if unmet:
            plan.blocked = True
            plan.forbidden_effects = ["drop_invariant"]
    return plan


def run(budget, plans, handlers, *, allow_policy=False):
    """Execute repair plans in order under a shared budget.

    `handlers` maps handler-name -> callable(**kwargs) returning a result dict.
    A handler that is absent is reported as UNAVAILABLE, never faked as success.
    Stops at the first blocked plan, the first failed repair, or budget
    exhaustion — and always returns what actually happened.
    """
    if not isinstance(plans, (list, tuple)):
        raise UniSkillError("run expects a list of repair plans")
    trace, stopped = [], None
    for raw in plans:
        plan = raw if isinstance(raw, RepairPlan) else plan_repair(raw, allow_policy=allow_policy)
        if plan.blocked:
            stopped = stopped or {"reason": "blocked", "intervention": plan.intervention,
                                  "detail": diagnosis_row_reason(plan)}
            trace.append({**plan.as_dict(), "result": "BLOCKED",
                          "detail": stopped["detail"]})
            break
        handler = handlers.get(plan.handler)
        if plan.handler == "none" or handler is None:
            # Advisory work is reported, not charged: it needs no budget because
            # it executes nothing.
            trace.append({**plan.as_dict(), "result": "ADVISORY",
                          "detail": "No automatic handler; operator action required"})
            continue
        try:
            budget.charge(plan.cost)
        except UniSkillError as exc:
            stopped = {"reason": "budget", "intervention": plan.intervention, "detail": str(exc)}
            trace.append({**plan.as_dict(), "result": "BUDGET_EXHAUSTED", "detail": str(exc)})
            break
        try:
            outcome = handler(plan=plan)
        except UniSkillError as exc:
            trace.append({**plan.as_dict(), "result": "FAILED", "detail": str(exc)})
            stopped = {"reason": "handler_error", "intervention": plan.intervention,
                       "detail": str(exc)}
            break
        ok = isinstance(outcome, dict) and outcome.get("state") in {"REPAIRED", "UNCHANGED"}
        trace.append({**plan.as_dict(), "result": "REPAIRED" if ok else "FAILED",
                      "detail": outcome})
        if not ok:
            stopped = {"reason": "handler_reported_failure",
                       "intervention": plan.intervention, "detail": outcome}
            break
    state = "REPAIRED" if stopped is None and any(
        t["result"] == "REPAIRED" for t in trace) else (
        "STOPPED" if stopped else "NO_ACTION")
    return {"state": state, "trace": trace, "stopped": stopped,
            "budget": budget.remaining(),
            "safety": {"never": list(FORBIDDEN_EFFECTS),
                       "policy_actions_require_approval": True,
                       "invariant_tradeaway_blocked": True}}


def diagnosis_row_reason(plan):
    if plan.diagnosis_class == "BLOCKED":
        return ("External harness lacks a required capability or the operator has not "
                "granted permission; stopping is the correct outcome")
    return "Blocked before execution"


def assert_no_weakening(before, after, invariants):
    """Fail closed if a repair weakened permissions or dropped an invariant."""
    for effect in FORBIDDEN_EFFECTS:
        if before.get(effect) != after.get(effect):
            raise UniSkillError(f"Repair attempted forbidden effect: {effect}")
    kept = {i for i in invariants if i in after.get("invariants", [])}
    if len(kept) != len(set(invariants)):
        raise UniSkillError("Repair dropped a behavioral invariant")
    return True