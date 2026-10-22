"""Tests for the deterministic UniSkill creation pipeline.

Covers all five stages:
  1. Behavioural contract validation
  2. Methodology selection
  3. Candidate generation
  4. Comparative evaluation
  5. Selection
"""
from __future__ import annotations
from pathlib import Path
import pytest
import yaml
from uniskill.common import UniSkillError
from uniskill.contract import (
    load_contract,
    generate_skill,
    engine_skill,
)
from uniskill.methodology import (
    load_methodology,
    select_methodology,
    generate_candidates,
    evaluate_candidates,
    select_candidate,
    baseline_skill,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"


def _make_contract(tmp_path, overrides=None):
    """Start from concurrency-audit.yaml and apply overrides."""
    src = _CONTRACTS / "concurrency-audit.yaml"
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if overrides:
        data.update(overrides)
    p = tmp_path / "test-contract.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Stage 1 – Contract validation
# ---------------------------------------------------------------------------

def test_load_contract_valid(tmp_path):
    p = _make_contract(tmp_path)
    data = load_contract(p)
    assert data["skill"] == "concurrency-audit"
    assert len(data["triggers"]["positive"]) >= 1


def test_load_contract_missing_goal(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("skill: foo\n")
    with pytest.raises(UniSkillError, match="goal"):
        load_contract(p)


def test_load_contract_no_positive_triggers(tmp_path):
    p = _make_contract(tmp_path, overrides={"triggers": {"positive": [], "negative": ["x"]}})
    with pytest.raises(UniSkillError, match="positive"):
        load_contract(p)


def test_load_contract_no_required_capabilities(tmp_path):
    p = _make_contract(tmp_path, overrides={"capabilities": {"required": [], "optional": []}})
    with pytest.raises(UniSkillError, match="required"):
        load_contract(p)


# ---------------------------------------------------------------------------
# Stage 2 – Methodology selection
# ---------------------------------------------------------------------------

def test_methodology_selection_picks_contract_principles(tmp_path):
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    result = select_methodology(contract, meta)

    assert result["version"]
    selected_ids = [s["id"] for s in result["selected"]]

    # concurrency-audit has invariants, positive triggers, acceptance tests
    assert "enforcement-boundary" in selected_ids
    assert "evaluation-before-expansion" in selected_ids
    assert "behavioral-contract-first" in selected_ids
    # New principle should also fire
    assert "needs-deterministic-enforcement" in selected_ids
    assert "needs-negative-triggers" in selected_ids


def test_methodology_selection_records_reasons(tmp_path):
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    result = select_methodology(contract, meta)

    for s in result["selected"]:
        assert "reason" in s
        assert s["reason"]
        assert "id" in s
        assert "class" in s
        assert "rule" in s
        assert "sources" in s


def test_methodology_selection_excludes_irrelevant(tmp_path):
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    result = select_methodology(contract, meta)

    # If nothing matches a principle, it should be excluded
    if result["excluded"]:
        for e in result["excluded"]:
            assert "reason" in e


def test_methodology_selection_records_source_review_dates(tmp_path):
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    result = select_methodology(contract, meta)

    dates = result.get("source_review_dates", {})
    assert isinstance(dates, dict)
    # At least one source should have a review date
    assert len(dates) >= 1


def test_methodology_empty_triggers_rejected(tmp_path):
    """A contract with no positive triggers should fail at load_contract."""
    p = _make_contract(tmp_path, overrides={"triggers": {"positive": [], "negative": ["x"]}})
    with pytest.raises(UniSkillError, match="positive"):
        load_contract(p)


# ---------------------------------------------------------------------------
# Stage 3 – Candidate generation
# ---------------------------------------------------------------------------

def test_generate_three_candidates(tmp_path):
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    sel = select_methodology(contract, meta)
    result = generate_candidates(contract, tmp_path / "cands", sel)

    names = [c["name"] for c in result["candidates"]]
    assert "minimal" in names
    assert "structured" in names
    assert "deterministic-assisted" in names


def test_candidates_are_genuinely_different(tmp_path):
    """Assert digests and content differ across candidates."""
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    sel = select_methodology(contract, meta)
    result = generate_candidates(contract, tmp_path / "cands", sel)

    cands = result["candidates"]
    assert len(cands) == 3

    digests = [c["digest"] for c in cands]
    # All three digests must be unique
    assert len(set(digests)) == 3

    # Read actual content
    contents = {}
    for c in cands:
        contents[c["name"]] = Path(c["dir"]) / "SKILL.md"

    min_text = contents["minimal"].read_text()
    str_text = contents["structured"].read_text()
    det_text = contents["deterministic-assisted"].read_text()

    # structured > minimal (has extra sections)
    assert len(str_text) > len(min_text)
    # structured contains "Methodology References"
    assert "Methodology References" in str_text
    assert "Methodology References" not in min_text
    # deterministic-assisted contains deterministic step
    assert "Deterministic Verification Step" in det_text
    # minimal does NOT contain deterministic step
    assert "Deterministic Verification Step" not in min_text


def test_deterministic_assisted_marks_not_met(tmp_path):
    """If contract has no invariants, deterministic-assisted marks justification not-met."""
    p = _make_contract(tmp_path, overrides={"invariants": []})
    contract = load_contract(p)
    meta = load_methodology()
    sel = select_methodology(contract, meta)
    result = generate_candidates(contract, tmp_path / "cands2", sel)

    det = [c for c in result["candidates"] if c["name"] == "deterministic-assisted"][0]
    content = Path(det["dir"]) / "SKILL.md"
    text = content.read_text()
    # Should contain "not-met" marker
    assert "not-met" in text


def test_generate_candidates_no_overwrite(tmp_path):
    """Generation must refuse to overwrite an existing non-empty directory."""
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    out = tmp_path / "cands3"
    meta = load_methodology()
    sel = select_methodology(contract, meta)

    # First generation succeeds
    generate_candidates(contract, out, sel)

    # Second generation on same non-empty dir fails
    with pytest.raises(UniSkillError, match="Refusing overwrite"):
        generate_candidates(contract, out, sel)


# ---------------------------------------------------------------------------
# Stage 4 – Evaluation
# ---------------------------------------------------------------------------

def test_evaluation_detects_broken_candidate(tmp_path):
    """Hand-build a candidate that drops an invariant and verify it is flagged."""
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    sel = select_methodology(contract, meta)

    # Generate candidates normally
    result = generate_candidates(contract, tmp_path / "cands4", sel)

    # Find the first candidate and corrupt it: remove first invariant text
    cand = result["candidates"][0]
    cand_dir = Path(cand["dir"])
    skill_md = cand_dir / "SKILL.md"
    original = skill_md.read_text()

    # Remove the first invariant from the content
    inv = contract["invariants"][0]
    cleaned = original.replace(inv, "[REMOVED INVARIANT]")
    skill_md.write_text(cleaned, encoding="utf-8")

    evaluations = evaluate_candidates(contract, result["candidates"])

    # The broken candidate should have a failed invariant check
    found_fail = False
    for e in evaluations:
        if e["candidate_name"] == cand["name"]:
            for f in e["findings"]:
                if f["check"] == "invariant-present" and not f["passed"]:
                    found_fail = True
                    break
    assert found_fail, "Evaluation should detect missing invariant"


def test_evaluation_labels_evidence_types(tmp_path):
    """Every finding must have an evidence_type label."""
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    sel = select_methodology(contract, meta)
    result = generate_candidates(contract, tmp_path / "cands5", sel)

    evaluations = evaluate_candidates(contract, result["candidates"])

    for e in evaluations:
        for f in e["findings"]:
            assert "evidence_type" in f
            assert f["evidence_type"] in (
                "static-structure", "contract-coverage", "enforcement-boundary",
            )
        assert "verification_class" in e
        assert e["verification_class"] == "static-structural-only"


def test_evaluation_includes_baseline_comparison(tmp_path):
    """Evaluation must include a baseline comparison finding."""
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    sel = select_methodology(contract, meta)
    result = generate_candidates(contract, tmp_path / "cands6", sel)

    bl = baseline_skill(contract)
    evaluations = evaluate_candidates(contract, result["candidates"], bl)

    for e in evaluations:
        baseline_found = any(
            f["check"] == "baseline-improvement" for f in e["findings"]
        )
        assert baseline_found, "Each evaluation must include baseline comparison"


# ---------------------------------------------------------------------------
# Stage 5 – Selection
# ---------------------------------------------------------------------------

def test_select_picks_smallest_qualified(tmp_path):
    """Selection picks the smallest candidate that passes all mandatory checks."""
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    sel = select_methodology(contract, meta)
    gen_result = generate_candidates(contract, tmp_path / "cands7", sel)

    evaluations = evaluate_candidates(contract, gen_result["candidates"])
    selection = select_candidate(evaluations)

    assert selection["state"] == "SELECTED"
    assert selection["winner"] is not None
    assert "runner_up" in selection
    assert selection["reason"]
    # Winner should be smallest
    # minimal is typically smallest
    if gen_result["candidates"][0]["name"] in [
        e["candidate_name"] for e in evaluations if e["coverage_pass"]
    ]:
        assert selection["winner"]["candidate_name"] == "minimal"


def test_select_returns_failed_when_no_candidate_meets_mandatory(tmp_path):
    """If no candidate satisfies mandatory requirements, return FAILED."""
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    sel = select_methodology(contract, meta)
    gen_result = generate_candidates(contract, tmp_path / "cands8", sel)

    # Corrupt ALL candidates: remove first invariant from each
    inv = contract["invariants"][0]
    for c in gen_result["candidates"]:
        skill_md = Path(c["dir"]) / "SKILL.md"
        text = skill_md.read_text().replace(inv, "[REMOVED]")
        skill_md.write_text(text, encoding="utf-8")

    evaluations = evaluate_candidates(contract, gen_result["candidates"])
    selection = select_candidate(evaluations)

    assert selection["state"] == "FAILED"
    assert selection["winner"] is None
    assert selection.get("unmet_requirements")
    assert len(selection["unmet_requirements"]) > 0


def test_select_includes_runner_up_reason(tmp_path):
    """Selection must include runner_up and explain why the winner won."""
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    meta = load_methodology()
    sel = select_methodology(contract, meta)
    gen_result = generate_candidates(contract, tmp_path / "cands9", sel)

    evaluations = evaluate_candidates(contract, gen_result["candidates"])
    selection = select_candidate(evaluations)

    assert selection["state"] == "SELECTED"
    assert selection["runner_up"] is not None
    # Reason should mention runner-up
    assert "Runner-up" in selection["reason"]


# ---------------------------------------------------------------------------
# generate_skill backward-compatibility
# ---------------------------------------------------------------------------

def test_generate_skill_still_works(tmp_path):
    """generate_skill writes SKILL.md containing goal text."""
    p = _make_contract(tmp_path)
    data = load_contract(p)
    out = tmp_path / "out"
    result = generate_skill(data, out)
    skill_md = result / "SKILL.md"
    assert skill_md.exists()
    text = skill_md.read_text()
    assert data["goal"] in text


def test_generate_skill_refuses_overwrite_nonempty(tmp_path):
    """generate_skill raises UniSkillError on non-empty directory."""
    p = _make_contract(tmp_path)
    data = load_contract(p)
    out = tmp_path / "out"
    generate_skill(data, out)
    # Create a dummy file so directory is non-empty
    (out / "dummy.txt").write_text("exists")
    with pytest.raises(UniSkillError, match="Refusing overwrite"):
        generate_skill(data, out)


def test_generate_skill_twice_raises(tmp_path):
    """Calling generate_skill twice on a non-empty dir must raise."""
    p = _make_contract(tmp_path)
    data = load_contract(p)
    out = tmp_path / "out2"
    generate_skill(data, out)
    with pytest.raises(UniSkillError):
        generate_skill(data, out)


# ---------------------------------------------------------------------------
# Engine orchestrator
# ---------------------------------------------------------------------------

def test_engine_skill_returns_trace(tmp_path):
    """engine_skill executes all five stages and returns a full trace."""
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    out = tmp_path / "engine_out"

    trace = engine_skill(contract, out)

    assert trace["stage_contract"] == "ok"
    assert "stage_methodology" in trace
    assert "stage_candidates" in trace
    assert "stage_evaluation" in trace
    assert "stage_selection" in trace
    assert trace["stage_selection"]["state"] in ("SELECTED", "FAILED")


def test_engine_skill_preserves_generate_skill_contract(tmp_path):
    """engine_skill does not break generate_skill."""
    p = _make_contract(tmp_path)
    contract = load_contract(p)
    out = tmp_path / "engine_out2"

    trace = engine_skill(contract, out)
    assert trace["stage_selection"]["state"] == "SELECTED"

    # generate_skill should still work independently
    gen_out = tmp_path / "gen_out"
    generate_skill(contract, gen_out)
    assert (gen_out / "SKILL.md").exists()
