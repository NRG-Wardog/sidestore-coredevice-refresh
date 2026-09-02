#!/usr/bin/env python3
# V21 source barrier: no dead NAT/matrix route and no cached transport success.
from pathlib import Path
import sys

M="[SS-V21-LOCKDOWN]"
RPK="7b9d269ec64027d73a50faa917cb18fa218c1fc9"

def die(x): raise SystemExit(x)

def main():
    if len(sys.argv)!=2: die("usage: verify_v21_lockdown.py <minimuxer-root>")
    r=Path(sys.argv[1])
    files={
        "api":r/"Sources/MinimuxerApi.swift",
        "pair":r/"DeviceGateway/PairingProtocol.swift",
        "gw":r/"DeviceGateway/libimobiledevice/LibimobiledeviceGateway.swift",
        "net":r/"Sources/Services/NetworkObserverService.swift",
        "impl":r/"Sources/MinimuxerImpl.swift",
        "mux":r/"Sources/Services/UsbmuxdProxyServer.swift",
        "gpkg":r/"DeviceGateway/Package.swift",
        "pkg":r/"Package.swift",
    }
    if any(not p.is_file() for p in files.values()): die("v21 source layout incomplete")
    t={k:p.read_text() for k,p in files.items()}
    req={
        "api":["resolvedBackend: GatewayBackend = .libimobiledevice",
               f"{M} backend=libimobiledevice","previousBackend == resolvedBackend"],
        "pair":[f"{M} composite records prefer",
                "if missingLockdownKeys.isEmpty","if missingRPKeys.isEmpty"],
        "gw":[f"{M} pairing selected=",f"{M} device lookup pass",
              f"{M} lockdownd handshake pass",f"{M} UniqueDeviceID query pass",
              f"{M} GetValue start key=",f"{M} GetValue pass key=",
              "classicRecord.removeValue"],
        "net":[f"{M} retaining explicit LocalVPN peer","? overrideIp"],
        "impl":[f"{M} network monitor started",f"{M} usbmuxd socket retargeted",
                f"{M} fake usbmuxd ready="],
        "mux":[f"{M} fake usbmuxd listening",
               f"{M} usbmux ListDevices peer configured",
               f"{M} usbmux ReadPairRecord"],
        "gpkg":[RPK,"SideStore/idevice/releases/download/",
                "SideStore/libimobiledevice-xcframework/releases/download/"],
        "pkg":["SideStore/em_proxy/releases/download/",
               'LibimobiledeviceGateway", package: "DeviceGateway'],
    }
    for key,needles in req.items():
        missing=[x for x in needles if x not in t[key]]
        if missing: die(f"verify {key}: missing {missing}")

    lp=t["pair"].find("if missingLockdownKeys.isEmpty")
    rp=t["pair"].find("if missingRPKeys.isEmpty")
    if lp<0 or rp<0 or lp>=rp: die("composite record does not prefer Lockdown")

    active="\n".join(t.values())
    stale=[
        "resolvedBackend: GatewayBackend = .idevice",
        "currentBackend == resolvedBackend",
        "return cachedUDID",
        'branch: "main"',
        'path: "LocalBinary/IDevice.xcframework"',
        'path: "LocalBinary/EMProxy.xcframework"',
        "EMP-NAT44","EMP-TRANSIT","v14-rp-protocol-matrix",
    ]
    leaked=[x for x in stale if x in active]
    if leaked: die(f"stale active route/code: {leaked}")

    marker_lines="\n".join(x for x in active.splitlines() if M in x)
    secrets=["RootPrivateKey","HostPrivateKey","EscrowBag","UDID=","private_key="]
    leaked=[x for x in secrets if x in marker_lines]
    if leaked: die(f"sensitive diagnostic marker: {leaked}")

    print("v21 Lockdown-first source verification: PASS")

if __name__=="__main__": main()
