"""Behavioral contract validation and deterministic skill scaffolding."""
from __future__ import annotations
from pathlib import Path
import hashlib
import yaml
from .common import UniSkillError, slug, digest_tree

# ---------------------------------------------------------------------------
# Contract model – typed / validated
# ---------------------------------------------------------------------------

_REQUIRED_TOP = ("skill", "goal")
_REQUIRED_LISTS = ("triggers.positive", "triggers.negative",
                   "preconditions", "workflow", "outputs",
                   "invariants", "acceptance_tests")
_REQ_CAPS = ("capabilities.required", "capabilities.optional")


def _validate_contract(data: dict) -> None:
    """Raise UniSkillError with a precise message for every violation."""
    # Top-level fields
    for k in _REQUIRED_TOP:
        if k not in data:
            raise UniSkillError(f"Missing required field: '{k}'")

    skill = data.get("skill")
    if not slug(skill):
        raise UniSkillError(
            "Field 'skill' must be a valid slug (lowercase a-z/0-9/hyphen, 1-64 chars)"
        )

    # Goal
    goal = data.get("goal")
    if isinstance(goal, dict):
        goal = goal.get("text")
    if not isinstance(goal, str) or not goal.strip():
        raise UniSkillError("Field 'goal' must be a non-empty string")
    data["goal"] = goal.strip()

    # Triggers – MUST have at least one positive trigger
    triggers = data.get("triggers")
    if not isinstance(triggers, dict):
        raise UniSkillError("Field 'triggers' must be a mapping")
    for name in ("positive", "negative"):
        values = triggers.get(name, [])
        if not isinstance(values, list) or any(
            not isinstance(x, str) for x in values
        ):
            raise UniSkillError(
                f"triggers.{name} must be a list of strings"
            )
    if not triggers.get("positive"):
        raise UniSkillError(
            "triggers.positive must contain at least one entry"
        )

    # Capability classification
    caps = data.get("capabilities", {"required": [], "optional": []})
    if not isinstance(caps, dict):
        raise UniSkillError("capabilities must be a mapping")
    for ck in ("required", "optional"):
        vals = caps.get(ck, [])
        if not isinstance(vals, list) or any(
            not isinstance(x, str) for x in vals
        ):
            raise UniSkillError(
                f"capabilities.{ck} must be a list of strings"
            )
    if not caps.get("required"):
        raise UniSkillError(
            "capabilities.required must contain at least one entry"
        )
    data["capabilities"] = caps

    # List fields
    for key in ("preconditions", "workflow", "outputs",
                 "invariants", "acceptance_tests"):
        vals = data.get(key, [])
        if not isinstance(vals, list) or any(
            not isinstance(x, str) for x in vals
        ):
            raise UniSkillError(f"'{key}' must be a list of strings")

# ---------------------------------------------------------------------------
# load_contract – keep existing behaviour + stricter validation
# ---------------------------------------------------------------------------

def load_contract(path):
    """Load a YAML contract from *path*, normalise goal to a plain string,
    and return the dict.  On invalid input the function raises UniSkillError
    with a precise per-field message.

    This function is a **thin loader** – it does NOT invoke methodology
    selection or candidate generation.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UniSkillError(f"Cannot read contract file: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise UniSkillError(f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise UniSkillError("Contract YAML must be a mapping")
    _validate_contract(data)
    return data


# ---------------------------------------------------------------------------
# generate_skill – thin deterministic convenience wrapper
# ---------------------------------------------------------------------------

def generate_skill(data, out):
    """Generate a SKILL.md in *out* from *data*.

    *out* must not exist or be empty.  If it already contains files the call
    raises UniSkillError.  This is a **thin wrapper** – it writes the current
    SKILL.md format and is preserved for backward compatibility.
    """
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise UniSkillError(f"Refusing overwrite: {out}")
    out.mkdir(parents=True, exist_ok=True)
    pos = data.get("triggers", {}).get("positive", [])
    neg = data.get("triggers", {}).get("negative", [])
    desc = (
        f"{data['goal'].rstrip('.')} . Use when: "
        + "; ".join(pos[:4] or [data["skill"]])
    )
    desc = desc.replace(" .", ".")[:1024]
    fm = {"name": data["skill"], "description": desc,
          "metadata": {"uniskill/version": "0.2.0"}}
    content = ("---\n"
               + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
               + "\n---\n")
    content += (
        f"\n# {data['skill'].replace('-', ' ').title()}\n\n"
        f"## Objective\n\n{data['goal']}\n"
    )
    for title, items in (
        ("When to use", pos),
        ("When not to use", neg),
        ("Preconditions", data.get("preconditions", [])),
        ("Workflow", data.get("workflow", [])),
        ("Required outputs", data.get("outputs", [])),
        ("Non-negotiable invariants", data.get("invariants", [])),
        ("Acceptance criteria", data.get("acceptance_tests", [])),
    ):
        content += "\n## " + title + "\n\n"
        content += (
            ("\n".join("- " + x for x in items) if items else "Not specified.")
            + "\n"
        )
    content += "\nNever claim runtime verification without observable evidence.\n"
    (out / "SKILL.md").write_text(content, encoding="utf-8")
    (out / "uniskill.contract.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    return out


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _dir_digest(path):
    """SHA-256 of all files under *path* (uses common.digest_tree)."""
    return digest_tree(path)


# ---------------------------------------------------------------------------
# engine_skill – orchestrator
# ---------------------------------------------------------------------------

def engine_skill(contract, out_dir, methodology_path=None,
                 source_path=None):
    """Execute the full five-stage pipeline.

    Parameters
    ----------
    contract : dict
        Normalised contract dict (output of load_contract).
    out_dir : str | Path
        Output directory for candidates.
    methodology_path : str | Path | None
        Path to canonical-principles.yaml.
    source_path : str | Path | None
        Path to source-registry.yaml.

    Returns
    -------
    dict  Full execution trace with keys:
          stage_contract, stage_methodology, stage_candidates,
          stage_evaluation, stage_selection
    """
    from .methodology import (
        load_methodology,
        select_methodology,
        generate_candidates,
        evaluate_candidates,
        select_candidate,
        baseline_skill,
    )

    out_dir = Path(out_dir)

    trace = {
        "stage_contract": "ok",
    }

    # Stage 1 – contract is already validated by load_contract
    trace["contract_sha"] = hashlib.sha256(
        yaml.safe_dump(contract, sort_keys=True).encode()
    ).hexdigest()[:12]

    # Stage 2 – methodology
    meta = load_methodology(methodology_path)
    selected = select_methodology(contract, meta, source_path)
    trace["stage_methodology"] = selected

    # Stage 3 – candidates
    cands = generate_candidates(contract, out_dir, selected)
    trace["stage_candidates"] = cands

    # Stage 4 – evaluate
    bl = baseline_skill(contract)
    evals = evaluate_candidates(contract, cands["candidates"], bl)
    trace["stage_evaluation"] = evals

    # Stage 5 – select
    sel = select_candidate(evals)
    trace["stage_selection"] = sel

    return trace
