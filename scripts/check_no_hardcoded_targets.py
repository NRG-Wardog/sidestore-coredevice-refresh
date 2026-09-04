#!/usr/bin/env python3
"""
Static CI check (Mandate Section 27)
Fails if production code contains unexpected hardcoded target addresses:
- 10.7.0.1
- 10.0.0.15
- 127.0.0.1

Allowed only in:
- Explicit test fixtures (tests/*)
- Diagnostic tools / PC scripts (scripts/*)
- Documentation (*.md, *.txt)
- Obsolete experiment archives
"""

import sys
from pathlib import Path

FORBIDDEN_TARGETS = [
    "10.7.0.1",
    "10.7.1.1",
]

# In production code, 127.0.0.1 and 10.0.0.15 cannot be dialed as remote transport targets
FORBIDDEN_PRODUCTION_TARGETS = [
    "10.7.0.1",
    "10.7.1.1",
    "\"10.0.0.15\"",
]

PRODUCTION_DIRS = [
    Path("LockdownDirectDiag"),
]

EXCLUDED_FILES = [
    # Explicit unit tests or headers
    "LocalTunnelEndpointResolver.h", # header declarations
]

def check_file(path: Path) -> list[str]:
    violations = []
    if path.name in EXCLUDED_FILES:
        return violations
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    for idx, line in enumerate(lines, 1):
        # Skip comments
        stripped = line.strip()
        if stripped.startswith(("//", "/*", "*", "#")):
            continue
        for target in FORBIDDEN_PRODUCTION_TARGETS:
            if target in line:
                violations.append(f"{path}:{idx}: Found forbidden hardcoded target '{target}': {stripped}")
    return violations

def main() -> int:
    root = Path(__file__).resolve().parent.parent
    violations = []
    for pdir in PRODUCTION_DIRS:
        full_dir = root / pdir
        if not full_dir.exists():
            continue
        for ext in ("*.m", "*.h", "*.swift", "*.rs"):
            for f in full_dir.rglob(ext):
                violations.extend(check_file(f))

    if violations:
        print(f"STATIC CHECK FAILED: {len(violations)} forbidden hardcoded target(s) found in production code:")
        for v in violations:
            print(f"  {v}")
        return 1
    else:
        print("STATIC CHECK PASSED: No forbidden hardcoded targets in production code.")
        return 0

if __name__ == "__main__":
    sys.exit(main())
