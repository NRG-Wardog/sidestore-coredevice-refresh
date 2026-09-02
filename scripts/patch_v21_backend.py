#!/usr/bin/env python3
# V21: force Libimobiledevice and prefer classic Lockdown in composite records.
from pathlib import Path
import re, sys

M = "[SS-V21-LOCKDOWN]"
RPK = "7b9d269ec64027d73a50faa917cb18fa218c1fc9"

def die(x): raise SystemExit(x)

def rep(s, old, new, label):
    n=s.count(old)
    if n != 1: die(f"{label}: anchor count={n}")
    return s.replace(old,new,1)

def sub(s, pattern, new, label):
    s,n=re.subn(pattern,new,s,count=1,flags=re.M|re.S)
    if n != 1: die(f"{label}: pattern count={n}")
    return s

def patch_api(path):
    s=path.read_text()
    if f"{M} backend=libimobiledevice" not in s:
        s=rep(s,
            "    private static var currentBackend: GatewayBackend = .idevice\n",
            "    private static var currentBackend: GatewayBackend = .libimobiledevice\n",
            "backend default")
        s=sub(s,
            r"        let resolvedBackend = backend \?\? currentBackend\n.*?"
            r"        return createInstance\(backend: resolvedBackend\)\n",
            '''        let previousBackend = currentBackend
        let requestedBackend = backend ?? .libimobiledevice
        let resolvedBackend: GatewayBackend = .libimobiledevice
        let resolvedPort = remotePairingPort ?? currentRemotePairingPort

        if requestedBackend != resolvedBackend {
            debugLog("[SS-V21-LOCKDOWN] overriding requested backend=\\(requestedBackend.rawValue)")
        }
        currentBackend = resolvedBackend
        currentRemotePairingPort = resolvedPort
        LibimobiledeviceGateway.shared.setRemotePairingPort(resolvedPort)
        debugLog("[SS-V21-LOCKDOWN] backend=libimobiledevice previous=\\(previousBackend.rawValue) port=\\(resolvedPort)")

        if let cached = cachedInstance, previousBackend == resolvedBackend {
            return cached
        }
        debugLog("[SS-V21-LOCKDOWN] creating fresh Minimuxer instance")
        return createInstance(backend: resolvedBackend)
''',"shared backend")
    path.write_text(s)

def patch_pairing(path):
    s=path.read_text()
    if f"{M} composite records prefer" not in s:
        s=rep(s,'''        let requiredRPKeys = ["private_key", "public_key", "identifier"]
        let missingRPKeys = requiredRPKeys.filter { plist[$0] == nil }
        if missingRPKeys.isEmpty {
            return .rppairing
        }

        let requiredLockdownKeys = [
            "WiFiMACAddress", "SystemBUID", "RootPrivateKey", "HostPrivateKey",
            "HostID", "RootCertificate", "UDID", "EscrowBag", "HostCertificate",
            "DeviceCertificate"
        ]
        let missingLockdownKeys = requiredLockdownKeys.filter { plist[$0] == nil }
        if missingLockdownKeys.isEmpty {
            return .lockdown
        }
''','''        // [SS-V21-LOCKDOWN] composite records prefer the classic
        // Lockdown record and avoid RPPairing createListener self-connect.
        let requiredLockdownKeys = [
            "WiFiMACAddress", "SystemBUID", "RootPrivateKey", "HostPrivateKey",
            "HostID", "RootCertificate", "UDID", "EscrowBag", "HostCertificate",
            "DeviceCertificate"
        ]
        let missingLockdownKeys = requiredLockdownKeys.filter { plist[$0] == nil }
        let requiredRPKeys = ["private_key", "public_key", "identifier"]
        let missingRPKeys = requiredRPKeys.filter { plist[$0] == nil }

        if missingLockdownKeys.isEmpty { return .lockdown }
        if missingRPKeys.isEmpty { return .rppairing }
''',"pairing order")
    path.write_text(s)

def patch_package(path):
    s=path.read_text()
    wanted=('.package(url: "https://github.com/mahee96/RemotePairingKit.git", '
            f'revision: "{RPK}")')
    if wanted not in s:
        s=sub(s,
            r'\.package\(url: "https://github\.com/mahee96/RemotePairingKit\.git", '
            r'(?:branch: "[^"]+"|revision: "[0-9a-f]+")\)',
            wanted,"RPK pin")
    path.write_text(s)

def main():
    if len(sys.argv)!=2: die("usage: patch_v21_backend.py <minimuxer-root>")
    r=Path(sys.argv[1])
    files=[r/"Sources/MinimuxerApi.swift",r/"DeviceGateway/PairingProtocol.swift",
           r/"DeviceGateway/Package.swift"]
    if any(not p.is_file() for p in files): die("missing pinned minimuxer backend files")
    patch_api(files[0]); patch_pairing(files[1]); patch_package(files[2])
    print("v21 backend/pairing patch: PASS")

if __name__=="__main__": main()
