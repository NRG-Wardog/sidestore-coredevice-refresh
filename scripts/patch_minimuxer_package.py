#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_minimuxer_package.py <minimuxer/Package.swift>")

p = Path(sys.argv[1])
s = p.read_text()

local_path = 'path: "LocalBinary/EMProxy.xcframework"'
local = '''        .binaryTarget(
            name: "EMProxy",
            path: "LocalBinary/EMProxy.xcframework"
        ),'''

pattern = re.compile(
    r'(?ms)^\s*\.binaryTarget\(\s*'
    r'name:\s*"EMProxy",\s*'
    r'url:\s*"https://github\.com/SideStore/em_proxy/releases/download/[^"]+",\s*'
    r'checksum:\s*"[0-9a-f]+"\s*'
    r'\),'
)

if local_path in s:
    if pattern.search(s):
        raise SystemExit("Local EMProxy target exists but active remote EMProxy target still remains")
    if s.count(local_path) != 1:
        raise SystemExit(f"Expected one local EMProxy path, found {s.count(local_path)}")
    print("Minimuxer Package.swift already uses exactly one local EMProxy target")
    raise SystemExit(0)

s2, n = pattern.subn(local, s, count=1)
if n != 1:
    raise SystemExit("Could not replace exactly one active remote EMProxy binaryTarget")

if pattern.search(s2):
    raise SystemExit("Verification failed: active remote EMProxy binaryTarget remains")
if s2.count(local_path) != 1:
    raise SystemExit(f"Verification failed: expected one local EMProxy path, found {s2.count(local_path)}")

p.write_text(s2)
print("Patched minimuxer Package.swift to use exactly one local diagnostic EMProxy XCFramework")
