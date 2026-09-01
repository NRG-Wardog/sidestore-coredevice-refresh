#!/usr/bin/env python3
"""Pin v12 dependencies, inject local fixed EMProxy, and retain explicit VPN peer."""

from pathlib import Path
import re
import sys

MARKER = "[SS-V12-HYBRID] retaining explicit LocalVPN peer"
# This is the exact RemotePairingKit revision used by current SideStore develop.
# It exports both RPPairing and OpenSSL. The older e72cd027 revision does not
# export the OpenSSL product expected by current minimuxer DeviceGateway.
RPK_REV = "7b9d269ec64027d73a50faa917cb18fa218c1fc9"
IDEVICE_RELEASE = "v0.1.66-ss-61c2704"


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v12_packages.py <minimuxer-root>")

    root = Path(sys.argv[1])
    package = root / "Package.swift"
    gateway_package = root / "DeviceGateway" / "Package.swift"
    network = root / "Sources" / "Services" / "NetworkObserverService.swift"
    for path in (package, gateway_package, network):
        if not path.exists():
            die(f"missing expected file: {path}")

    text = package.read_text()
    remote = re.compile(
        r'(?ms)^\s*\.binaryTarget\(\s*name:\s*"EMProxy",\s*'
        r'url:\s*"https://github\.com/SideStore/em_proxy/releases/download/[^"]+",\s*'
        r'checksum:\s*"[0-9a-f]+"\s*\),'
    )
    local = '''        .binaryTarget(
            name: "EMProxy",
            path: "LocalBinary/EMProxy.xcframework"
        ),'''
    if 'path: "LocalBinary/EMProxy.xcframework"' not in text:
        text, count = remote.subn(local, text, count=1)
        if count != 1:
            die("Could not replace exactly one EMProxy binary target")
    package.write_text(text)

    gateway = gateway_package.read_text()
    branch_dep = '.package(url: "https://github.com/mahee96/RemotePairingKit.git", branch: "main")'
    pinned_dep = f'.package(url: "https://github.com/mahee96/RemotePairingKit.git", revision: "{RPK_REV}")'
    if branch_dep in gateway:
        gateway = gateway.replace(branch_dep, pinned_dep, 1)
    elif pinned_dep not in gateway:
        die("Could not locate RemotePairingKit dependency")
    gateway_package.write_text(gateway)

    network_text = network.read_text()
    old = '''                    let effectiveIp = await isOverridden
                            ? (manager.isOverridePeerIpReachable ? overrideIp : nil)            // when override active, we don't question user intent
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)  // only if not overriden, we try to use auto discovered
'''
    new = '''                    if isOverridden, let overrideIp {
                        debugLog("[SS-V12-HYBRID] retaining explicit LocalVPN peer '\\(overrideIp)' while reachability converges")
                    }
                    let effectiveIp = await isOverridden
                            ? overrideIp
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)
'''
    if MARKER not in network_text:
        if network_text.count(old) != 1:
            die("Could not locate unique LocalVPN override gate")
        network_text = network_text.replace(old, new, 1)
    network.write_text(network_text)

    checks = {
        package: ['path: "LocalBinary/EMProxy.xcframework"'],
        gateway_package: [
            RPK_REV,
            IDEVICE_RELEASE,
            'name: "IDevice"',
            'name: "IdeviceGateway"',
            '.product(name: "OpenSSL",   package: "RemotePairingKit")',
            '.product(name: "RPPairing", package: "RemotePairingKit")',
        ],
        network: [MARKER, "? overrideIp"],
    }
    for path, required in checks.items():
        final = path.read_text()
        missing = [item for item in required if item not in final]
        if missing:
            die(f"v12 package verification failed for {path}: {missing}")

    gateway = gateway_package.read_text()
    forbidden = [
        'path: "LocalBinary/IDevice.xcframework"',
        'branch: "main"',
    ]
    leaked = [item for item in forbidden if item in gateway]
    if leaked:
        die(f"v12 package barrier failed: {leaked}")

    print("v12 package pins, official IDevice, fixed EMProxy, and explicit peer policy verified")


if __name__ == "__main__":
    main()
