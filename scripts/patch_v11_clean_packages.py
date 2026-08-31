#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_v11_clean_packages.py <minimuxer-root>")

root = Path(sys.argv[1])
package = root / "Package.swift"
gateway_package = root / "DeviceGateway" / "Package.swift"
network = root / "Sources" / "Services" / "NetworkObserverService.swift"
for p in (package, gateway_package, network):
    if not p.exists():
        raise SystemExit(f"missing expected file: {p}")

s = package.read_text()
remote = re.compile(
    r'(?ms)^\s*\.binaryTarget\(\s*name:\s*"EMProxy",\s*'
    r'url:\s*"https://github\.com/SideStore/em_proxy/releases/download/[^"]+",\s*'
    r'checksum:\s*"[0-9a-f]+"\s*\),'
)
local = '''        .binaryTarget(
            name: "EMProxy",
            path: "LocalBinary/EMProxy.xcframework"
        ),'''
if 'path: "LocalBinary/EMProxy.xcframework"' not in s:
    s, n = remote.subn(local, s, count=1)
    if n != 1:
        raise SystemExit("Could not replace exactly one EMProxy binary target")
package.write_text(s)

g = gateway_package.read_text()
old_dep = '.package(url: "https://github.com/mahee96/RemotePairingKit.git", branch: "main")'
new_dep = '.package(url: "https://github.com/mahee96/RemotePairingKit.git", revision: "e72cd0272ab7b4548b5cd22ed4a81008b2b52717")'
if old_dep in g:
    g = g.replace(old_dep, new_dep, 1)
elif new_dep not in g:
    raise SystemExit("Could not locate RemotePairingKit dependency")
gateway_package.write_text(g)

n = network.read_text()
marker = "[SS-V11-CLEAN-RP] retaining explicit LocalVPN peer"
old = '''                    let effectiveIp = await isOverridden
                            ? (manager.isOverridePeerIpReachable ? overrideIp : nil)            // when override active, we don't question user intent
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)  // only if not overriden, we try to use auto discovered
'''
new = '''                    if isOverridden, let overrideIp {
                        debugLog("[SS-V11-CLEAN-RP] retaining explicit LocalVPN peer '\\(overrideIp)' while reachability converges")
                    }
                    let effectiveIp = await isOverridden
                            ? overrideIp
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)
'''
if marker not in n:
    if old not in n:
        raise SystemExit("Could not locate LocalVPN override gate")
    n = n.replace(old, new, 1)
network.write_text(n)

checks = {
    package: ['path: "LocalBinary/EMProxy.xcframework"'],
    gateway_package: ["e72cd0272ab7b4548b5cd22ed4a81008b2b52717", 'name: "libimobiledevice"', 'name: "OpenSSL"'],
    network: [marker, "? overrideIp"],
}
for path, required in checks.items():
    text = path.read_text()
    missing = [x for x in required if x not in text]
    if missing:
        raise SystemExit(f"package verification failed for {path}: {missing}")

g = gateway_package.read_text()
for forbidden in ['path: "LocalBinary/IDevice.xcframework"', 'path: "LocalBinary/OpenSSL.xcframework"', 'branch: "main"']:
    if forbidden in g:
        raise SystemExit(f"clean v11 package barrier failed: {forbidden}")
print("v11 clean package pins, local EMProxy, and LocalVPN peer policy applied and verified")
