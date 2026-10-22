"""Methodology selection, candidate generation, evaluation and selection.

This module implements the deterministic pipeline for turning a behavioural
contract into a skill artifact.  Every stage returns structured results that
inspect callers can inspect, debug, or audit.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import yaml

from .common import UniSkillError, digest_tree

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PRINCIPLES_PATH = Path(__file__).resolve().parent.parent.parent / \
    "methodology" / "canonical-principles.yaml"
DEFAULT_SOURCES_PATH = Path(__file__).resolve().parent.parent.parent / \
    "sources" / "source-registry.yaml"


# ---------------------------------------------------------------------------
# Stage 2 – Methodology selection
# ---------------------------------------------------------------------------

def load_methodology(path=None):
    """Load the canonical-principles YAML into a structured dict.

    Returns
    -------
    dict with keys: version, classification_vocabulary, principles, sources
    """
    if path is None:
        path = DEFAULT_PRINCIPLES_PATH
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise UniSkillError("canonical-principles.yaml must be a mapping")
    return {
        "version": data.get("version", "unknown"),
        "classification_vocabulary": data.get(
            "classification_vocabulary", []
        ),
        "principles": data.get("principles", []),
        # Load sources eagerly so select_methodology can annotate
        "sources": _load_sources(path.parent.parent / "sources" /
                                 "source-registry.yaml"),
    }


def _load_sources(path):
    """Load source-registry.yaml and return {id: meta} dict."""
    if not Path(path).exists():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return data.get("sources", {})


def select_methodology(contract, methodology=None, source_path=None):
    """Select principles that actually apply to *contract*.

    Each principle is matched against contract signals:
      - triggers.positive       → method-selection, behavioral-contract-first
      - triggers.negative       → needs-negative-triggers
      - capabilities.required   → minimal-instruction
      - capabilities.optional   → method-selection (orchestration)
      - invariants              → enforcement-boundary, needs-deterministic-enforcement
      - workflow                → method-selection, progressive-disclosure
      - acceptance_tests        → evaluation-before-expansion
      - preconditions           → enforcement-boundary
      - outputs                 → evaluation-before-expansion
      - goal contains "reference" / "reference/" or has a long description
        → progressive-disclosure
      - capabilities references scripting/shell/tool-use
        → method-selection

    Returns
    -------
    dict with:
      version, selected, excluded, source_review_dates
    """
    if methodology is None:
        methodology = load_methodology()

    sources = methodology.get("sources", {})
    principles = methodology.get("principles", [])

    # Contract signals
    pos_triggers = contract.get("triggers", {}).get("positive", [])
    neg_triggers = contract.get("triggers", {}).get("negative", [])
    required_caps = contract.get("capabilities", {}).get("required", [])
    optional_caps = contract.get("capabilities", {}).get("optional", [])
    invariants = contract.get("invariants", [])
    workflow = contract.get("workflow", [])
    acceptance = contract.get("acceptance_tests", [])
    preconditions = contract.get("preconditions", [])
    outputs = contract.get("outputs", [])
    goal_text = contract.get("goal", "")

    # --- New matching rules ---

    selected = []
    excluded = []
    source_review_dates = {}

    def _sources_for(p):
        """Merge source ids and review dates for a principle."""
        ids = list(p.get("sources", []))
        for sid in ids:
            meta = sources.get(sid, {})
            rv = meta.get("reviewed")
            if rv:
                source_review_dates[sid] = rv
        return ids

    def _add(princ, reason):
        selected.append({
            "id": princ["id"],
            "class": princ.get("class", "general-best-practice"),
            "rule": princ["rule"],
            "sources": _sources_for(princ),
            "reason": reason,
        })

    for p in principles:
        pid = p["id"]
        reason = None

        # Existing principle matches
        if pid == "behavioral-contract-first":
            reason = "Contract defines goals, triggers, invariants and observables"
        elif pid == "minimal-instruction":
            if required_caps:
                reason = "Required capabilities listed; scope is bounded"
        elif pid == "progressive-disclosure":
            if "references" in goal_text.lower() or "/references" in goal_text:
                reason = "Goal mentions references; supporting material should be split"
            else:
                reason = "Standard practice for skill organisation"
        elif pid == "degrees-of-freedom":
            reason = "Deterministic checks recommended where operations are fragile"
        elif pid == "method-selection":
            if optional_caps or workflow:
                reason = "Optional capabilities / workflow pattern present; orchestration matters"
            else:
                reason = "General principle applies to skill composition"
        elif pid == "evaluation-before-expansion":
            if acceptance or outputs:
                reason = "Acceptance tests and outputs defined; baseline comparison warranted"
            else:
                reason = "Standard evaluation practice"
        elif pid == "enforcement-boundary":
            if invariants:
                reason = "Invariants require non-model enforcement mechanisms"
            else:
                reason = "General policy discipline"
        elif pid == "no-success-shortcut":
            reason = "Filesystem install is not proof of discovery/invocation"
        elif pid == "permission-fence":
            reason = "Permission policies must not be weakened silently"

        # --- New principle triggers ---
        elif pid == "needs-deterministic-enforcement":
            if invariants:
                reason = f"{len(invariants)} invariants defined; deterministic check/verification step required"
        elif pid == "needs-negative-triggers":
            if neg_triggers:
                reason = (
                    f"{len(neg_triggers)} negative trigger(s) defined; "
                    "near-miss rejection must steer selection away from the skill"
                )
        elif pid == "needs-progressive-disclosure":
            if "reference" in goal_text.lower() or any(
                "reference" in c.lower() for c in required_caps + optional_caps
            ):
                reason = "References or resources referenced; progressive disclosure needed"

        if reason:
            _add(p, reason)
        else:
            excluded.append({
                "id": pid,
                "class": p.get("class", "general-best-practice"),
                "rule": p["rule"],
                "reason": "No contract signal matched this principle",
            })

    # Add the new principles that may not exist in the YAML yet –
    # we register them if they have a contract signal
    new_rules = {
        "needs-deterministic-enforcement": {
            "class": "conditional-technique",
            "rule": (
                "When a contract contains invariants or required deterministic "
                "steps, include a deterministic check/verification step in the "
                "generated skill."
            ),
            "sources": ["anthropic-best-practices", "vscode-customization"],
        },
        "needs-negative-triggers": {
            "class": "conditional-technique",
            "rule": (
                "When a contract defines negative triggers, the generated skill "
                "must surface near-miss rejection patterns that steer the harness "
                "away from invoking the skill for unrelated requests."
            ),
            "sources": ["anthropic-best-practices"],
        },
        "needs-progressive-disclosure": {
            "class": "standard-requirement",
            "rule": (
                "When a skill carries references or supporting resources, "
                "main SKILL.md should reference them rather than embedding them."
            ),
            "sources": ["anthropic-best-practices", "agent-skills-standard"],
        },
    }

    for pid, rule_def in new_rules.items():
        # Skip if already added above
        if any(s["id"] == pid for s in selected):
            continue
        reason = None
        if pid == "needs-deterministic-enforcement" and invariants:
            reason = (
                f"{len(invariants)} invariants defined; deterministic enforcement required"
            )
        elif pid == "needs-negative-triggers" and neg_triggers:
            reason = (
                f"{len(neg_triggers)} negative trigger(s) present; "
                "near-miss rejection pattern required"
            )
        elif pid == "needs-progressive-disclosure":
            if "reference" in goal_text.lower() or any(
                "reference" in c.lower()
                for c in required_caps + optional_caps
            ):
                reason = "References present; progressive disclosure warranted"
        if reason:
            _add({"id": pid, **rule_def}, reason)
        else:
            # Only add to excluded if there was a signal but not enough to select
            pass  # don't clutter excluded with "not met" new principles

    return {
        "version": methodology.get("version", "unknown"),
        "selected": selected,
        "excluded": excluded,
        "source_review_dates": source_review_dates,
    }


# ---------------------------------------------------------------------------
# Stage 3 – Candidate generation
# ---------------------------------------------------------------------------

def _cap_items(contract):
    """Format required + optional capabilities as bullet items."""
    items = []
    for cap in contract.get("capabilities", {}).get("required", []):
        items.append(f"[required] {cap}")
    for cap in contract.get("capabilities", {}).get("optional", []):
        items.append(f"[optional] {cap}")
    return items or ["Not specified."]


def _generate_minimal(contract):
    """Minimal candidate: basic SKILL.md from contract fields (no extras)."""
    pos = contract.get("triggers", {}).get("positive", [])
    neg = contract.get("triggers", {}).get("negative", [])
    desc = (
        f"{contract['goal'].rstrip('.')} . Use when: "
        + "; ".join(pos[:4])
    )
    desc = desc.replace(" .", ".")[:1024]
    fm = {
        "name": contract["skill"],
        "description": desc,
        "metadata": {"uniskill/version": "0.2.0"},
    }
    content = ("---\n"
               + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
               + "\n---\n")
    content += (
        f"\n# {contract['skill'].replace('-', ' ').title()}\n\n"
        f"## Objective\n\n{contract['goal']}\n"
    )
    for title, items in (
        ("When to use", pos),
        ("When not to use", neg),
        ("Required capabilities", _cap_items(contract)),
        ("Preconditions", contract.get("preconditions", [])),
        ("Workflow", contract.get("workflow", [])),
        ("Required outputs", contract.get("outputs", [])),
        ("Non-negotiable invariants", contract.get("invariants", [])),
        ("Acceptance criteria", contract.get("acceptance_tests", [])),
    ):
        content += "\n## " + title + "\n\n"
        content += (
            ("\n".join("- " + x for x in items) if items else "Not specified.")
            + "\n"
        )
    content += "\nNever claim runtime verification without observable evidence.\n"
    return content


def _generate_structured(contract):
    """Structured candidate: adds sections for methodology references,
    evaluation strategy, and clear enforcement boundaries."""
    minimal = _generate_minimal(contract)

    # Append extra structured sections
    extra = "\n## Methodology References\n\n"
    extra += "This skill follows the UniSkill evidence-driven methodology.\n\n"
    extra += "## Enforcement Boundaries\n\n"
    for inv in contract.get("invariants", []):
        extra += f"- **Invariant**: {inv}\n"
    if not contract.get("invariants"):
        extra += "No explicit invariants defined.\n"
    extra += "\n## Evaluation Strategy\n\n"
    extra += "This skill should be validated against its acceptance criteria\n"
    extra += "before deployment. Each acceptance test must be verified\n"
    extra += "with observable evidence, not merely referenced.\n"

    return minimal + extra


def _generate_deterministic_assisted(contract):
    """Deterministic-assisted candidate: adds a verification/check step
    justified by the contract's invariants or capabilities.

    The deterministic step is present even if the contract doesn't warrant it,
    but the justification field will reflect the actual situation.
    """
    minimal = _generate_minimal(contract)
    invariants = contract.get("invariants", [])
    required_caps = contract.get("capabilities", {}).get("required", [])

    # Check if the contract actually warrants deterministic enforcement
    warrants = bool(invariants) or any(
        c.lower() in ("shell", "test.execution", "file.write",
                       "code.modify", "deploy")
        for c in required_caps
    )

    deterministic_step = "\n## Deterministic Verification Step\n\n"
    if warrants:
        deterministic_step += (
            "This skill includes a **deterministic verification step**\n"
            "justified by the contract's invariants and/or required capabilities.\n\n"
        )
        if invariants:
            deterministic_step += (
                "### Invariant Check Script\n\n"
                "Before any operation, verify:\n\n"
            )
            for inv in invariants:
                deterministic_step += f"1. `{inv}`\n"
            deterministic_step += (
                "\nIf any invariant check fails, abort the operation.\n"
            )
        if any(
            c.lower() in ("shell", "test.execution")
            for c in required_caps
        ):
            deterministic_step += (
                "\n### Capability Verification\n\n"
                "Verify that required tools (shell, test.execution)\n"
                "are available before proceeding. Log availability status.\n"
            )
    else:
        deterministic_step += (
            "No deterministic verification step is warranted by this contract.\n"
            "The contract lacks invariants and required capabilities that\n"
            "imply deterministic enforcement.  This candidate still includes\n"
            "the scaffold for completeness but marks justification as **not-met**.\n\n"
        )
        deterministic_step += "**Justification: not-met** – contract has no\n"
        deterministic_step += "invariants and no required capabilities that imply\n"
        deterministic_step += "deterministic steps (e.g. shell, test.execution, file.write).\n"

    return minimal + deterministic_step


def generate_candidates(contract, out_dir, methodology_selection=None):
    """Generate at least three distinct candidates.

    Parameters
    ----------
    contract : dict
    out_dir : str | Path
    methodology_selection : dict or None

    Returns
    -------
    list of candidate dicts:
      {name, dir, digest, justification, satisfied_requirements,
       unmet_requirements}
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = []

    for name, gen_fn in (
        ("minimal", _generate_minimal),
        ("structured", _generate_structured),
        ("deterministic-assisted", _generate_deterministic_assisted),
    ):
        cand_dir = out_dir / name
        if cand_dir.exists() and any(cand_dir.iterdir()):
            raise UniSkillError(f"Refusing overwrite: {cand_dir}")
        cand_dir.mkdir(parents=True, exist_ok=True)

        content = gen_fn(contract)
        (cand_dir / "SKILL.md").write_text(content, encoding="utf-8")

        # Compute satisfied / unmet requirements
        sat, unmet = _check_requirements(contract, name, content)

        candidates.append({
            "name": name,
            "dir": str(cand_dir),
            "digest": digest_tree(cand_dir),
            "justification": _candidate_justification(name, contract,
                                                       methodology_selection),
            "satisfied_requirements": sat,
            "unmet_requirements": unmet,
        })

    return {"candidates": candidates}


def _check_requirements(contract, name, content):
    """Check which contract requirements are visibly satisfied in the content.

    Returns (satisfied_list, unmet_list).
    """
    sat = []
    unmet = []

    # Check required capabilities appear as text
    for cap in contract.get("capabilities", {}).get("required", []):
        if cap.lower() in content.lower():
            sat.append(f"capability:{cap}")
        else:
            unmet.append(f"capability:{cap}")

    # Check invariants
    for inv in contract.get("invariants", []):
        if inv.lower() in content.lower():
            sat.append(f"invariant:{inv[:40]}")
        else:
            unmet.append(f"invariant:{inv[:40]}")

    # Check acceptance tests
    for at in contract.get("acceptance_tests", []):
        if at.lower() in content.lower():
            sat.append(f"acceptance:{at[:40]}")
        else:
            unmet.append(f"acceptance:{at[:40]}")

    # Check triggers
    for t in contract.get("triggers", {}).get("positive", []):
        if t.lower() in content.lower():
            sat.append(f"trigger_pos:{t[:30]}")
    for t in contract.get("triggers", {}).get("negative", []):
        if t.lower() in content.lower():
            sat.append(f"trigger_neg:{t[:30]}")

    return sat, unmet


def _candidate_justification(name, contract, methodology):
    """Return a human-readable justification string for the candidate."""
    if name == "minimal":
        return "Basic contract-to-markdown scaffold with no additional structure."
    if name == "structured":
        return (
            "Adds methodology references, enforcement boundaries, and an "
            "evaluation strategy section."
        )
    if name == "deterministic-assisted":
        invariants = contract.get("invariants", [])
        caps = contract.get("capabilities", {}).get("required", [])
        warrants = bool(invariants) or any(
            c.lower() in ("shell", "test.execution", "file.write",
                           "code.modify", "deploy")
            for c in caps
        )
        if warrants:
            return (
                "Includes a deterministic verification step justified by "
                f"{len(invariants)} invariant(s) and/or required capabilities."
            )
        return (
            "Deterministic verification scaffold present but justification "
            "is not-met (no invariants or deterministic-capable capabilities)."
        )
    return ""


# ---------------------------------------------------------------------------
# Stage 4 – Comparative evaluation
# ---------------------------------------------------------------------------

def baseline_skill(contract):
    """Return the no-skill baseline description.

    The baseline represents what the agent does without a skill – it has
    no structure, no triggers, no invariants, and no evaluation strategy.
    """
    return {
        "name": "_baseline_",
        "dir": "",
        "digest": "",
        "content": "",
        "description": (
            "No-skill baseline: the agent handles the request using only "
            "general instructions with no skill scaffold."
        ),
    }


def evaluate_candidates(contract, candidates, baseline=None):
    """Evaluate every candidate against the baseline and the contract.

    Each finding is labelled with an evidence type:
      - "static-structure" – textual/structural analysis of the artifact
      - "contract-coverage" – does the artifact contain contract elements?
      - "size-bloat" – does the artifact exceed a reasonable size?
      - "enforcement-boundary" – does the artifact claim enforcement via
        instructions where the contract marks something as a hard invariant?
      - "determinism-freedom" – degrees-of-freedom analysis

    Returns a list of evaluation dicts, one per candidate.
    """
    bl = baseline or baseline_skill(contract)
    results = []

    for cand in candidates:
        cand_dir = Path(cand["dir"])
        skill_md = cand_dir / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8") if skill_md.exists() else ""
        content_lower = content.lower()

        findings = []
        verification_class = "static-structural-only"

        # 1. Contract coverage – does every required capability, invariant,
        #    acceptance test appear in the artifact?
        coverage_pass = True
        for cap in contract.get("capabilities", {}).get("required", []):
            if cap.lower() not in content_lower:
                findings.append({
                    "check": "required-capability",
                    "target": cap,
                    "passed": False,
                    "evidence_type": "contract-coverage",
                    "detail": (
                        f"Required capability '{cap}' not found in SKILL.md"
                    ),
                })
                coverage_pass = False
            else:
                findings.append({
                    "check": "required-capability",
                    "target": cap,
                    "passed": True,
                    "evidence_type": "contract-coverage",
                })

        for inv in contract.get("invariants", []):
            if inv.lower() not in content_lower:
                findings.append({
                    "check": "invariant-present",
                    "target": inv[:50],
                    "passed": False,
                    "evidence_type": "contract-coverage",
                    "detail": f"Invariant not present: {inv[:50]}",
                })
                coverage_pass = False
            else:
                findings.append({
                    "check": "invariant-present",
                    "target": inv[:50],
                    "passed": True,
                    "evidence_type": "contract-coverage",
                })

        for at in contract.get("acceptance_tests", []):
            if at.lower() not in content_lower:
                findings.append({
                    "check": "acceptance-test-present",
                    "target": at[:50],
                    "passed": False,
                    "evidence_type": "contract-coverage",
                    "detail": f"Acceptance test not present: {at[:50]}",
                })
                coverage_pass = False
            else:
                findings.append({
                    "check": "acceptance-test-present",
                    "target": at[:50],
                    "passed": True,
                    "evidence_type": "contract-coverage",
                })

        # 2. Trigger quality – positive triggers in description,
        #    negative triggers in a steer-away section
        pos_triggers = contract.get("triggers", {}).get("positive", [])
        neg_triggers = contract.get("triggers", {}).get("negative", [])

        has_pos_in_desc = bool(pos_triggers) and (
            "when to use" in content_lower or pos_triggers[0].lower() in content_lower
        )
        findings.append({
            "check": "positive-trigger-in-description",
            "passed": has_pos_in_desc,
            "evidence_type": "static-structure",
            "detail": (
                f"Positive triggers {'present in description' if has_pos_in_desc else 'missing from description'}"
            ),
        })

        has_neg_in_section = bool(neg_triggers) and (
            "when not to use" in content_lower or neg_triggers[0].lower() in content_lower
        )
        findings.append({
            "check": "negative-trigger-steer-away",
            "passed": has_neg_in_section,
            "evidence_type": "static-structure",
            "detail": (
                f"Negative triggers {'present in steer-away section' if has_neg_in_section else 'missing from steer-away section'}"
            ),
        })

        # 3. Description length and format legality
        fm_len = len(cand.get("description", "")) if "description" in cand else len(content[:200])
        findings.append({
            "check": "description-length",
            "passed": 1 <= len(content) <= 40000,
            "evidence_type": "static-structure",
            "detail": f"SKILL.md is {len(content)} chars (max 40000)",
        })

        # 4. Size / bloat – check if the candidate is unreasonably large
        #    relative to what's necessary
        expected_min_sections = 5  # name, desc, objective, when-to-use, when-not-to-use
        actual_sections = content.count("\n## ")
        size_pass = actual_sections >= expected_min_sections and len(content) < 30000
        findings.append({
            "check": "size-bloat",
            "passed": size_pass,
            "evidence_type": "static-structure",
            "detail": (
                f"{actual_sections} sections, {len(content)} chars"
                f" (reasonable range: {expected_min_sections}+ sections, <30k chars)"
            ),
        })

        # 5. Determinism vs freedom – degrees-of-freedom principle
        has_det = "deterministic" in content_lower or "verification" in content_lower
        inv_count = len(contract.get("invariants", []))
        # If invariants exist, we expect at least some deterministic element
        det_balance = True
        if inv_count > 0 and not has_det:
            det_balance = False
        findings.append({
            "check": "determinism-freedom-balance",
            "passed": det_balance,
            "evidence_type": "static-structure",
            "detail": (
                ("Deterministic step present" if has_det else "No deterministic step")
                + "; " + str(inv_count) + " invariant(s) in contract"
            ),
        })

        # 6. Enforcement-boundary violation: if a contract invariant says
        #    "no X" but the skill uses instruction text like "make sure X doesn't happen"
        #    without actual enforcement scaffolding → flag
        for inv in contract.get("invariants", []):
            inv_lower = inv.lower()
            if "no " in inv_lower or "never " in inv_lower or "must not" in inv_lower:
                # Check if the skill just says "ensure that..." in instruction
                # text without a dedicated enforcement section
                has_enforcement_section = any(
                    kw in content_lower
                    for kw in ["enforcement", "verification step", "check script",
                               "deterministic"]
                )
                if not has_enforcement_section:
                    findings.append({
                        "check": "enforcement-boundary",
                        "passed": False,
                        "evidence_type": "enforcement-boundary",
                        "detail": (
                            f"Invariant '{inv[:50]}' appears as instruction text "
                            "without dedicated enforcement/verification section"
                        ),
                    })
                else:
                    findings.append({
                        "check": "enforcement-boundary",
                        "passed": True,
                        "evidence_type": "enforcement-boundary",
                        "detail": f"Enforcement section present for invariant '{inv[:50]}'",
                    })

        # 7. Baseline comparison
        bl_content = bl.get("content", "") if isinstance(bl, dict) else ""
        baseline_comparison = len(content) > len(bl_content) if bl_content else True
        findings.append({
            "check": "baseline-improvement",
            "passed": baseline_comparison,
            "evidence_type": "contract-coverage",
            "detail": (
                f"Candidate is {len(content)} chars vs baseline {len(bl_content)} chars"
            ),
        })

        overall_pass = coverage_pass and det_balance
        # Count how many critical checks passed
        critical_checks = [
            f for f in findings
            if f["check"] in (
                "required-capability", "invariant-present",
                "acceptance-test-present", "determinism-freedom-balance",
            ) and not f["passed"]
        ]
        critical_pass = len(critical_checks) == 0

        results.append({
            "candidate_name": cand["name"],
            "candidate_dir": cand["dir"],
            "candidate_digest": cand["digest"],
            "findings": findings,
            "coverage_pass": coverage_pass,
            "critical_pass": critical_pass,
            "verification_class": verification_class,
            "overall_pass": overall_pass,
        })

    return results


# ---------------------------------------------------------------------------
# Stage 5 – Selection
# ---------------------------------------------------------------------------

def select_candidate(evaluations):
    """Pick the SMALLEST candidate that satisfies every mandatory requirement.

    Mandatory requirements = coverage_pass AND critical_pass.

    Returns
    -------
    dict with:
      state: "SELECTED" or "FAILED"
      winner: candidate eval dict (if SELECTED)
      reason: str
      runner_up: candidate eval dict (if SELECTED and exists)
      unmet_requirements: list (if FAILED)
    """
    qualified = [
        e for e in evaluations
        if e["coverage_pass"] and e["critical_pass"]
    ]

    if not qualified:
        # No candidate meets mandatory requirements
        all_unmet = set()
        for e in evaluations:
            for f in e["findings"]:
                if not f["passed"] and f["check"] in (
                    "required-capability", "invariant-present",
                    "acceptance-test-present", "determinism-freedom-balance",
                ):
                    all_unmet.add(f"{f['check']}:{f.get('target', f.get('detail', ''))[:60]}")
        return {
            "state": "FAILED",
            "winner": None,
            "reason": "No candidate satisfies all mandatory requirements",
            "runner_up": None,
            "unmet_requirements": sorted(all_unmet),
        }

    # Pick smallest by content length (proxy for minimal instructions)
    def _size(e):
        cand_dir = Path(e["candidate_dir"])
        skill_md = cand_dir / "SKILL.md"
        if skill_md.exists():
            return skill_md.stat().st_size
        return 999999999

    qualified.sort(key=_size)
    winner = qualified[0]

    # Runner-up: the next smallest that also passed
    runner_up = qualified[1] if len(qualified) > 1 else None

    reason = (
        f"Smallest candidate that satisfies all mandatory requirements. "
        f"File size: {_size(winner)} bytes."
    )
    if runner_up:
        reason += (
            f" Runner-up '{runner_up['candidate_name']}' "
            f"({_size(runner_up)} bytes) was larger."
        )

    return {
        "state": "SELECTED",
        "winner": winner,
        "reason": reason,
        "runner_up": runner_up,
    }
