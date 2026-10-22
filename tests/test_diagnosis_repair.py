import pytest

from uniskill.common import UniSkillError
from uniskill import diagnosis, repair, status


def test_status_layers_do_not_conflate():
    out = status.assemble(
        receipt={"skill": "s", "receipt_id": "r"},
        verify={"state": "INSTALLED", "destinations": []},
        targets={"codex": {}},
    )
    assert out["targets"]["codex"]["installation"] == "INSTALLED"
    assert out["targets"]["codex"]["discovery"] == "UNVERIFIED"
    assert out["targets"]["codex"]["functional"] == "UNVERIFIED"
    assert out["targets"]["codex"]["certified"] == "UNVERIFIED"
    assert out["earliest_unproven_stage"] == {"harness": "codex", "stage": "discovered"}


def test_status_certifies_only_with_real_evidence():
    out = status.assemble(
        receipt={"skill": "s", "receipt_id": "r"},
        verify={"state": "INSTALLED", "destinations": []},
        targets={"codex": {}},
        evidence={"codex": {"discovered": {"state": "PASS"},
                            "functional": {"state": "PASS"}}},
    )
    assert out["targets"]["codex"]["certified"] == "CERTIFIED"


def test_status_failed_installation_short_circuits():
    out = status.assemble(
        receipt={"skill": "s", "receipt_id": "r"},
        verify={"state": "FAILED", "destinations": []},
        targets={"codex": {}},
    )
    assert out["state"] == "FAILED"
    assert out["failed_stage"] == "installed"
    assert out["earliest_unproven_stage"] is None


def test_status_without_receipt_is_unverified():
    out = status.assemble(receipt=None, targets={})
    assert out["state"] == "UNVERIFIED"
    assert "No active" in out["reason"]


def test_diagnosis_picks_earliest_failing_stage():
    row = diagnosis.diagnose({
        "installed": "PASS",
        "discovered": "POLICY_DENIED",
        "functional": "WRONG_RESULT",
    })
    assert row["stage"] == "discovered"
    assert row["intervention"] == "REPORT_POLICY_BLOCKER"
    assert row["blocked"] is True


def test_diagnosis_prefers_earliest_over_later():
    row = diagnosis.diagnose({"discovered": "NOT_INSTALLED", "functional": "WRONG_RESULT"})
    assert row["stage"] == "discovered"
    assert row["intervention"] == "REPLAN_ROOTS"


def test_diagnosis_unproven_stops_instead_of_guessing():
    row = diagnosis.diagnose({"installed": "PASS", "discovered": "UNVERIFIED"})
    assert row["failure"] == "UNVERIFIED"
    assert row["intervention"] == "DECLARE_UNVERIFIABLE"
    assert row["blocked"] is True


def test_diagnosis_clean_when_all_proven():
    row = diagnosis.diagnose({"installed": "PASS", "discovered": "PASS"})
    assert row["state"] == "CLEAN"
    assert row["blocked"] is False


def test_unknown_pair_is_a_hard_error():
    with pytest.raises(UniSkillError):
        diagnosis.classify("discovered", "SOMETHING_ELSE")


def test_unknown_stage_is_a_hard_error():
    with pytest.raises(UniSkillError):
        diagnosis.classify("not_a_stage", "MISSING")


def test_policy_repair_requires_explicit_approval():
    # A trust failure needs no mutation and is always blocked: the operator
    # must act in the harness, UniSkill must not act for them.
    trust = repair.plan_repair(diagnosis.classify("discovered", "TRUST_REQUIRED"))
    assert trust.blocked is True
    assert trust.mutates == "NONE"
    # A reload request would touch harness-side state, so it needs approval and
    # stays blocked until the operator allows it.
    reload_deny = repair.plan_repair(diagnosis.classify("discovered", "RELOAD_REQUIRED"))
    assert reload_deny.blocked is True and reload_deny.mutates == "POLICY"
    reload_ok = repair.plan_repair(diagnosis.classify("discovered", "RELOAD_REQUIRED"),
                                   allow_policy=True)
    assert reload_ok.blocked is False
    assert reload_ok.requires_approval is True


def test_repair_respects_attempt_budget():
    budget = repair.Budget(max_attempts=2, max_cost=10)
    plans = [repair.plan_repair(diagnosis.classify("discovered", "NOT_INSTALLED"))
             for _ in range(4)]
    calls = []
    handlers = {"replan": lambda plan: (calls.append(1), {"state": "REPAIRED"})[1]}
    out = repair.run(budget, plans, handlers)
    assert out["state"] == "STOPPED"
    assert out["stopped"]["reason"] == "budget"
    assert len(calls) == 2


def test_repair_respects_cost_budget():
    # Two cost-3 revisions exceed a cost budget of 5 even though the attempt
    # budget (10) is nowhere near exhausted.
    budget = repair.Budget(max_attempts=10, max_cost=5)
    plans = [repair.plan_repair(diagnosis.classify("functional", "WRONG_RESULT"))
             for _ in range(2)]
    handlers = {"revise_instructions": lambda plan: {"state": "REPAIRED"}}
    out = repair.run(budget, plans, handlers)
    assert out["stopped"]["reason"] == "budget"
    assert out["trace"][-1]["result"] == "BUDGET_EXHAUSTED"
    assert budget.attempts == 1


def test_repair_blocks_when_invariant_unmet():
    row = diagnosis.classify("functional", "WRONG_RESULT")
    plan = repair.plan_repair(row, invariants=[{"id": "no-deploy", "satisfied": False}])
    assert plan.blocked is True
    assert "drop_invariant" in plan.forbidden_effects


def test_repair_never_weakens_permissions():
    with pytest.raises(UniSkillError, match="forbidden effect"):
        repair.assert_no_weakening({"weaken_permission": False},
                                   {"weaken_permission": True}, [])


def test_repair_rejects_dropped_invariant():
    with pytest.raises(UniSkillError, match="invariant"):
        repair.assert_no_weakening({}, {}, ["never deploy"])


def test_repair_stops_on_blocked_before_spending_budget():
    budget = repair.Budget()
    plans = [repair.plan_repair(diagnosis.classify("discovered", "POLICY_DENIED"))]
    out = repair.run(budget, plans, {})
    assert out["state"] == "STOPPED"
    assert out["trace"][0]["result"] == "BLOCKED"
    assert budget.attempts == 0


def test_repair_never_fakes_success_without_handler():
    out = repair.run(repair.Budget(),
                     [repair.plan_repair(diagnosis.classify("discovered", "NOT_INSTALLED"))],
                     {})
    assert out["trace"][0]["result"] == "ADVISORY"
    assert out["state"] == "NO_ACTION"


def test_repair_stops_when_handler_fails():
    out = repair.run(repair.Budget(),
                     [repair.plan_repair(diagnosis.classify("discovered", "NOT_INSTALLED"))],
                     {"replan": lambda plan: {"state": "FAILED"}})
    assert out["state"] == "STOPPED"
    assert out["trace"][0]["result"] == "FAILED"


def test_earliest_unproven_helper():
    s = status.assemble(receipt={"skill": "s", "receipt_id": "r"},
                        verify={"state": "INSTALLED", "destinations": []},
                        targets={"codex": {}})
    assert status.earliest_unproven(s)["stage"] == "discovered"