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
recovery = here / "apply_emproxy_rebind_recovery.py"

for required in (base, nonblocking, recovery):
    if not required.exists():
        raise SystemExit(f"missing EMProxy patch: {required}")

subprocess.check_call([sys.executable, str(base), target])
subprocess.check_call([sys.executable, str(nonblocking), target])
subprocess.check_call([sys.executable, str(recovery), target])
print("EMProxy diagnostics + nonblocking UDP + ENOTCONN rebind recovery applied and verified")
