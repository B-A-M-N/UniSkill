"""Earliest-failure-stage diagnosis with a fixed, auditable decision table.

The mapping is data, not scattered `if` branches: an operator can read exactly
why a given failure produced a given intervention. Every diagnosis carries the
evidence that produced it, so a repair can justify itself later.
"""
from __future__ import annotations

from .common import UniSkillError
from .status import UNVERIFIED

# stage -> failure signature -> diagnosis
#   intervention: what a repair is allowed to try
#   class: FILE (safe, reversible), REVIEW (needs operator), BLOCKED (stop)
DECISION_TABLE = {
    "validated": {
        "INVALID": ("Contract or skill package failed portable validation",
                    "REPAIR_PACKAGE", "FILE"),
    },
    "installed": {
        "MISSING": ("Deployed tree is absent", "REINSTALL", "FILE"),
        "DRIFTED": ("Deployed tree no longer matches the receipt digest",
                    "RECONCILE_TRANSACTION", "FILE"),
    },
    "discovered": {
        "NOT_INSTALLED": ("No candidate directory in any effective discovery root",
                          "REPLAN_ROOTS", "FILE"),
        "WRONG_ROOT": ("Skill exists but not in an active/preferred discovery root",
                       "REINSTALL_TO_ACTIVE_ROOT", "FILE"),
        "SHADOWED": ("A divergent same-named skill shadows this one in a lower-precedence root",
                     "RESOLVE_COLLISION", "REVIEW"),
        "POLICY_DENIED": ("Harness policy denies or disables this skill",
                          "REPORT_POLICY_BLOCKER", "BLOCKED"),
        "TRUST_REQUIRED": ("Harness requires project trust / activation before loading",
                           "REQUEST_OPERATOR_TRUST", "BLOCKED"),
        "QUARANTINED": ("Harness quarantined the skill pending security review",
                        "REQUEST_SECURITY_REVIEW", "BLOCKED"),
        "RELOAD_REQUIRED": ("Skill written but the runtime has not reloaded its registry",
                            "TRIGGER_HARNESS_RELOAD", "REVIEW"),
        "UNVERIFIED": ("No native discovery probe available for this harness",
                       "DECLARE_UNVERIFIABLE", "BLOCKED"),
    },
    "invoked": {
        "NO_INVOCATION_MECHANISM": ("Harness exposes no documented explicit invocation",
                                    "DECLARE_UNSUPPORTED", "BLOCKED"),
        "NOT_INVOKED": ("Runtime did not activate the skill when asked explicitly",
                        "REVIEW_TRIGGER_SURFACE", "REVIEW"),
    },
    "selected": {
        "NO_SELECTION_MECHANISM": ("Harness provides no observable automatic-selection signal",
                                   "DECLARE_UNSUPPORTED", "BLOCKED"),
        "FALSE_POSITIVE": ("Skill was selected on a request it must decline",
                           "OPTIMIZE_TRIGGER_BOUNDARY", "FILE"),
        "FALSE_NEGATIVE": ("Skill was not selected on a request it must handle",
                           "OPTIMIZE_TRIGGER_COVERAGE", "FILE"),
    },
    "functional": {
        "MISSING_CAPABILITY": ("Harness lacks a capability the contract requires",
                               "DECLARE_UNSUPPORTED_OR_ADAPT", "REVIEW"),
        "WRONG_RESULT": ("Acceptance case failed on observable behavior",
                         "REVISE_INSTRUCTIONS_AND_RETEST", "FILE"),
        "FORBIDDEN_SIDE_EFFECT": ("Acceptance case used a forbidden tool or touched forbidden paths",
                                  "REVISE_INSTRUCTIONS_AND_RETEST", "FILE"),
        "UNRELIABLE": ("Passed in some trials and failed in others",
                       "TIGHTEN_INSTRUCTIONS_AND_RETEST", "FILE"),
    },
    "certified": {
        "REGRESSION": ("Revised candidate broke previously established behavior",
                       "REJECT_CANDIDATE_OR_ROLLBACK", "FILE"),
    },
}

# Ordering used to pick the earliest failing stage.
STAGE_ORDER = ("validated", "planned", "installed", "discovered", "invoked",
               "selected", "functional", "certified")

# Failures that mean "stop accurately": the harness or operator cannot proceed.
BLOCKERS = frozenset({"POLICY_DENIED", "TRUST_REQUIRED", "QUARANTINED",
                      "UNVERIFIED", "NO_INVOCATION_MECHANISM",
                      "NO_SELECTION_MECHANISM"})


def known(stage, failure):
    return failure in DECISION_TABLE.get(stage, {})


def classify(stage, failure):
    """Return the decision-table row for a (stage, failure) pair.

    An unknown pair is a hard error rather than a silent pass: a diagnosis the
    engine does not understand must stop, not guess.
    """
    if stage not in DECISION_TABLE:
        raise UniSkillError(f"Unknown lifecycle stage: {stage}")
    if failure not in DECISION_TABLE[stage]:
        raise UniSkillError(f"No diagnosis rule for {stage}/{failure}")
    reason, intervention, cls = DECISION_TABLE[stage][failure]
    return {"stage": stage, "failure": failure, "reason": reason,
            "intervention": intervention, "class": cls,
            "blocked": failure in BLOCKERS}


def earliest(stage_states):
    """Pick the earliest failing stage from {stage: "state-or-failure"}.

    A stage is "failing" when its value is a known failure signature for that
    stage, or when it is an explicit non-passing sentinel (UNVERIFIED counts
    as unproven, not as failed).
    """
    if not isinstance(stage_states, dict):
        raise UniSkillError("stage_states must be a mapping")
    for stage in STAGE_ORDER:
        if stage not in stage_states:
            continue
        value = stage_states[stage]
        if known(stage, value):
            return stage, value
    return None, None


def diagnose(stage_states, evidence=None):
    """Produce the single actionable diagnosis, or an explicit clean stop."""
    stage, failure = earliest(stage_states)
    evidence = evidence or {}
    if stage is None:
        unproven = [(s, stage_states[s]) for s in STAGE_ORDER
                    if s in stage_states and stage_states[s] == UNVERIFIED]
        if unproven:
            first = unproven[0]
            return {"state": "UNPROVEN", "stage": first[0], "failure": UNVERIFIED,
                    "reason": f"{first[0]} was not proven by any runtime evidence",
                    "intervention": "DECLARE_UNVERIFIABLE", "class": "BLOCKED",
                    "blocked": True,
                    "evidence": evidence.get(first[0], {}),
                    "note": "Stopping is the correct result when no evidence exists"}
        return {"state": "CLEAN", "stage": None, "failure": None,
                "reason": "All proven stages passed", "blocked": False,
                "evidence": evidence}
    row = classify(stage, failure)
    row["state"] = "DIAGNOSED"
    row["evidence"] = evidence.get(stage, {})
    row["note"] = ("Repair targets the earliest failing stage only; later stages "
                   "are consequences and are not independently repaired")
    return row