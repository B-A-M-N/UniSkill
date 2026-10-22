"""UniSkill command-line entrypoint. Machine-readable JSON by default."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .common import UniSkillError
from .contract import generate_skill, load_contract
from .deploy import (install, latest_receipt, plan, read_receipt, rollback,
                     state_root, verify_receipt)
from .evaluation import load_cases, run_evals
from .harnesses import (HARNESSES, capability_profile, detect, load_registry,
                        native_discovery, opencode_diagnostics, crush_diagnostics, inspect_harness_skill)
from .validator import validate_skill
from . import diagnosis, repair, status

def build_parser():
    parser = argparse.ArgumentParser(prog="uniskill", description="Portable skill engineering CLI")
    parser.add_argument("--registry", type=Path,
                        help="Opt-in JSON registry for custom and overridden harnesses (before subcommand)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("detect", help="Detect binaries (not functional skill support)")
    commands.add_parser("harnesses", help="List built-in and custom harness mapping/support tiers")
    create = commands.add_parser("create", help="Generate SKILL.md from behavioral contract")
    create.add_argument("contract", type=Path)
    create.add_argument("--out", type=Path, required=True)
    valid = commands.add_parser("validate", help="Structural validation, no script execution")
    valid.add_argument("skill", type=Path)
    valid.add_argument("--target")
    for cmd in ("plan", "install"):
        sub = commands.add_parser(cmd, help="Preview or apply skill deployment")
        sub.add_argument("skill", type=Path)
        sub.add_argument("--target", nargs="+", required=True)
        sub.add_argument("--scope", choices=("project", "user"), default="project")
        sub.add_argument("--workspace", type=Path, default=Path.cwd())
        sub.add_argument("--home", type=Path, default=Path.home())
        sub.add_argument("--state-root", type=Path)
        if cmd == "install":
            sub.add_argument("--apply", action="store_true", help="Required for mutation")
            sub.add_argument("--replace", action="store_true", help="Approve replacing different versions")
    for cmd in ("verify", "status", "doctor"):
        sub = commands.add_parser(cmd)
        sub.add_argument("skill", help="Skill ID")
        sub.add_argument("--scope", choices=("project", "user"), default="project")
        sub.add_argument("--workspace", type=Path, default=Path.cwd())
        sub.add_argument("--home", type=Path, default=Path.home())
        sub.add_argument("--state-root", type=Path)
        if cmd == "verify":
            sub.add_argument("--probe", action="store_true", help="Try implemented native probes")
        if cmd == "doctor":
            sub.add_argument("--target", nargs="+", help="Inspect the named harness roots even without a UniSkill receipt")
    undo = commands.add_parser("rollback", help="Rollback exact receipt when deployed digest unchanged")
    undo.add_argument("receipt_id")
    undo.add_argument("--scope", choices=("project", "user"), default="project")
    undo.add_argument("--workspace", type=Path, default=Path.cwd())
    undo.add_argument("--home", type=Path, default=Path.home())
    undo.add_argument("--state-root", type=Path)
    evaluation = commands.add_parser("evaluate", help="Opt-in executable prompt/tool-trace tests")
    evaluation.add_argument("cases", type=Path)
    evaluation.add_argument("--runner-config", type=Path, required=True)
    evaluation.add_argument("--execute", action="store_true", help="Required to run subprocesses")
    evaluation.add_argument("--repetitions", type=int, default=1)
    capabilities = commands.add_parser("capabilities")
    capabilities.add_argument("harness")
    diagnose = commands.add_parser("diagnose",
        help="Earliest-failing-stage diagnosis for a deployed skill")
    diagnose.add_argument("skill")
    diagnose.add_argument("--scope", choices=("project", "user"), default="project")
    diagnose.add_argument("--workspace", type=Path, default=Path.cwd())
    diagnose.add_argument("--home", type=Path, default=Path.home())
    diagnose.add_argument("--state-root", type=Path)
    diagnose.add_argument("--target", nargs="+", help="Restrict diagnosis to these harnesses")
    repair_cmd = commands.add_parser("repair",
        help="Plan a bounded repair for the earliest diagnosed failure")
    repair_cmd.add_argument("skill")
    repair_cmd.add_argument("--scope", choices=("project", "user"), default="project")
    repair_cmd.add_argument("--workspace", type=Path, default=Path.cwd())
    repair_cmd.add_argument("--home", type=Path, default=Path.home())
    repair_cmd.add_argument("--state-root", type=Path)
    repair_cmd.add_argument("--target", nargs="+", help="Restrict repair to these harnesses")
    repair_cmd.add_argument("--max-attempts", type=int, default=repair.DEFAULT_MAX_ATTEMPTS)
    repair_cmd.add_argument("--max-cost", type=int, default=repair.DEFAULT_MAX_COST)
    repair_cmd.add_argument("--allow-policy", action="store_true",
        help="Approve repairs that touch harness-side policy (never weakens permissions)")
    repair_cmd.add_argument("--execute", action="store_true",
        help="Required to run repairs; without it only the plan is reported")
    return parser

def dispatch(args):
    cmd = args.command
    registry = load_registry(args.registry)
    if cmd == "detect":
        return {"harnesses":detect(registry)}
    if cmd == "harnesses":
        return {"count":len(registry),
                "harnesses":{h:capability_profile(h,registry) for h in registry}}
    if cmd == "capabilities":
        if args.harness not in registry:
            raise UniSkillError(f"Unknown harness: {args.harness}")
        return capability_profile(args.harness,registry)
    if cmd == "create":
        contract = load_contract(args.contract)
        output = generate_skill(contract, args.out)
        return {"state":"SPECIFIED", "skill":str(output), "validation":validate_skill(output),
                "note":"Deterministic scaffold; no LLM optimization or runtime eval claimed"}
    if cmd == "validate":
        result = validate_skill(args.skill)
        if args.target and args.target not in registry:
            raise UniSkillError(f"Unknown harness: {args.target}")
        if args.target:
            result["target"] = args.target
            result["target_validation"] = "UNVERIFIED: portable checks only"
        return result
    if cmd in {"plan", "install"}:
        targets = list(registry) if "all" in args.target else args.target
        p = plan(args.skill, targets, args.scope, args.workspace.resolve(), args.home.resolve(), registry)
        if cmd == "plan" or not args.apply:
            p["note"] = "Dry run. Pass install --apply to make changes."
            return p
        state = state_root(args.scope, args.workspace.resolve(), args.home.resolve(), args.state_root)
        return install(p, state, args.replace)
    if cmd in {"verify", "status", "doctor", "rollback"}:
        state = state_root(args.scope, args.workspace.resolve(), args.home.resolve(), args.state_root)
        if cmd == "rollback":
            return rollback(state, args.receipt_id)
        receipt = latest_receipt(state, args.skill)
        if cmd == "doctor":
            targets = args.target or (receipt["targets"] if receipt else [])
            if not targets:
                return {"skill":args.skill,"state":"UNVERIFIED",
                        "reason":"Specify --target to inspect a skill without a receipt"}
            if any(h not in registry for h in targets):
                raise UniSkillError("Unknown target(s): " + ", ".join(set(targets)-set(registry)))
            findings = {h:inspect_harness_skill(args.skill,h,args.scope,
                                args.workspace.resolve(),args.home.resolve(),registry)
                        for h in targets}
            return {"skill":args.skill,"state":"DIAGNOSED", "targets":findings,
                    "receipt_id":receipt["receipt_id"] if receipt else None}
        if not receipt:
            return {"skill":args.skill, "state":"UNVERIFIED", "reason":"No active UniSkill deployment receipt"}
        verified = verify_receipt(receipt)
        targets, evidence = {}, {}
        for h in receipt["targets"]:
            targets[h] = {}
            evidence[h] = {}
            if cmd == "verify" and args.probe and verified["state"] == "INSTALLED":
                probe = native_discovery(h, args.skill, args.workspace.resolve())
                evidence[h]["discovered"] = probe
                targets[h]["discovery_evidence"] = probe
            if cmd == "doctor":
                findings = inspect_harness_skill(
                    args.skill, h, args.scope, args.workspace.resolve(),
                    args.home.resolve(), registry)
                targets[h] = findings
                evidence[h]["installed"] = {
                    "state": "PASS" if findings.get("state") == "FILES_PRESENT" else "NOT_INSTALLED"}
                for policy_note in findings.get("policy_diagnostics", []):
                    evidence[h].setdefault("policy", []).append(policy_note)
        result = status.assemble(receipt=receipt, verify=verified,
                                 targets=targets, evidence=evidence)
        result["skill"] = args.skill
        return result
    if cmd in {"diagnose", "repair"}:
        state = state_root(args.scope, args.workspace.resolve(), args.home.resolve(), args.state_root)
        receipt = latest_receipt(state, args.skill)
        if not receipt:
            return {"skill": args.skill, "state": "UNVERIFIED",
                    "reason": "No active UniSkill deployment receipt"}
        verified = verify_receipt(receipt)
        selected = [h for h in (args.target or receipt["targets"]) if h in registry]
        unknown = [h for h in (args.target or []) if h not in registry]
        if unknown:
            raise UniSkillError("Unknown target(s): " + ", ".join(sorted(unknown)))
        stage_states = {
            "installed": "PASS" if verified["state"] == "INSTALLED" else "DRIFTED"}
        found = {h: inspect_harness_skill(args.skill, h, args.scope,
                                          args.workspace.resolve(),
                                          args.home.resolve(), registry)
                 for h in selected}
        # Roll the per-harness findings up into ONE stage verdict. Files being
        # present is a precondition for discovery, not evidence of it: a
        # harness whose runtime was never probed stays UNVERIFIED, and only an
        # actually-missing copy (or an explicit policy blocker) is a failure.
        # Any harness that fails outranks the ones that merely lack evidence.
        precedence = ("POLICY_DENIED", "TRUST_REQUIRED", "QUARANTINED",
                      "SHADOWED", "NOT_INSTALLED", "UNVERIFIED")
        rollup = {"installed": stage_states["installed"]}
        for h, detail in found.items():
            policy = " ".join(detail.get("policy_diagnostics", [])).lower()
            present = detail.get("state") == "FILES_PRESENT"
            if "disabled" in policy or "deny" in policy:
                verdict = "POLICY_DENIED"
            elif "trust" in policy:
                verdict = "TRUST_REQUIRED"
            elif "quarantin" in policy:
                verdict = "QUARANTINED"
            elif detail.get("conflict"):
                verdict = "SHADOWED"
            elif not present:
                verdict = "NOT_INSTALLED"
            else:
                verdict = "UNVERIFIED"
            if verdict != "UNVERIFIED":
                rollup.setdefault("discovered", verdict)
                if precedence.index(verdict) < precedence.index(
                        rollup.get("discovered", "UNVERIFIED")):
                    rollup["discovered"] = verdict
        stage_states = {**stage_states, **rollup}
        row = diagnosis.diagnose(stage_states,
                                 evidence={h: found[h] for h in found})
        report = {"skill": args.skill, "diagnosis": row,
                  "stage_states": stage_states,
                  "inspections": found}
        if cmd == "diagnose":
            return report
        budget = repair.Budget(max_attempts=args.max_attempts, max_cost=args.max_cost)
        # A CLEAN (all proven stages passed) or UNPROVEN (no stage failed, but
        # nothing beyond installation was proven either) diagnosis has no
        # failing stage to repair. Say so plainly instead of erroring.
        if row.get("state") in {"CLEAN", "UNPROVEN"}:
            return {**report, "state": "NO_REPAIR", "repair": None,
                    "reason": ("No failing stage to repair"
                               if row["state"] == "CLEAN" else
                               "No stage failed; remaining stages lack runtime "
                               "evidence and cannot be repaired by UniSkill"),
                    "budget": budget.remaining()}
        plan_item = repair.plan_repair(row, allow_policy=args.allow_policy)
        if not args.execute:
            return {**report, "repair_plan": plan_item.as_dict(),
                    "budget": budget.remaining(),
                    "note": "Dry run. Pass repair --execute to apply."}
        outcome = repair.run(budget, [plan_item], {}, allow_policy=args.allow_policy)
        return {**report, "repair": outcome}
    if cmd == "evaluate":
        cases = load_cases(args.cases)
        if not args.execute:
            return {"state":"UNVERIFIED", "cases":len(cases),
                    "note":"No subprocesses executed; use --execute to approve runner"}
        return run_evals(cases, args.runner_config, args.repetitions)
    raise UniSkillError("Unknown command")

def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        output = dispatch(args)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 2 if output.get("state") == "FAILED" or output.get("state") == "ERROR" else 0
    except (UniSkillError, OSError, ValueError) as exc:
        print(json.dumps({"state":"ERROR", "error":str(exc)}, indent=2), file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
