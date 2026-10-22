"""Portable structural validation. Never executes untrusted skill code."""
from __future__ import annotations
import re
from pathlib import Path
import yaml
from .common import UniSkillError, safe_tree, slug

ALLOWED = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}

def validate_skill(path):
    path = Path(path)
    errors, warnings, fm = [], [], {}
    try:
        files = safe_tree(path)
    except UniSkillError as exc:
        return {"state":"FAILED", "name":path.name, "errors":[str(exc)], "warnings":[]}
    if path / "SKILL.md" not in files:
        return {"state":"FAILED", "name":path.name, "errors":["SKILL.md missing"], "warnings":[]}
    raw = ""
    try:
        raw = (path / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", raw, re.DOTALL)
        if not match:
            errors.append("Missing YAML frontmatter delimited by ---")
        else:
            fm = yaml.safe_load(match.group(1))
            if not isinstance(fm, dict):
                errors.append("Frontmatter must be mapping")
                fm = {}
            if not match.group(2).strip():
                errors.append("Skill instructions empty")
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        errors.append(f"Invalid SKILL.md frontmatter: {exc}")
    name = fm.get("name")
    if not slug(name):
        errors.append("Invalid skill name (lowercase a-z/0-9/hyphen, 1-64, no --)")
    elif name != path.name:
        errors.append("Skill name must match directory name")
    description = fm.get("description")
    if not isinstance(description, str) or not (1 <= len(description.strip()) <= 1024):
        errors.append("description must be 1-1024 characters")
    if "compatibility" in fm and (not isinstance(fm["compatibility"], str) or
                                   not (1 <= len(fm["compatibility"]) <= 500)):
        errors.append("compatibility must be a 1-500 character string")
    if "metadata" in fm and (not isinstance(fm["metadata"], dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in fm["metadata"].items())):
        errors.append("metadata must map strings to strings")
    for key in ("license", "allowed-tools"):
        if key in fm and not isinstance(fm[key], str):
            errors.append(f"{key} must be string")
    extra = set(fm) - ALLOWED
    if extra:
        warnings.append(f"Nonportable top-level metadata fields: {sorted(extra)}")
    if len(raw) > 40000:
        warnings.append("Long SKILL.md; consider splitting into references")
    for link in re.findall(r"\]\(([^)]+)\)", raw):
        link = link.split("#")[0].split("?")[0]
        if not link or "://" in link or link.startswith("mailto:"):
            continue
        local = (path / link).resolve()
        if not local.is_relative_to(path.resolve()) or not local.exists():
            warnings.append(f"Broken/escaping resource reference: {link}")
    scripts = [str(p.relative_to(path)) for p in files if "scripts" in p.parts]
    if scripts:
        warnings.append("Script execution needs separate security review: " + ", ".join(scripts[:5]))
    return {"state":"VALID" if not errors else "FAILED", "name":name,
            "errors":errors, "warnings":warnings, "files":len(files)}
