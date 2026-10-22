import os, shutil, json
from pathlib import Path
from uniskill.common import UniSkillError
from uniskill.deploy import install, plan, rollback, read_receipt, _read_ledger

base = Path("/tmp/opencode/rev_ledger")
shutil.rmtree(base, ignore_errors=True)


def skill(name, body):
    s = base / "canonical" / name
    s.mkdir(parents=True, exist_ok=True)
    (s / "SKILL.md").write_text("---\nname: %s\ndescription: d\n---\n%s\n" % (name, body))
    return s


ws = base / "ws"
state = base / "state"
r1 = install(plan(skill("x", "V1"), ["codex"], "project", ws, base),
             state)
r1id = r1["receipt_id"]
r2 = install(plan(skill("x", "V2"), ["codex"], "project", ws, base),
             state, replace=True)
r2id = r2["receipt_id"]
dest = Path(r2["destinations"][0]["path"])
print("r1=%s r2=%s dest=%s" % (r1id, r2id, dest))
print("ledger before:", json.dumps(_read_ledger(state), indent=2))

# Attempt 1: pop r2's ownership entry from ledger, then rollback r1
ledger = _read_ledger(state)
del ledger[str(dest)]
# use internal write to simulate corruption
from uniskill.deploy import _write_ledger
_write_ledger(state, ledger)
print("ledger after removing r2 ownership:", _read_ledger(state))
try:
    out = rollback(state, r1id)
    print("ROLLBACK r1 SLEW THROUGH after ledger corruption:", out)
    print("  dest exists after r1 rollback:", dest.exists())
    print("  r2 receipt file exists:", (state/"receipts"/(r2id+".json")).exists())
except UniSkillError as e:
    print("rollback r1 blocked:", e)