from pathlib import Path
import json
import os
import pytest
from uniskill.common import UniSkillError, digest_tree
from uniskill.contract import generate_skill, load_contract
from uniskill.validator import validate_skill
from uniskill.harnesses import choose_roots, native_discovery, opencode_diagnostics
from uniskill.deploy import install, plan, rollback, verify_receipt, read_receipt, latest_receipt
from uniskill.evaluation import load_cases, run_evals
from uniskill.cli import main


def sample(tmp_path, name="example-skill", text="Do the work"):
    p = tmp_path / "canonical" / name
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Use for a reproducible example\n---\n\n# Example\n{text}\n")
    return p


def test_valid(tmp_path):
    s = sample(tmp_path)
    assert validate_skill(s)["state"] == "VALID"
    assert digest_tree(s) == digest_tree(s)


def test_missing_frontmatter(tmp_path):
    s = sample(tmp_path)
    (s / "SKILL.md").write_text("no frontmatter")
    assert validate_skill(s)["state"] == "FAILED"


def test_mismatch_name(tmp_path):
    s = sample(tmp_path)
    (s / "SKILL.md").write_text("---\nname: some-other\ndescription: Test\n---\nBody")
    assert validate_skill(s)["state"] == "FAILED"


def test_metadata_types(tmp_path):
    s = sample(tmp_path)
    (s / "SKILL.md").write_text("---\nname: example-skill\ndescription: Test\nmetadata:\n  value: 20\n---\nBody")
    assert validate_skill(s)["state"] == "FAILED"


def test_scripts_warning_no_execute(tmp_path):
    s = sample(tmp_path)
    (s / "scripts").mkdir()
    (s / "scripts" / "evil.py").write_text("raise Exception('NEVER EXECUTE')\n")
    r = validate_skill(s)
    assert r["state"] == "VALID"
    assert "security review" in " ".join(r["warnings"])


def test_escape_links_warning(tmp_path):
    s = sample(tmp_path, text="[outside](../../secret.txt)")
    assert "escaping" in " ".join(validate_skill(s)["warnings"])


def test_reject_symlinks(tmp_path):
    s = sample(tmp_path)
    (s / "link").symlink_to("SKILL.md")
    assert validate_skill(s)["state"] == "FAILED"


def test_contract_generate(tmp_path):
    source = Path(__file__).parents[1] / "contracts/concurrency-audit.yaml"
    data = load_contract(source)
    result = generate_skill(data, tmp_path / "concurrency-audit")
    assert validate_skill(result)["state"] == "VALID"
    assert "no production deployment" in (result / "SKILL.md").read_text().lower()
    with pytest.raises(UniSkillError):
        generate_skill(data, result)


def test_invalid_contract(tmp_path):
    doc = tmp_path / "x.yaml"
    doc.write_text("skill: Not-A-Name\ngoal: task\n")
    with pytest.raises(UniSkillError):
        load_contract(doc)


def test_root_selection(tmp_path):
    selection = choose_roots(["codex", "gemini", "opencode", "cursor", "copilot"], "project", tmp_path, tmp_path)
    assert selection["roots"] == [str(tmp_path / ".agents/skills")]
    assert not selection["duplicate_exposure"]
    mixed = choose_roots(["claude-code", "codex"], "project", tmp_path, tmp_path)
    assert len(mixed["roots"]) == 2


def test_plan_nonmutating(tmp_path):
    s = sample(tmp_path)
    root = tmp_path / "target"
    p = plan(s, ["codex", "opencode"], "project", root, tmp_path)
    assert len(p["destinations"]) == 1
    assert not root.exists()
    assert p["mutation"].startswith("NONE")


def test_install_verify_rollback(tmp_path):
    s = sample(tmp_path)
    root = tmp_path / "target"
    state = tmp_path / "state"
    p = plan(s, ["codex", "opencode"], "project", root, tmp_path)
    rec = install(p, state)
    assert verify_receipt(rec)["state"] == "INSTALLED"
    assert latest_receipt(state, s.name)["receipt_id"] == rec["receipt_id"]
    assert rollback(state, rec["receipt_id"])["state"] == "ROLLED_BACK"
    assert not (root / ".agents/skills/example-skill").exists()
    assert latest_receipt(state, s.name) is None


def test_require_replace_and_restore_old(tmp_path):
    s = sample(tmp_path)
    root, state = tmp_path / "target", tmp_path / "state"
    previous = root / ".agents/skills/example-skill"
    previous.mkdir(parents=True)
    (previous / "SKILL.md").write_text("OLD CONTENT")
    p = plan(s, ["codex"], "project", root, tmp_path)
    with pytest.raises(UniSkillError, match="replace"):
        install(p, state)
    rec = install(p, state, replace=True)
    assert verify_receipt(rec)["state"] == "INSTALLED"
    rollback(state, rec["receipt_id"])
    assert (previous / "SKILL.md").read_text() == "OLD CONTENT"


def test_modified_files_protect_rollback(tmp_path):
    s = sample(tmp_path)
    root, state = tmp_path / "target", tmp_path / "state"
    rec = install(plan(s, ["codex"], "project", root, tmp_path), state)
    dest = Path(rec["destinations"][0]["path"])
    (dest / "SKILL.md").write_text("USER CHANGED THIS")
    assert verify_receipt(rec)["state"] == "FAILED"
    with pytest.raises(UniSkillError, match="changed"):
        rollback(state, rec["receipt_id"])
    assert (dest / "SKILL.md").read_text() == "USER CHANGED THIS"


def test_state_lock(tmp_path):
    s = sample(tmp_path)
    root, state = tmp_path / "target", tmp_path / "state"
    state.mkdir()
    (state / ".lock").write_text("someone else")
    with pytest.raises(UniSkillError, match="locked"):
        install(plan(s, ["codex"], "project", root, tmp_path), state)


def test_invalid_receipt_id(tmp_path):
    with pytest.raises(UniSkillError):
        read_receipt(tmp_path, "../file")


def test_native_probe_unverified_for_other(tmp_path):
    assert native_discovery("codex", "foo", tmp_path)["state"] == "UNVERIFIED"


def test_gemini_native_probe(tmp_path, monkeypatch):
    exe = tmp_path / "gemini"
    exe.write_text("#!/bin/sh\nprintf 'Available skills:\\n - example-skill: test\\n'\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    assert native_discovery("gemini", "example-skill", tmp_path)["state"] == "DISCOVERED"
    assert native_discovery("gemini", "example", tmp_path)["state"] == "FAILED"


def test_opencode_deny(tmp_path):
    (tmp_path / "opencode.json").write_text(json.dumps({"permission":{"skill":{"example-*":"deny"}}}))
    assert "deny" in " ".join(opencode_diagnostics("example-skill", tmp_path, tmp_path))


def test_eval_execution(tmp_path):
    script = tmp_path / "runner.py"
    script.write_text("import sys,json\np=sys.stdin.read()\nprint(json.dumps({'response':'mutex lock' if 'mutex' in p else 'bottleneck','tool_calls':[]}))\n")
    runner = tmp_path / "runner.json"
    runner.write_text(json.dumps({"argv":["python", str(script)], "cwd":str(tmp_path), "output":"json"}))
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps({"id":"case1", "prompt":"explain mutex", "must_include":["mutex"], "forbidden_tools":["deploy"]}) + "\n")
    results = run_evals(load_cases(cases), runner, 2)
    assert results["state"] == "EVAL_PASS"
    assert results["passed"] == 2


def test_eval_cannot_prove_tools_from_plain_text(tmp_path):
    runner = tmp_path / "runner.json"
    runner.write_text(json.dumps({"argv":["python", "-c", "print('hello')"], "cwd":str(tmp_path)}))
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps({"prompt":"hello", "required_tools":["search"]}) + "\n")
    assert run_evals(load_cases(cases), runner)["state"] == "FAILED"


def test_cli_dry_run_and_validate(tmp_path, capsys):
    s = sample(tmp_path)
    assert main(["validate",str(s)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "VALID"
    root = tmp_path / "target"
    assert main(["install",str(s),"--target","codex","--workspace",str(root)]) == 0
    assert not root.exists()
    assert json.loads(capsys.readouterr().out)["mutation"].startswith("NONE")


def test_cli_install_and_status(tmp_path, capsys):
    s = sample(tmp_path)
    root = tmp_path / "target"
    assert main(["install",str(s),"--target","codex","--workspace",str(root),"--apply"]) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["state"] == "INSTALLED"
    assert main(["status",s.name,"--workspace",str(root)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["targets"]["codex"]["functional"] == "UNVERIFIED"
    assert status["deployment"]["state"] == "INSTALLED"


def test_reject_escaping_project_root(tmp_path):
    s = sample(tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    (root / ".agents").symlink_to(tmp_path / "other", target_is_directory=True)
    (tmp_path / "other").mkdir()
    with pytest.raises(UniSkillError, match="escapes workspace"):
        plan(s, ["codex"], "project", root, tmp_path)

# Extended harness / unusual-agent coverage -----------------------------------
from uniskill.harnesses import (HARNESSES, load_registry, roots,
                               inspect_harness_skill, crush_diagnostics)


def test_jcode_source_inspected_and_crush_roots(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "work"
    assert str(workspace / ".jcode/skills") in map(str, roots("jcode", "project", workspace, home))
    assert str(workspace / ".agents/skills") in map(str, roots("jcode", "project", workspace, home))
    assert str(home / ".jcode/skills") in map(str, roots("jcode", "user", workspace, home))
    assert str(workspace / ".crush/skills") in map(str, roots("crush", "project", workspace, home))
    assert "hermes-agent" in HARNESSES and "pi" in HARNESSES and "kimi-code-cli" in HARNESSES


def test_shared_root_for_weird_harnesses(tmp_path):
    s = sample(tmp_path)
    p = plan(s, ["crush", "jcode", "codex", "opencode", "gemini", "cursor", "pi", "cline"],
             "project", tmp_path / "workspace", tmp_path / "home")
    assert len(p["destinations"]) == 1
    assert p["destinations"][0]["path"].endswith(".agents/skills/example-skill")
    assert all(p["coverage"].values())
    assert p["harness_evidence"]["jcode"]["tier"] == "local-source"
    assert p["harness_evidence"]["pi"]["tier"] == "community-mapped"


def test_custom_profile_and_path_escape_protection(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"harnesses":{"my-own-agent":{
        "executable":"my-agent", "project_roots":[".myagent/skills"],
        "user_roots":[".myagent/skills"]}}}))
    registry = load_registry(path)
    assert "my-own-agent" in registry and registry["my-own-agent"].tier == "custom"
    s = sample(tmp_path)
    p = plan(s, ["my-own-agent", "jcode"], "project",tmp_path / "proj",tmp_path / "home",registry)
    assert len(p["destinations"]) == 2
    path.write_text(json.dumps({"harnesses":{"bad":{
        "project_roots":["../escape"], "user_roots":[]}}}))
    with pytest.raises(UniSkillError, match="Invalid harness discovery root"):
        load_registry(path)


def test_profile_override_requires_explicit_consent(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"harnesses":{"jcode":{
        "project_roots":[".other/skills"], "user_roots":[".other/skills"]}}}))
    with pytest.raises(UniSkillError, match="override"):
        load_registry(path)


def test_doctor_no_receipt_and_crush_policy(tmp_path, capsys):
    root = tmp_path / "repo"
    home = tmp_path / "home"
    s = sample(root)
    installed = root / ".crush/skills" / s.name
    installed.parent.mkdir(parents=True)
    import shutil
    shutil.copytree(s, installed)
    cfg = root / "crush.json"
    cfg.write_text(json.dumps({"options":{"disabled_skills":[s.name]}}))
    assert "disabled" in " ".join(crush_diagnostics(s.name, root, home))
    assert main(["doctor",s.name,"--target","crush","--workspace",str(root),
                 "--home",str(home)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["targets"]["crush"]["state"] == "FILES_PRESENT"
    assert data["targets"]["crush"]["discovery"] == "UNVERIFIED"
    assert len(data["targets"]["crush"]["policy_diagnostics"]) == 1


def test_preexisting_shadow_collision(tmp_path):
    s = sample(tmp_path)
    root = tmp_path / "project"
    old = root / ".claude/skills" / s.name
    old.mkdir(parents=True)
    (old / "SKILL.md").write_text(f"---\nname: {s.name}\ndescription: Existing skill\n---\nOLD\n")
    p = plan(s,["codex","jcode"],"project",root,tmp_path / "home")
    assert p["existing_discovery_collisions"]
    assert any(x["path"] == str(old) for x in p["existing_discovery_collisions"])


def test_all_profile_count_and_safe_many_targets(tmp_path):
    s = sample(tmp_path)
    p = plan(s,list(HARNESSES),"project",tmp_path / "repo",tmp_path / "home")
    assert p["root_selection"] == "GREEDY"
    assert len(p["coverage"]) >= 25
    assert len(p["destinations"]) < len(HARNESSES)


def test_crushrc_disable_detection(tmp_path):
    root=tmp_path / 'repo'
    root.mkdir()
    (root / 'crushrc').write_text('option disable-skill test-skill\n')
    assert any('crushrc directive' in n for n in crush_diagnostics('test-skill', root, tmp_path))
