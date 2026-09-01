#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GENERATED="/tmp/v14_ci.fixed.sh"

gzip -dc "$SCRIPT_DIR/v14_ci.sh.gz" > "$GENERATED"

python3 - "$GENERATED" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text()
old = '  python3 "$BUILDER/scripts/patch_v12_packages.py" "$mux"\n'
new = '''  if grep -Fq 'path: "LocalBinary/IDevice.xcframework"' "$mux/DeviceGateway/Package.swift"; then
    grep -Fq 'path: "LocalBinary/EMProxy.xcframework"' "$mux/Package.swift"
    grep -Fq '7b9d269ec64027d73a50faa917cb18fa218c1fc9' "$mux/DeviceGateway/Package.swift"
    echo "v14 package layout already uses local patched XCFrameworks; skipping the v12 remote-pin verifier"
  else
    python3 "$BUILDER/scripts/patch_v12_packages.py" "$mux"
  fi
'''

if source.count(old) != 1:
    raise SystemExit(f"v14 CI source drift: expected one package patch anchor, found {source.count(old)}")

path.write_text(source.replace(old, new, 1))
PY

bash -n "$GENERATED"
exec bash "$GENERATED"
