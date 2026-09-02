#!/usr/bin/env python3
"""Make pinned minimuxer consume the locally built V22 IDevice XCFramework only."""
from __future__ import annotations
from pathlib import Path
import re
import sys
from typing import NoReturn

LOCAL = 'path: "LocalBinary/IDevice.xcframework"'
REMOTE_URL = 'https://github.com/SideStore/idevice/releases/download/'


def die(message: str) -> NoReturn:
    raise SystemExit(message)


def verify(root: Path) -> None:
    gateway = root / "DeviceGateway" / "Package.swift"
    top = root / "Package.swift"
    if not gateway.is_file() or not top.is_file():
        die("missing minimuxer package manifests")
    g = gateway.read_text()
    if g.count(LOCAL) != 1:
        die(f"expected exactly one local IDevice target; found {g.count(LOCAL)}")
    # Ignore commented examples when asserting the active remote target is gone.
    active_remote = re.search(
        r'(?ms)^\s*\.binaryTarget\(\s*name:\s*"IDevice",\s*url:\s*"https://github\.com/SideStore/idevice/releases/download/',
        g,
    )
    if active_remote:
        die("active remote IDevice target remains")
    t = top.read_text()
    if 'https://github.com/SideStore/em_proxy/releases/download/' not in t:
        die("released EMProxy target unexpectedly missing")
    if 'path: "LocalBinary/EMProxy.xcframework"' in t:
        die("V22 must not restore a custom EMProxy")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v22_local_idevice_package.py <minimuxer-root>")
    root = Path(sys.argv[1])
    gateway = root / "DeviceGateway" / "Package.swift"
    if not gateway.is_file():
        die(f"missing {gateway}")
    source = gateway.read_text()
    if LOCAL in source:
        verify(root)
        print("v22 local IDevice package target already present and verified")
        return
    remote = re.compile(
        r'(?ms)^\s*\.binaryTarget\(\s*'
        r'name:\s*"IDevice",\s*'
        r'url:\s*"https://github\.com/SideStore/idevice/releases/download/[^"]+",\s*'
        r'checksum:\s*"[0-9a-f]+"\s*'
        r'\),'
    )
    replacement = '''        .binaryTarget(
            name: "IDevice",
            path: "LocalBinary/IDevice.xcframework"
        ),'''
    source, n = remote.subn(replacement, source, count=1)
    if n != 1:
        die(f"expected one active remote IDevice target, replaced {n}")
    gateway.write_text(source)
    verify(root)
    print("v22 minimuxer now consumes the locally built patched IDevice XCFramework")


if __name__ == "__main__":
    main()
