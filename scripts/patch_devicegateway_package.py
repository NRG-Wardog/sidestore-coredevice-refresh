from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_devicegateway_package.py <Package.swift>")

p = Path(sys.argv[1])
s = p.read_text()

replacements = [
    (
        "IDevice",
        re.compile(
            r'(?ms)\s*\.binaryTarget\(\s*'
            r'name:\s*"IDevice",\s*'
            r'url:\s*"https://github\.com/SideStore/idevice/releases/download/[^"]+",\s*'
            r'checksum:\s*"[0-9a-f]+"\s*'
            r'\),'
        ),
        '''
         .binaryTarget(
             name: "IDevice",
             path: "LocalBinary/IDevice.xcframework"
         ),''',
    ),
    (
        "OpenSSL",
        re.compile(
            r'(?ms)\s*\.binaryTarget\(\s*'
            r'name:\s*"OpenSSL",\s*'
            r'url:\s*"https://github\.com/krzyzanowskim/OpenSSL/releases/download/[^"]+",\s*'
            r'checksum:\s*"[0-9a-f]+"\s*'
            r'\),'
        ),
        '''
         .binaryTarget(
             name: "OpenSSL",
             path: "LocalBinary/OpenSSL.xcframework"
         ),''',
    ),
]

for name, pattern, replacement in replacements:
    s, count = pattern.subn(replacement, s, count=1)
    if count != 1:
        raise SystemExit(f"Expected exactly one {name} binaryTarget replacement, got {count}")

p.write_text(s)

check = p.read_text()
for required in [
    'path: "LocalBinary/IDevice.xcframework"',
    'path: "LocalBinary/OpenSSL.xcframework"',
]:
    if required not in check:
        raise SystemExit(f"Verification failed: {required}")

print("DeviceGateway Package.swift now uses local IDevice and OpenSSL")
