#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_v11_clean_backend.py <Sources/MinimuxerApi.swift>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-V11-CLEAN-RP]"

old_default = "    private static var currentBackend: GatewayBackend = .idevice\n"
new_default = "    private static var currentBackend: GatewayBackend = .libimobiledevice\n"
if old_default in s:
    s = s.replace(old_default, new_default, 1)
elif new_default not in s:
    raise SystemExit("Could not locate Minimuxer.currentBackend default")

old_shared = '''        let resolvedBackend = backend ?? currentBackend
        let resolvedPort = remotePairingPort ?? currentRemotePairingPort

        currentBackend = resolvedBackend
        currentRemotePairingPort = resolvedPort

        switch resolvedBackend {
        case .libimobiledevice:
            LibimobiledeviceGateway.shared.setRemotePairingPort(resolvedPort)
        case .idevice:
            IdeviceGateway.shared.setRemotePairingPort(resolvedPort)
        }

        if let cached = cachedInstance, currentBackend == resolvedBackend {
            return cached
        }
        return createInstance(backend: resolvedBackend)
'''
new_shared = '''        let previousBackend = currentBackend
        let requestedBackend = backend ?? .libimobiledevice
        let resolvedBackend: GatewayBackend = .libimobiledevice
        let resolvedPort = remotePairingPort ?? currentRemotePairingPort

        if requestedBackend != .libimobiledevice {
            debugLog("[SS-V11-CLEAN-RP] overriding requested backend=\\(requestedBackend.rawValue) with libimobiledevice")
        }

        currentBackend = resolvedBackend
        currentRemotePairingPort = resolvedPort

        debugLog("[SS-V11-CLEAN-RP] shared backend=\\(resolvedBackend.rawValue) previous=\\(previousBackend.rawValue) port=\\(resolvedPort)")
        LibimobiledeviceGateway.shared.setRemotePairingPort(resolvedPort)

        // The upstream implementation assigns currentBackend before comparing it,
        // making the cached-instance check tautological. A backend switch could
        // therefore return an instance wired to the previous gateway. Compare to
        // the captured previous backend instead and rebuild when it changes.
        if let cached = cachedInstance, previousBackend == resolvedBackend {
            return cached
        }
        debugLog("[SS-V11-CLEAN-RP] creating fresh Minimuxer instance backend=\\(resolvedBackend.rawValue)")
        return createInstance(backend: resolvedBackend)
'''
if old_shared in s:
    s = s.replace(old_shared, new_shared, 1)
elif marker not in s:
    raise SystemExit("Could not locate Minimuxer.shared backend/cache block")

p.write_text(s)
patched = p.read_text()
required = [
    marker,
    "currentBackend: GatewayBackend = .libimobiledevice",
    "let previousBackend = currentBackend",
    "let requestedBackend = backend ?? .libimobiledevice",
    "let resolvedBackend: GatewayBackend = .libimobiledevice",
    "previousBackend == resolvedBackend",
    "overriding requested backend=",
    "creating fresh Minimuxer instance",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"v11 clean backend verification failed: {missing}")
for forbidden in [
    "currentBackend: GatewayBackend = .idevice",
    "if let cached = cachedInstance, currentBackend == resolvedBackend",
    "let resolvedBackend = backend ?? currentBackend",
]:
    if forbidden in patched:
        raise SystemExit(f"v11 clean backend verification failed; stale code remains: {forbidden}")
print("v11 clean libimobiledevice backend + cache lifecycle fix applied and verified")
