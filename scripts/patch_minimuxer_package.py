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
    package_changed = False
else:
    s, n = pattern.subn(local, s, count=1)
    if n != 1:
        raise SystemExit("Could not replace exactly one active remote EMProxy binaryTarget")
    package_changed = True

if pattern.search(s):
    raise SystemExit("Verification failed: active remote EMProxy binaryTarget remains")
if s.count(local_path) != 1:
    raise SystemExit(f"Verification failed: expected one local EMProxy path, found {s.count(local_path)}")

p.write_text(s)

# A user-entered local-VPN peer is authoritative. The stock code still gates that
# explicit override on an instantaneous reachability probe. During SideStore boot
# EMProxy/WireGuard can still be converging, so this clears DeviceEndpoint to nil
# before the actual RemotePairing transport gets a chance to connect. Keep the
# explicit peer and let the real transport determine readiness instead.
network_observer = p.parent / "Sources" / "Services" / "NetworkObserverService.swift"
if not network_observer.exists():
    raise SystemExit(f"Could not find NetworkObserverService.swift at {network_observer}")

net = network_observer.read_text()
net_marker = "[SS-LOCALVPN-OVERRIDE] retaining explicit tunnel peer"
old_override = '''                    let effectiveIp = await isOverridden
                            ? (manager.isOverridePeerIpReachable ? overrideIp : nil)            // when override active, we don't question user intent
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)  // only if not overriden, we try to use auto discovered
'''
new_override = '''                    if isOverridden, let overrideIp {
                        debugLog("[SS-LOCALVPN-OVERRIDE] retaining explicit tunnel peer '\(overrideIp)' before reachability stabilizes")
                    }

                    let effectiveIp = await isOverridden
                            ? overrideIp                                                        // explicit user intent is authoritative
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)  // auto-discovered peers still require reachability
'''

if net_marker not in net:
    if old_override not in net:
        raise SystemExit("Could not locate local-VPN override reachability gate")
    net = net.replace(old_override, new_override, 1)
    network_observer.write_text(net)

verified_net = network_observer.read_text()
required_net = [
    net_marker,
    "? overrideIp",
    "manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil",
]
missing = [x for x in required_net if x not in verified_net]
if missing:
    raise SystemExit(f"Local-VPN override verification failed: {missing}")
if "manager.isOverridePeerIpReachable ? overrideIp : nil" in verified_net:
    raise SystemExit("Local-VPN override is still gated by startup reachability")

# Rust's idevice logger is guarded by std::sync::Once. SideStore's first call is
# normally setLogging(false), which permanently initializes it with zero output
# layers and suppresses the TLS/CDTunnel stage diagnostics in release builds.
# Always install only the ERROR layer. Our forensic events use tracing::error!
# and intentionally redact PSKs, pairing keys, UDIDs, and packet bodies.
gateway = p.parent / "DeviceGateway" / "idevice" / "IdeviceGateway.swift"
if not gateway.exists():
    raise SystemExit(f"Could not find IdeviceGateway.swift at {gateway}")

gw = gateway.read_text()
gw_marker = "[SS-FORENSIC-LOG] Rust error-stage tracing forced active"
old_logging = '''    public func setLogging(_ enabled: Bool) {
        DeviceGatewayLogging.setLogging(enabled)
        debugLog("[IdeviceGateway] setLogging(\(enabled)) called")
        idevice_init_logger(enabled ? IdeviceLogLevel(rawValue: 1) : IdeviceLogLevel(rawValue: 0), IdeviceLogLevel(rawValue: 0), nil)
        #if DEBUG
        // just comment/uncomment to override above set logging level during local debugging
//        idevice_init_logger(IdeviceLogLevel(rawValue: 4), IdeviceLogLevel(rawValue: 0), nil)
        #endif
    }'''
new_logging = '''    public func setLogging(_ enabled: Bool) {
        DeviceGatewayLogging.setLogging(enabled)
        debugLog("[IdeviceGateway] setLogging(\(enabled)) called")

        // idevice_init_logger is guarded by Rust's std::sync::Once. The first
        // disabled call would otherwise make later diagnostics impossible.
        _ = idevice_init_logger(
            IdeviceLogLevel(rawValue: 1),
            IdeviceLogLevel(rawValue: 0),
            nil
        )
        debugLog("[SS-FORENSIC-LOG] Rust error-stage tracing forced active; requestedVerbose=\(enabled)")
    }'''

if gw_marker not in gw:
    if old_logging not in gw:
        raise SystemExit("Could not locate IdeviceGateway.setLogging implementation")
    gw = gw.replace(old_logging, new_logging, 1)
    gateway.write_text(gw)

verified_gw = gateway.read_text()
required_gw = [
    gw_marker,
    "IdeviceLogLevel(rawValue: 1)",
    "requestedVerbose=",
]
missing = [x for x in required_gw if x not in verified_gw]
if missing:
    raise SystemExit(f"Forensic logger verification failed: {missing}")
if "enabled ? IdeviceLogLevel(rawValue: 1) : IdeviceLogLevel(rawValue: 0)" in verified_gw:
    raise SystemExit("Forensic logger is still vulnerable to disabled-first initialization")

if package_changed:
    print("Patched minimuxer Package.swift to use exactly one local diagnostic EMProxy XCFramework")
else:
    print("Minimuxer Package.swift already uses exactly one local EMProxy target")
print("Patched local-VPN override handling to retain the explicit peer during startup")
print("Forced privacy-safe Rust error-stage diagnostics for deterministic TLS/CDTunnel evidence")
