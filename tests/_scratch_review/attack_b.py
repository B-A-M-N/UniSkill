import os, shutil
from pathlib import Path
from uniskill.common import UniSkillError
from uniskill.deploy import install, plan

base = Path("/tmp/opencode/rev_attacks_b")
shutil.rmtree(base, ignore_errors=True)


def new_skill(name):
    (base / "canonical" / name).mkdir(parents=True, exist_ok=True)
    (base / "canonical" / name / "SKILL.md").write_text(
        "---\nname: %s\ndescription: d\n---\nBODY\n" % name)
    return base / "canonical" / name


# 1. Source IS a symlink
s = new_skill("real")
lnk = base / "link"
lnk.symlink_to(s)
try:
    plan(lnk, ["codex"], "project", base / "ws1", base)
    print("1. source-symlink plan ACCEPTED")
except UniSkillError as e:
    print("1. source-symlink plan BLOCKED:", e)

# 2. Destination parent symlink to in-workspace dir
ws = base / "ws2"; ws.mkdir()
p = plan(new_skill("y"), ["codex"], "project", ws, base)
inside = base / "inside"; inside.mkdir()
(ws / ".agents").symlink_to(inside)
try:
    r = install(p, base / "state2")
    print("2. in-workspace parent symlink SLEW THROUGH:", r["state"])
except UniSkillError as e:
    print("2. in-workspace parent symlink BLOCKED:", e)

# 3. Destination leaf itself a symlink
ws3 = base / "ws3"; ws3.mkdir()
p = plan(new_skill("z"), ["codex"], "project", ws3, base)
outside3 = base / "o3"; outside3.mkdir()
dest = ws3 / ".agents/skills/z"
dest.mkdir(parents=True, exist_ok=True)
dest.rmdir()
dest.symlink_to(outside3)
try:
    r = install(p, base / "state3")
    print("3. dest-leaf symlink SLEW THROUGH:", r["state"])
except UniSkillError as e:
    print("3. dest-leaf symlink BLOCKED:", e)
print("   outside3 has z:", (outside3 / "z").exists())