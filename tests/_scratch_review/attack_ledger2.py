import os, shutil, json
from pathlib import Path
from uniskill.common import UniSkillError, digest_tree
from uniskill.deploy import (install, plan, rollback, read_receipt,
                             _read_ledger, _write_ledger)

base = Path("/tmp/opencode/rev_ledger2")
shutil.rmtree(base, ignore_errors=True)


def skill(name, body):
    s = base / "canonical" / name
    s.mkdir(parents=True, exist_ok=True)
    (s / "SKILL.md").write_text("---\nname: %s\ndescription: d\n---\n%s\n" % (name, body))
    return s


ws = base / "ws"
state = base / "state"
# Identical content -> identical source_digest
r1 = install(plan(skill("x", "SAME"), ["codex"], "project", ws, base), state)
r1id = r1["receipt_id"]
r2 = install(plan(skill("x", "SAME"), ["codex"], "project", ws, base), state)
r2id = r2["receipt_id"]
dest = Path(r2["destinations"][0]["path"])
print("r1=%s r2=%s" % (r1id, r2id))
print("digest matches r2:", digest_tree(dest) == r2["source_digest"])

# Corruption: drop r2's ownership entry
ledger = _read_ledger(state)
del ledger[str(dest)]
_write_ledger(state, ledger)
print("ledger now:", ledger)

# Now rollback r1 (the OLDER receipt). With identical content, verify_receipt(r1)
# should PASS (dest matches r1 digest too), and ownership check is bypassed.
try:
    out = rollback(state, r1id)
    print("ROLLBACK r1 (older) SLEW THROUGH:", out["state"])
    print("  dest exists after:", dest.exists())
    # What is the ownership now?
    ledger2 = _read_ledger(state)
    print("  ledger after:", ledger2)
    # Can r2 still verify?
    try:
        v = read_receipt(state, r2id)
        print("  r2 readable, its file still claims INSTALLED:", v["state"])
    except UniSkillError as e:
        print("  r2 receipt gone/error:", e)
except UniSkillError as e:
    print("rollback r1 blocked:", e)