"""RED proof runner.

Forces the RECONSTRUCTED ORIGINAL (vulnerable) deploy.py — copied into this
package as uniskill/deploy.py — to be the `uniskill` that pytest imports, then
runs the adversarial security tests. The real source tree's `src/uniskill` is
removed from sys.path so the vulnerable code (not the remediated one) is what
the tests exercise.

Usage:  python tests/_red_orig/run_red_proof.py
"""
from __future__ import annotations
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRATCH = os.path.join(_HERE, "run")
_REAL_SRC = os.path.abspath(os.path.join(_HERE, "..", "..", "src"))

# Neutralize every path entry that would resolve to the real source tree.
for entry in list(sys.path):
    if entry and os.path.abspath(entry) == _REAL_SRC:
        sys.path.remove(entry)
# Alias safety: also drop any cwd that equals the workspace root.
_WS = os.path.abspath(os.path.join(_HERE, "..", ".."))
for entry in list(sys.path):
    if entry and os.path.abspath(entry) == _WS:
        sys.path.remove(entry)

sys.path.insert(0, _SCRATCH)

import uniskill.deploy as deployed  # noqa: E402
print("RED targeting deploy at:", deployed.__file__)
assert deployed.__file__.startswith(_SCRATCH), "RED did not select the vulnerable deploy.py"

import pytest  # noqa: E402
test_file = os.path.join(_WS, "tests", "test_deploy_security.py")
sys.exit(pytest.main(["-p", "no:cacheprovider", "-q", test_file]))