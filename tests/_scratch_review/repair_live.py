import json, shutil
from pathlib import Path
from uniskill.cli import main
import io, contextlib

base = Path("/tmp/opencode/rev_repair_live")
shutil.rmtree(base, ignore_errors=True)
(s := base/"canonical/cli-skill").mkdir(parents=True)
(s/"SKILL.md").write_text("---\nname: cli-skill\ndescription: d\n---\nBODY\n")
ws = base/"ws"
main(["install", str(s), "--target","codex","--workspace",str(ws),"--apply"])
dest = ws/".agents/skills/cli-skill"
(dest/"SKILL.md").write_text("operator drifted THIS\n")

def run(args):
    f = io.StringIO(); e = io.StringIO()
    with contextlib.redirect_stdout(f), contextlib.redirect_stderr(e):
        code = main(args)
    return code, f.getvalue(), e.getvalue()

c,o,er = run(["diagnose","cli-skill","--workspace",str(ws)])
print("=== DIAGNOSE (drift) ==="); print(json.dumps(json.loads(o), indent=2))
c,o,er = run(["repair","cli-skill","--workspace",str(ws)])
print("=== REPAIR DRY (drift) exit", c, "==="); print(o)
c,o,er = run(["repair","cli-skill","--workspace",str(ws),"--execute"])
print("=== REPAIR EXEC (drift) exit", c, "==="); print(o)
