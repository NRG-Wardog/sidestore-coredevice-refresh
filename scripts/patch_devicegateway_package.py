#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_devicegateway_package.py <Package.swift>")

p = Path(sys.argv[1])
s = p.read_text()

specs = [
    (
        "IDevice",
        'path: "LocalBinary/IDevice.xcframework"',
        re.compile(
            r'(?ms)^\s*\.binaryTarget\(\s*'
            r'name:\s*"IDevice",\s*'
            r'url:\s*"https://github\.com/SideStore/idevice/releases/download/[^"]+",\s*'
            r'checksum:\s*"[0-9a-f]+"\s*'
            r'\),'
        ),
        '''        .binaryTarget(
            name: "IDevice",
            path: "LocalBinary/IDevice.xcframework"
        ),''',
    ),
    (
        "OpenSSL",
        'path: "LocalBinary/OpenSSL.xcframework"',
        re.compile(
            r'(?ms)^\s*\.binaryTarget\(\s*'
            r'name:\s*"OpenSSL",\s*'
            r'url:\s*"https://github\.com/krzyzanowskim/OpenSSL/releases/download/[^"]+",\s*'
            r'checksum:\s*"[0-9a-f]+"\s*'
            r'\),'
        ),
        '''        .binaryTarget(
            name: "OpenSSL",
            path: "LocalBinary/OpenSSL.xcframework"
        ),''',
    ),
]

for name, local_path, pattern, replacement in specs:
    has_local = local_path in s
    has_remote = pattern.search(s) is not None

    if has_local:
        if has_remote:
            raise SystemExit(f"{name}: local target exists but active remote target still remains")
        if s.count(local_path) != 1:
            raise SystemExit(f"{name}: expected one local path, found {s.count(local_path)}")
        continue

    if not has_remote:
        raise SystemExit(f"{name}: neither the expected active remote target nor local target was found")

    s, count = pattern.subn(replacement, s, count=1)
    if count != 1:
        raise SystemExit(f"{name}: expected exactly one binaryTarget replacement, got {count}")

for name, local_path, pattern, _ in specs:
    if s.count(local_path) != 1:
        raise SystemExit(f"{name}: verification failed, expected one local path, found {s.count(local_path)}")
    if pattern.search(s):
        raise SystemExit(f"{name}: verification failed, active remote binaryTarget remains")

p.write_text(s)
print("DeviceGateway Package.swift now uses exactly one local IDevice and one local OpenSSL target")
