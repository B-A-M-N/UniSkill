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
        result = {"skill":args.skill, "receipt_id":receipt["receipt_id"],
                  "deployment":verified, "targets":{}}
        for h in receipt["targets"]:
            target = {"installation":verified["state"], "discovery":"UNVERIFIED",
                      "explicit_invocation":"UNVERIFIED", "automatic_selection":"UNVERIFIED",
                      "functional":"UNVERIFIED"}
            if cmd == "verify" and args.probe and verified["state"] == "INSTALLED":
                target["discovery_evidence"] = native_discovery(h, args.skill, args.workspace.resolve())
                target["discovery"] = target["discovery_evidence"]["state"]
            if cmd == "doctor" and h in ("opencode", "crush"):
                inspect = opencode_diagnostics if h == "opencode" else crush_diagnostics
                target["policy_diagnostics"] = inspect(
                    args.skill, args.workspace.resolve(), args.home.resolve())
                if target["policy_diagnostics"]:
                    target["diagnosis"] = "REVIEW_POLICY; never auto-weaken permissions"
            result["targets"][h] = target
        result["state"] = "INSTALLED" if verified["state"] == "INSTALLED" else "FAILED"
        result["note"] = "Filesystem proof is not native invocation/functional proof"
        return result
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
