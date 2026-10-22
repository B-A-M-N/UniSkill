"""Verification of documentation-grounded adapter fixes (2026-10-21).

Fix 1: Kimi Code extended frontmatter uses documented fields only.
Fix 2: Crush manual-only detection uses disable-model-invocation (not the
       fabricated manual-only: true), detects user-invocable, fails closed,
       and scans .crushrc.
Fix 3: PiAdapter respects PI_CODING_AGENT_DIR env override.
Fix 4: Shared frontmatter helpers work across camelCase, kebab, snake.
"""
import json
import os
from pathlib import Path

import pytest

from uniskill.adapters import (
    UNVERIFIED,
    frontmatter_manual_only,
    frontmatter_user_invocable,
    get_adapter,
)
from uniskill.common import UniSkillError
from uniskill.harnesses import HARNESSES


# ======================================================================
# Fix 1 — Kimi Code extended frontmatter
# ======================================================================

def test_kimi_adapt_emits_documented_fields_not_fabricated(tmp_path):
    """Kimi adapt() should list documented fields and NOT version/metadata."""
    ad = get_adapter("kimi-code-cli")
    result = ad.adapt(tmp_path)
    fields = result["fields"]
    # Documented (kimi.com/code/docs, 2026-10-21): name, description, type,
    # whenToUse, disableModelInvocation, arguments
    for f in ("name", "description", "type", "whenToUse",
              "disableModelInvocation", "arguments"):
        assert f in fields, f"Expected documented field {f!r} in adapt() fields"
    # Fabricated fields must be absent
    assert "version" not in fields, "version was fabricated for Kimi"
    assert "metadata" not in fields, "metadata was fabricated for Kimi"


# ======================================================================
# Fix 2 — Crush manual-only detection (the important one)
# ======================================================================

def _make_skill(root: Path, name: str, frontmatter: dict) -> Path:
    """Create a minimal SKILL.md under the first crush project root.

    Crush project roots are <root>/.crush/skills, <root>/.agents/skills, etc.
    We pick the first one to keep tests simple.
    """
    import yaml
    from uniskill.harnesses import roots as get_roots
    # Get the first project root for crush
    first_root = get_roots("crush", "project", root, root)[0]
    skill_dir = first_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    lines.append(yaml.dump(frontmatter, default_flow_style=False).strip())
    lines.append("---")
    lines.append("# Instructions\n")
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return skill_dir


def test_crush_disable_model_invocation_classifies_manual_only(tmp_path):
    """disable-model-invocation: true → MANUAL_ONLY."""
    ad = get_adapter("crush")
    _make_skill(tmp_path, "my-skill",
                {"name": "my-skill", "description": "x",
                 "disable-model-invocation": True})
    result = ad.skill_metadata("my-skill", tmp_path, tmp_path)
    assert result["metadata_class"] == "MANUAL_ONLY", \
        f"Expected MANUAL_ONLY, got {result['metadata_class']}"
    assert result["manual_only"] is True


def test_crush_disable_model_invocation_camel_case(tmp_path):
    """disableModelInvocation: true (kimi-style camelCase) → MANUAL_ONLY."""
    ad = get_adapter("crush")
    _make_skill(tmp_path, "my-skill",
                {"name": "my-skill", "description": "x",
                 "disableModelInvocation": True})
    result = ad.skill_metadata("my-skill", tmp_path, tmp_path)
    assert result["metadata_class"] == "MANUAL_ONLY"
    assert result["manual_only"] is True


def test_crush_disable_model_invocation_snake_case(tmp_path):
    """disable_model_invocation: true (snake_case) → MANUAL_ONLY."""
    ad = get_adapter("crush")
    _make_skill(tmp_path, "my-skill",
                {"name": "my-skill", "description": "x",
                 "disable_model_invocation": True})
    result = ad.skill_metadata("my-skill", tmp_path, tmp_path)
    assert result["metadata_class"] == "MANUAL_ONLY"
    assert result["manual_only"] is True


def test_crush_old_fabricated_manual_only_field_not_classified_manual_only(
    tmp_path
):
    """regression: 'manual-only: true' (old fabricated field) must NOT
    classify MANUAL_ONLY — it falls through to UNCLASSIFIED (fail-closed)
    because we found a skill directory but cannot classify it with documented
    markers."""
    ad = get_adapter("crush")
    _make_skill(tmp_path, "my-skill",
                {"name": "my-skill", "description": "x",
                 "manual-only": True})
    result = ad.skill_metadata("my-skill", tmp_path, tmp_path)
    assert result["metadata_class"] != "MANUAL_ONLY", \
        "Old fabricated field manual-only:true should not trigger MANUAL_ONLY"
    # Fail-closed: found a skill dir but no documented classification marker
    assert result["metadata_class"] == "UNCLASSIFIED"


def test_crush_user_invocable_detected(tmp_path):
    """user-invocable: true is detected as its own dimension."""
    ad = get_adapter("crush")
    _make_skill(tmp_path, "my-skill",
                {"name": "my-skill", "description": "x",
                 "user-invocable": True})
    result = ad.skill_metadata("my-skill", tmp_path, tmp_path)
    assert result["user_invocable"] is True


def test_crush_user_invocable_camel_case(tmp_path):
    """userInvocable: true (camelCase) also works."""
    ad = get_adapter("crush")
    _make_skill(tmp_path, "my-skill",
                {"name": "my-skill", "description": "x",
                 "userInvocable": True})
    result = ad.skill_metadata("my-skill", tmp_path, tmp_path)
    assert result["user_invocable"] is True


def test_crush_dot_crushrc_scanned_and_disable_detected(tmp_path):
    """.crushrc (leading dot) in workspace is scanned — a disable directive
    there is detected."""
    from uniskill.harnesses import roots as get_roots
    ad = get_adapter("crush")
    workspace = tmp_path
    workspace.mkdir(exist_ok=True)
    # Create .crushrc (leading dot) with a disable-skill directive
    (workspace / ".crushrc").write_text(
        "disable-skill dot-skill\n", encoding="utf-8")
    # Create the skill in the first crush project root
    skill_root = get_roots("crush", "project", workspace, workspace)[0]
    skill_dir = skill_root / "dot-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: dot-skill\ndescription: x\n---\n", encoding="utf-8")
    result = ad.skill_metadata("dot-skill", workspace, tmp_path)
    assert result["metadata_class"] == "DISABLED"
    assert result["disabled"] is True


def test_crush_fail_closed_unreadable_skill(tmp_path):
    """Unreadable/garbage SKILL.md must NOT yield MODEL_INVOCABLE_CANDIDATE."""
    from uniskill.harnesses import roots as get_roots
    ad = get_adapter("crush")
    workspace = tmp_path
    # Create garbage skill in the first crush project root
    skill_root = get_roots("crush", "project", workspace, workspace)[0]
    skill_dir = skill_root / "garbage-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    # Write garbage that is not valid YAML frontmatter
    (skill_dir / "SKILL.md").write_text("not valid frontmatter at all\n",
                                         encoding="utf-8")
    result = ad.skill_metadata("garbage-skill", workspace, tmp_path)
    # Fail-closed: should NOT claim MODEL_INVOCABLE_CANDIDATE
    assert result["metadata_class"] != "MODEL_INVOCABLE_CANDIDATE", \
        "Garbage SKILL.md should fail closed (UNCLASSIFIED)"


def test_crush_fail_closed_no_skill_md(tmp_path):
    """Missing SKILL.md must NOT yield MODEL_INVOCABLE_CANDIDATE."""
    from uniskill.harnesses import roots as get_roots
    ad = get_adapter("crush")
    workspace = tmp_path
    # Create the skill dir but NO SKILL.md
    skill_root = get_roots("crush", "project", workspace, workspace)[0]
    skill_dir = skill_root / "no-frontmatter"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "README.md").write_text("no skill here", encoding="utf-8")
    result = ad.skill_metadata("no-frontmatter", workspace, tmp_path)
    assert result["metadata_class"] != "MODEL_INVOCABLE_CANDIDATE", \
        "Missing SKILL.md should fail closed"


# ======================================================================
# Fix 3 — PiAgentDir env override
# ======================================================================

def test_pi_agent_dir_default_resolves_to_home_dot_pi_agent(tmp_path):
    """Default resolves to home/.pi/agent."""
    from uniskill.adapters import _pi_agent_dir
    result = _pi_agent_dir(tmp_path)
    assert str(result) == str(tmp_path / ".pi" / "agent")


def test_pi_agent_dir_env_override(tmp_path, monkeypatch):
    """PI_CODING_AGENT_DIR env override changes the user root."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "my-pi-dir"))
    from uniskill.adapters import _pi_agent_dir
    result = _pi_agent_dir(tmp_path)
    assert str(result) == str(tmp_path / "my-pi-dir")


def test_pi_adapter_roots_respect_env_override(tmp_path, monkeypatch):
    """PiAdapter.roots() user scope respects PI_CODING_AGENT_DIR."""
    ad = get_adapter("pi")
    # Default: ~/.pi/agent/skills
    user_root = ad.roots("user", tmp_path, tmp_path)
    assert str(tmp_path / ".pi" / "agent" / "skills") in [str(r) for r in user_root]
    # Override: $PI_CODING_AGENT_DIR/skills
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "custom-pi"))
    user_root = ad.roots("user", tmp_path, tmp_path)
    assert str(tmp_path / "custom-pi" / "skills") in [str(r) for r in user_root]


# ======================================================================
# Fix 4 — Shared frontmatter helpers
# ======================================================================

def test_frontmatter_manual_only_kimi_camel_case(tmp_path):
    """Kimi-style camelCase disableModelInvocation is recognized."""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\ndisableModelInvocation: true\n---\n# x\n", encoding="utf-8")
    assert frontmatter_manual_only(skill_dir) is True


def test_frontmatter_manual_only_crush_kebab_case(tmp_path):
    """Crush-style kebab-case disable-model-invocation is recognized."""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\ndisable-model-invocation: true\n---\n# x\n", encoding="utf-8")
    assert frontmatter_manual_only(skill_dir) is True


def test_frontmatter_manual_only_pi_snake_case(tmp_path):
    """Pi-style snake_case disable_model_invocation is recognized."""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\ndisable_model_invocation: true\n---\n# x\n", encoding="utf-8")
    assert frontmatter_manual_only(skill_dir) is True


def test_frontmatter_manual_not_present_returns_false(tmp_path):
    """When frontmatter exists but marker absent → False (not None)."""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: s\ndescription: x\n---\n# x\n", encoding="utf-8")
    assert frontmatter_manual_only(skill_dir) is False


def test_frontmatter_manual_malformed_returns_none(tmp_path):
    """Unparseable frontmatter → None."""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\n{invalid yaml\n---\n# x\n",
                                         encoding="utf-8")
    assert frontmatter_manual_only(skill_dir) is None


def test_frontmatter_user_invocable_kebab(tmp_path):
    """user-invocable kebab is recognized."""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nuser-invocable: true\n---\n# x\n", encoding="utf-8")
    assert frontmatter_user_invocable(skill_dir) is True


def test_frontmatter_user_invocable_not_present_returns_false(tmp_path):
    """When marker absent → False."""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: s\ndescription: x\n---\n# x\n", encoding="utf-8")
    assert frontmatter_user_invocable(skill_dir) is False


def test_frontmatter_user_invocable_malformed_returns_none(tmp_path):
    """Malformed → None."""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nbroken\n---\n# x\n",
                                         encoding="utf-8")
    assert frontmatter_user_invocable(skill_dir) is None


# ======================================================================
# Fix 5 — capability_profile consistency check
# ======================================================================

def test_capability_profile_values_consistent(tmp_path):
    """capability_profile uses Harness.support_class (all INSTALL_ONLY in
    harnesses.py). The actual support-class overrides live in
    adapters.SUPPORT_CLASS_OVERRIDES and are applied through
    adapters.support_class(). Both are consistent with documented facts."""
    from uniskill.harnesses import capability_profile
    from uniskill.adapters import support_class, SUPPORT_CLASS_OVERRIDES
    # capability_profile returns what's on the Harness dataclass
    assert capability_profile("codex")["support_class"] == "INSTALL_ONLY"
    # support_class() applies the overrides
    assert support_class("gemini") == "DISCOVERY_PROBED"
    assert support_class("kimi-code-cli") == "NATIVE_DOCUMENTED"
    assert support_class("crush") == "INSTALL_ONLY"
    assert support_class("pi") == "INSTALL_ONLY"
    assert support_class("hermes-agent") == "INSTALL_ONLY"
    assert support_class("claude-code") == "INSTALL_ONLY"
    # Custom profiles must be INSTALL_ONLY
    assert SUPPORT_CLASS_OVERRIDES["gemini"] == "DISCOVERY_PROBED"
    assert SUPPORT_CLASS_OVERRIDES["kimi-code-cli"] == "NATIVE_DOCUMENTED"
    assert SUPPORT_CLASS_OVERRIDES["crush"] == "INSTALL_ONLY"
