"""Behavioral contract validation and deterministic skill scaffolding."""
from __future__ import annotations
from pathlib import Path
import yaml
from .common import UniSkillError, slug

def load_contract(path):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not slug(data.get("skill")):
        raise UniSkillError("Contract must map a valid 'skill' name")
    goal = data.get("goal")
    if isinstance(goal, dict):
        goal = goal.get("text")
    if not isinstance(goal, str) or not goal.strip():
        raise UniSkillError("Contract needs nonempty 'goal' string")
    data["goal"] = goal.strip()
    triggers = data.get("triggers", {})
    if not isinstance(triggers, dict):
        raise UniSkillError("triggers must be mapping")
    for name in ("positive", "negative"):
        values = triggers.get(name, [])
        if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
            raise UniSkillError(f"triggers.{name} must be string list")
    for key in ("workflow", "preconditions", "outputs", "invariants", "acceptance_tests"):
        values = data.get(key, [])
        if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
            raise UniSkillError(f"{key} must be string list")
    caps = data.get("capabilities", {"required": [], "optional": []})
    if not isinstance(caps, dict) or any(not isinstance(caps.get(k, []), list) or any(
        not isinstance(x, str) for x in caps.get(k, [])) for k in ("required", "optional")):
        raise UniSkillError("capabilities.required and optional must be string lists")
    data["capabilities"] = caps
    return data

def generate_skill(data, out):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise UniSkillError(f"Refusing overwrite: {out}")
    out.mkdir(parents=True, exist_ok=True)
    pos = data.get("triggers", {}).get("positive", [])
    neg = data.get("triggers", {}).get("negative", [])
    desc = f"{data['goal'].rstrip('.')} . Use when: " + "; ".join(pos[:4] or [data["skill"]])
    desc = desc.replace(" .", ".")[:1024]
    fm = {"name": data["skill"], "description": desc,
          "metadata": {"uniskill/version": "0.2.0"}}
    content = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip() + "\n---\n"
    content += f"\n# {data['skill'].replace('-', ' ').title()}\n\n## Objective\n\n{data['goal']}\n"
    for title, items in (("When to use", pos), ("When not to use", neg),
                         ("Preconditions", data.get("preconditions", [])),
                         ("Workflow", data.get("workflow", [])),
                         ("Required outputs", data.get("outputs", [])),
                         ("Non-negotiable invariants", data.get("invariants", [])),
                         ("Acceptance criteria", data.get("acceptance_tests", []))):
        content += "\n## " + title + "\n\n" + ("\n".join("- " + x for x in items) if items else "Not specified.") + "\n"
    content += "\nNever claim runtime verification without observable evidence.\n"
    (out / "SKILL.md").write_text(content, encoding="utf-8")
    (out / "uniskill.contract.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out
