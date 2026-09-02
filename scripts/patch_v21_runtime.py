#!/usr/bin/env python3
# V21: stabilize endpoint publication and instrument fake usbmuxd startup.
from pathlib import Path
import sys

M="[SS-V21-LOCKDOWN]"

def die(x): raise SystemExit(x)

def rep(s,old,new,label):
    n=s.count(old)
    if n!=1: die(f"{label}: anchor count={n}")
    return s.replace(old,new,1)

def patch_network(p):
    s=p.read_text()
    if f"{M} retaining explicit LocalVPN peer" not in s:
        s=rep(s,
'''                    let effectiveIp = await isOverridden
                            ? (manager.isOverridePeerIpReachable ? overrideIp : nil)            // when override active, we don't question user intent
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)  // only if not overriden, we try to use auto discovered
''',
'''                    if isOverridden {
                        debugLog("[SS-V21-LOCKDOWN] retaining explicit LocalVPN peer while reachability converges")
                    }
                    let effectiveIp = await isOverridden
                            ? overrideIp
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)
''',"network override")
    p.write_text(s)

def patch_impl(p):
    s=p.read_text()
    if f"{M} network monitor started" not in s:
        s=rep(s,
"        await self.network.start()\n\n        // actor serialization scope\n",
'        await self.network.start()\n'
'        debugLog("[SS-V21-LOCKDOWN] network monitor started")\n'
'        await self.network.refreshEndpoint()\n\n'
'        // actor serialization scope\n',
"network refresh")
        s=rep(s,
'''        // retarget usbmuxd to our fake usbmuxd server (over network)
        retargetUsbmuxdAddr()
        // start our fake usbmuxd server for lockdown protocol based clients if required
        try await restartMuxerServer()
''',
'''        debugLog("[SS-V21-LOCKDOWN] gateway started protocol=\\(self.gateway.getPairingFileType().rawValue)")
        // retarget usbmuxd to our fake usbmuxd server (over network)
        retargetUsbmuxdAddr()
        debugLog("[SS-V21-LOCKDOWN] usbmuxd socket retargeted")
        await self.network.refreshEndpoint()
        // start our fake usbmuxd server for lockdown protocol based clients if required
        try await restartMuxerServer()
        debugLog("[SS-V21-LOCKDOWN] fake usbmuxd ready=\\(self.proxyServer.isListening)")
''',"usbmux startup")
    p.write_text(s)

def patch_usbmux(p):
    s=p.read_text()
    if f"{M} fake usbmuxd listening" not in s:
        s=rep(s,
'''                    case .ready:
                        verboseLog("[minimuxer] UsbmuxdProxyServer (NWListener) bound successfully to \\(MinimuxerConstants.usbmuxdHost):\\(MinimuxerConstants.usbmuxdPort)")
                        self.isListening = true
                        self.started = true
''',
'''                    case .ready:
                        verboseLog("[minimuxer] UsbmuxdProxyServer (NWListener) bound successfully to \\(MinimuxerConstants.usbmuxdHost):\\(MinimuxerConstants.usbmuxdPort)")
                        debugLog("[SS-V21-LOCKDOWN] fake usbmuxd listening")
                        self.isListening = true
                        self.started = true
''',"usbmux ready")
        s=rep(s,
'''            case "ListDevices":
                guard let tunnelIfaceIp = currentDeviceIp else {
                    return ["DeviceList": []]
                }
''',
'''            case "ListDevices":
                guard let tunnelIfaceIp = currentDeviceIp else {
                    debugLog("[SS-V21-LOCKDOWN] usbmux ListDevices peer unavailable")
                    return ["DeviceList": []]
                }
                debugLog("[SS-V21-LOCKDOWN] usbmux ListDevices peer configured")
''',"ListDevices")
        s=rep(s,
'            case "ReadPairRecord":\n'
'                guard let pairingData = self.gateway.pairingFileData else {\n',
'            case "ReadPairRecord":\n'
'                debugLog("[SS-V21-LOCKDOWN] usbmux ReadPairRecord")\n'
'                guard let pairingData = self.gateway.pairingFileData else {\n',
"ReadPairRecord")
    p.write_text(s)

def main():
    if len(sys.argv)!=2: die("usage: patch_v21_runtime.py <minimuxer-root>")
    r=Path(sys.argv[1])
    files=[r/"Sources/Services/NetworkObserverService.swift",
           r/"Sources/MinimuxerImpl.swift",
           r/"Sources/Services/UsbmuxdProxyServer.swift"]
    if any(not p.is_file() for p in files): die("missing pinned minimuxer runtime files")
    patch_network(files[0]); patch_impl(files[1]); patch_usbmux(files[2])
    print("v21 runtime/usbmux patch: PASS")

if __name__=="__main__": main()
