#!/usr/bin/env python3
"""Select the locally built v13 IDevice XCFramework after v12 package patching."""

from __future__ import annotations

from pathlib import Path
import re
import sys

LOCAL_PATH = 'path: "LocalBinary/IDevice.xcframework"'


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def verify(root: Path) -> None:
    gateway_package = root / "DeviceGateway" / "Package.swift"
    package = root / "Package.swift"
    for path in (gateway_package, package):
        if not path.exists():
            die(f"v13 package verification: missing {path}")

    gateway = gateway_package.read_text()
    if gateway.count(LOCAL_PATH) != 1:
        die(f"v13 package verification failed: expected one local IDevice target, found {gateway.count(LOCAL_PATH)}")
    if re.search(
        r'(?ms)\.binaryTarget\(\s*name:\s*"IDevice",\s*url:',
        gateway,
    ):
        die("v13 package verification failed: active remote IDevice target remains")
    if 'path: "LocalBinary/EMProxy.xcframework"' not in package.read_text():
        die("v13 package verification failed: fixed local EMProxy target missing")
    if "e72cd0272ab7b4548b5cd22ed4a81008b2b52717" not in gateway:
        die("v13 package verification failed: RemotePairingKit revision is not pinned")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v13_packages.py <minimuxer-root>")

    root = Path(sys.argv[1])
    gateway_package = root / "DeviceGateway" / "Package.swift"
    if not gateway_package.exists():
        die(f"missing DeviceGateway Package.swift: {gateway_package}")

    source = gateway_package.read_text()
    if LOCAL_PATH in source:
        verify(root)
        print("v13 local IDevice package target already present and verified")
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
    source, count = remote.subn(replacement, source, count=1)
    if count != 1:
        die(f"v13 package patch expected one active remote IDevice target, replaced {count}")

    gateway_package.write_text(source)
    verify(root)
    print("v13 DeviceGateway now consumes exactly one locally built patched IDevice XCFramework")


if __name__ == "__main__":
    main()
