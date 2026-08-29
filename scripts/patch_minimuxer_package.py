#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_minimuxer_package.py <minimuxer/Package.swift>")

p = Path(sys.argv[1])
s = p.read_text()

local = '''        .binaryTarget(
            name: "EMProxy",
            path: "LocalBinary/EMProxy.xcframework"
        ),'''

if 'path: "LocalBinary/EMProxy.xcframework"' in s:
    print("Minimuxer Package.swift already uses local EMProxy")
    raise SystemExit(0)

pattern = re.compile(
    r'(?ms)^\s*\.binaryTarget\(\s*'
    r'name:\s*"EMProxy",\s*'
    r'url:\s*"https://github\.com/SideStore/em_proxy/releases/download/[^"]+",\s*'
    r'checksum:\s*"[0-9a-f]+"\s*'
    r'\),'
)

s2, n = pattern.subn(local, s, count=1)
if n != 1:
    raise SystemExit("Could not replace remote EMProxy binaryTarget")

p.write_text(s2)
print("Patched minimuxer Package.swift to use local diagnostic EMProxy XCFramework")
