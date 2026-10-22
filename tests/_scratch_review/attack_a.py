import os, shutil
from pathlib import Path
from uniskill.common import UniSkillError
from uniskill.deploy import install, plan

base = Path("/tmp/opencode/rev_attacks")
shutil.rmtree(base, ignore_errors=True)
(base/"canonical/x").mkdir(parents=True)
(base/"canonical/x/SKILL.md").write_text("---\nname: x\ndescription: d\n---\nBODY\n")
ws = base/"ws"
state = base/"state"
outside = base/"outside"; outside.mkdir()

p = plan(base/"canonical/x", ["codex"], "project", ws, base)
print("planned dests:", p["destinations"])
# Try to swap the anchor itself to a symlink
ws.mkdir(exist_ok=True)
os.symlink(str(outside), ws/".agents")
try:
    r = install(p, state)
    print("INSTALL SLEW THROUGH (VULN):", r)
except UniSkillError as e:
    print("BLOCKED:", e)
print("outside has x:", (outside/"x").exists())
