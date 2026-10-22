"""Gate C: capability-aware adapter layer tests."""
import json
import os
from pathlib import Path

import pytest

from uniskill.adapters import (DISCOVERED, UNVERIFIED, adapter_table,
                               get_adapter, support_class)
from uniskill.common import UniSkillError
from uniskill.harnesses import HARNESSES, load_registry


def test_every_harness_has_honest_support_class():
    table = adapter_table()
    assert set(table) == set(HARNESSES)
    for name, cls in table.items():
        assert cls in {"INSTALL_ONLY", "DISCOVERY_PROBED",
                       "NATIVE_DOCUMENTED", "FUNCTIONAL_VERIFIED"}
    # No harness claims functional verification without a real probe.
    assert not any(cls == "FUNCTIONAL_VERIFIED" for cls in table.values())
    # Community-mapped harnesses default to INSTALL_ONLY, never more.
    assert table["cline"] == "INSTALL_ONLY"
    assert table["gemini"] == "DISCOVERY_PROBED"  # real probe exists


def test_capability_profile_exposes_support_class():
    from uniskill.harnesses import capability_profile
    assert capability_profile("codex")["support_class"] == "INSTALL_ONLY"


def test_jcode_roots_session_workspace_relative(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "session-workspace"
    ad = get_adapter("jcode")
    proj = ad.roots("project", workspace, home)
    assert [str(p) for p in proj] == [str(workspace / ".jcode/skills"),
                                      str(workspace / ".agents/skills"),
                                      str(workspace / ".claude/skills")]
    user = ad.roots("user", workspace, home)
    assert str(home / ".jcode/skills") in [str(p) for p in user]
    result = ad.detect()
    assert result["daemon_check"] == "NOT_PERFORMED"
    disc = ad.discover("some-skill", workspace, home)
    assert disc["state"] == "UNVERIFIED"
    assert "daemon" in disc["reason"]


def test_jcode_diagnose_is_readonly_and_states_no_daemon_check(tmp_path):
    result = get_adapter("jcode").diagnose("x", tmp_path, tmp_path)
    assert result["read_only"] is True
    assert "daemon" in result["note"]


def test_crush_crushrc_disable_and_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("CRUSH_SKILLS_DIR", raising=False)
    root, home = tmp_path / "repo", tmp_path / "home"
    home.mkdir()
    root.mkdir()
    # crush.json disables locked-skill
    (root / "crush.json").write_text(json.dumps({"options": {"disabled_skills": ["locked-skill"]}}))
    # disable-skill (documented, NOT disable-skill-model) in crushrc disables manual-skill
    (root / "crushrc").write_text("option disable-skill manual-skill\n")
    ad = get_adapter("crush")
    locked = ad.skill_metadata("locked-skill", root, home)
    assert locked["metadata_class"] == "DISABLED"
    manual = ad.skill_metadata("manual-skill", root, home)
    assert manual["metadata_class"] == "DISABLED"
    assert manual["disabled"] is True
    normal = ad.skill_metadata("other-skill", root, home)
    assert normal["metadata_class"] == "MODEL_INVOCABLE_CANDIDATE"


def test_crush_skills_dir_override_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUSH_SKILLS_DIR", "/custom/skills")
    result = get_adapter("crush").inspect(tmp_path, tmp_path)
    overrides = result["effective_path_overrides"]
    assert any(o["name"] == "CRUSH_SKILLS_DIR" for o in overrides)


def test_crush_crush_diagnostics_still_works(tmp_path):
    # Existing crush.json path still integrated via diagnostics reuse.
    from uniskill.harnesses import crush_diagnostics
    root = tmp_path / "repo"
    root.mkdir()
    (root / "crush.json").write_text(json.dumps({"options": {"disabled_skills": ["s"]}}))
    assert "disabled" in " ".join(crush_diagnostics("s", root, tmp_path))


def test_hermes_untrusted_copy_not_discovered(tmp_path):
    root, home = tmp_path / "proj", tmp_path / "home"
    home.mkdir()
    root.mkdir()
    skill = root / ".hermes/skills/demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n")
    ad = get_adapter("hermes-agent")
    result = ad.discover("demo", root, home)
    assert result["state"] != DISCOVERED
    assert result["state"] == UNVERIFIED
    assert "trust" in result["reason"] or "trust" in str(result.get("trust"))


def test_hermes_inspect_and_diagnose_surface_trust_quarantine_precedence(tmp_path):
    root, home = tmp_path / "proj", tmp_path / "home"
    home.mkdir()
    root.mkdir()
    (root / ".hermes").mkdir()
    (root / ".hermes/trust.json").write_text(json.dumps({"trusted": False}))
    (root / ".hermes/quarantine").mkdir()
    ad = get_adapter("hermes-agent")
    insp = ad.inspect(root, home)
    assert insp["trust"]["state"] == "UNTRUSTED"
    assert any(q["scope"] == "project" for q in insp["quarantine"]["quarantine_dirs"])
    assert insp["precedence"]
    diag = ad.diagnose("demo", root, home)
    assert any(s["stage"] == "trust" for s in diag["stages"])
    assert diag["trust"]["state"] == "UNTRUSTED"


def test_kimi_roots_and_env_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ad = get_adapter("kimi-code-cli")
    proj = [str(p) for p in ad.roots("project", workspace, home)]
    assert str(workspace / ".kimi-code/skills") in proj
    assert str(workspace / ".agents/skills") in proj
    # Default user home
    user = [str(p) for p in ad.roots("user", workspace, home)]
    assert str(home / ".kimi-code/skills") in user
    # Honors monkeypatched KIMI_CODE_HOME
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimihome"))
    user = [str(p) for p in ad.roots("user", workspace, home)]
    assert str(tmp_path / "kimihome" / "skills") in user
    # Registry entry extended, not orphaned
    assert HARNESSES["kimi-code-cli"].project == (".agents/skills", ".kimi-code/skills")


def test_kimi_invoke_and_adapt(tmp_path):
    ad = get_adapter("kimi-code-cli")
    inv = ad.invoke("demo")
    assert inv["command"] == "/skill:demo"
    assert inv["native"] is True
    assert inv["state"] == UNVERIFIED
    ad_pack = ad.adapt(tmp_path)
    # Documented Kimi Code CLI extended fields (kimi.com/code/docs, 2026-10-21)
    assert "name" in ad_pack["fields"]
    assert "type" in ad_pack["fields"]
    assert "whenToUse" in ad_pack["fields"]
    assert "disableModelInvocation" in ad_pack["fields"]
    assert "arguments" in ad_pack["fields"]
    # Fabricated fields must NOT be present
    assert "version" not in ad_pack["fields"]
    assert "metadata" not in ad_pack["fields"]


def test_pi_native_locations(tmp_path):
    ad = get_adapter("pi")
    proj = [str(p) for p in ad.roots("project", tmp_path, tmp_path)]
    user = [str(p) for p in ad.roots("user", tmp_path, tmp_path)]
    assert str(tmp_path / ".pi/skills") in proj
    assert str(tmp_path / ".pi/agent/skills") in user
    # .agents/skills remains so the shared-root set-cover still holds
    assert str(tmp_path / ".agents/skills") in proj


def test_no_probe_harness_returns_unverified_never_discovered(tmp_path):
    ad = get_adapter("codex")
    result = ad.discover("anything", tmp_path, tmp_path, probe=False)
    assert result["state"] == UNVERIFIED and result["reason"]
    # Even with probe=True: codex has no native probe.
    assert ad.discover("anything", tmp_path, tmp_path, probe=True)["state"] == UNVERIFIED
    # Filesystem presence alone must never yield DISCOVERED.
    skill = tmp_path / ".agents/skills/anything"
    skill.mkdir(parents=True)
    assert ad.discover("anything", tmp_path, tmp_path)["state"] == UNVERIFIED


def test_custom_profile_is_install_only_and_cannot_discover(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"harnesses": {"my-own-agent": {
        "executable": "my-agent",
        "project_roots": [".myagent/skills"],
        "user_roots": [".myagent/skills"]}}}))
    registry = load_registry(path)
    assert support_class("my-own-agent", registry) == "INSTALL_ONLY"
    ad = get_adapter("my-own-agent", registry)
    assert ad.detect()["version"] is None  # custom executables never launched
    result = ad.discover("x", tmp_path, tmp_path, probe=True)
    assert result["state"] == UNVERIFIED
    assert result["state"] != DISCOVERED


def test_run_case_requires_explicit_runner(tmp_path):
    result = get_adapter("codex").run_case({"id": "c1"}, {})
    assert result["state"] == "FAILED"
    assert "runner" in result["reason"].lower()


def test_adapter_unknown_harness_raises():
    with pytest.raises(UniSkillError):
        get_adapter("not-a-harness")
