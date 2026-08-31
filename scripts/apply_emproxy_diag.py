#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_emproxy_diag.py <src/lib.rs>")

target = sys.argv[1]
here = Path(__file__).resolve().parent
base = here / "apply_emproxy_diag_base.py"
nonblocking = here / "apply_emproxy_nonblocking.py"

if not base.exists():
    raise SystemExit(f"missing base EMProxy diagnostic patch: {base}")
if not nonblocking.exists():
    raise SystemExit(f"missing EMProxy nonblocking patch: {nonblocking}")

subprocess.check_call([sys.executable, str(base), target])
subprocess.check_call([sys.executable, str(nonblocking), target])
print("EMProxy diagnostics + nonblocking UDP fix applied and verified")
