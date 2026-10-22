"""Observable deployment status: what is proven, what is merely present.

Status answers three separate questions and never conflates them:

1. installation   - does the deployed tree still match its receipt?
2. discovery      - did the RUNTIME report the skill? (needs a native probe)
3. behavior       - did it invoke/select/execute correctly? (needs a case run)

A filesystem copy only ever answers (1). Anything else without evidence is
reported as UNVERIFIED with the reason it is unverified.
"""
from __future__ import annotations

from pathlib import Path

from .common import UniSkillError, digest_tree
from .validator import validate_skill

# Ordered lifecycle stages. The earliest stage that is not proven is the only
# stage worth reporting: a later failure is usually a consequence of it.
STAGES = (
    "validated",
    "planned",
    "installed",
    "discovered",
    "invoked",
    "selected",
    "functional",
    "certified",
)

UNVERIFIED = "UNVERIFIED"


def installation_status(receipt, verify):
    """Layer 1: does the deployed tree still match what we wrote?"""
    return {
        "state": verify["state"],
        "receipt_id": receipt.get("receipt_id"),
        "destinations": verify.get("destinations", []),
        "evidence": "digest-tree-match",
    }


def skill_status(skill_path):
    """Portable structural validity of the skill directory itself."""
    report = validate_skill(Path(skill_path))
    return {
        "state": report["state"],
        "name": report.get("name"),
        "errors": report.get("errors", []),
        "warnings": report.get("warnings", []),
        "evidence": "structural-only",
        "digest": digest_tree(skill_path) if report["state"] == "VALID" else None,
    }


def assemble(*, receipt=None, verify=None, targets=None, evidence=None, reason=None):
    """Build one status record with per-stage evidence and no inference.

    `targets` maps harness -> adapter-reported state dicts. `evidence` maps
    harness -> {"discovered":..., "invoked":..., "selected":..., "functional":...}
    where each value is a dict with at least {"state": ...} produced by a real
    probe. A missing entry stays UNVERIFIED.
    """
    targets = targets or {}
    evidence = evidence or {}
    out: dict = {
        "skill": (receipt or {}).get("skill"),
        "receipt_id": (receipt or {}).get("receipt_id"),
        "state": UNVERIFIED,
        "earliest_unproven_stage": None,
        "targets": {},
    }
    if receipt is None:
        out["reason"] = reason or "No active UniSkill deployment receipt"
        return out

    install_layer = installation_status(receipt, verify or {"state": UNVERIFIED,
                                                             "destinations": []})
    out["deployment"] = install_layer
    if install_layer["state"] != "INSTALLED":
        out["state"] = "FAILED"
        out["failed_stage"] = "installed"
        out["reason"] = "Deployed files no longer match the receipt"
        return out

    first_gap = None
    for name in sorted(targets):
        row = dict(targets[name]) if isinstance(targets[name], dict) else {}
        ev = evidence.get(name, {}) if isinstance(evidence.get(name), dict) else {}
        stages = {}
        for stage in ("discovered", "invoked", "selected", "functional"):
            got = ev.get(stage)
            if isinstance(got, dict) and got.get("state"):
                stages[stage] = got["state"]
            else:
                stages[stage] = UNVERIFIED
                if first_gap is None:
                    first_gap = (name, stage)
        row.update({
            "installation": "INSTALLED",
            "discovery": stages["discovered"],
            "explicit_invocation": stages["invoked"],
            "automatic_selection": stages["selected"],
            "functional": stages["functional"],
            "evidence": ev or {"note": "No runtime probe executed"},
            "certified": "CERTIFIED" if all(
                stages[s] == "PASS" for s in ("discovered", "functional")) else UNVERIFIED,
        })
        out["targets"][name] = row

    out["state"] = "INSTALLED"
    if first_gap:
        out["earliest_unproven_stage"] = {"harness": first_gap[0], "stage": first_gap[1]}
    out["note"] = ("Installation is proven by digest. Discovery, invocation, "
                   "selection and functional correctness each require their own "
                   "runtime evidence and are UNVERIFIED by default.")
    return out


def earliest_unproven(status):
    """The single stage a repair attempt should target first."""
    if not isinstance(status, dict):
        raise UniSkillError("Status must be a mapping")
    if status.get("state") == "FAILED" and status.get("failed_stage"):
        return {"harness": None, "stage": status["failed_stage"], "reason": status.get("reason")}
    gap = status.get("earliest_unproven_stage")
    if not gap:
        return None
    harness, stage = gap["harness"], gap["stage"]
    detail = status.get("targets", {}).get(harness, {})
    return {"harness": harness, "stage": stage,
            "observed": detail.get(stage if stage != "discovered" else "discovery")}