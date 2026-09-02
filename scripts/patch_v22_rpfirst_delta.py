#!/usr/bin/env python3
"""Turn the verified V21 adaptive minimuxer patch into V22 RP-first policy."""
from pathlib import Path
import sys

MARK='[SS-V22-RPFIRST]'

def die(s): raise SystemExit(s)
def once(s,a,b,label):
    n=s.count(a)
    if n != 1: die(f'{label}: expected 1 anchor, found {n}')
    return s.replace(a,b,1)

def main():
    if len(sys.argv)!=2: die('usage: patch_v22_rpfirst_delta.py <minimuxer-root>')
    root=Path(sys.argv[1])
    p=root/'DeviceGateway/PairingProtocol.swift'
    g=root/'DeviceGateway/idevice/IdeviceGateway.swift'
    for f in (p,g):
        if not f.is_file(): die(f'missing {f}')

    ps=p.read_text()
    relaxed='''        let requiredLockdownKeys = [\n            "WiFiMACAddress", "SystemBUID", "RootPrivateKey", "HostPrivateKey",\n            "HostID", "RootCertificate", "HostCertificate", "DeviceCertificate"\n        ]\n'''
    strict='''        let requiredLockdownKeys = [\n            "WiFiMACAddress", "SystemBUID", "RootPrivateKey", "HostPrivateKey",\n            "HostID", "RootCertificate", "UDID", "EscrowBag", "HostCertificate",\n            "DeviceCertificate"\n        ]\n'''
    if relaxed not in ps:
        ps=once(ps,strict,relaxed,'PairingProtocol optional fields')
        p.write_text(ps)

    s=g.read_text()
    if MARK in s:
        verify(p.read_text(),s); print('v22 RP-first delta already present and verified'); return

    strict_g='''            let lockdownKeys = [\n                "WiFiMACAddress", "SystemBUID", "RootPrivateKey", "HostPrivateKey",\n                "HostID", "RootCertificate", "UDID", "EscrowBag", "HostCertificate",\n                "DeviceCertificate"\n            ]\n'''
    relaxed_g='''            let lockdownKeys = [\n                "WiFiMACAddress", "SystemBUID", "RootPrivateKey", "HostPrivateKey",\n                "HostID", "RootCertificate", "HostCertificate", "DeviceCertificate"\n            ]\n'''
    s=once(s,strict_g,relaxed_g,'gateway optional fields')

    anchor='''            let pairingType = try Self.validatePairingFile(from: plist)\n'''
    timeout='''            idevice_set_global_timeout(15)\n            debugLog("[SS-V22-RPFIRST] native connect timeout=15s")\n            let pairingType = try Self.validatePairingFile(from: plist)\n'''
    s=once(s,anchor,timeout,'global timeout')

    old='''        if compositePairingFile {\n            // Device-specific evidence wins: this iPhone previously reached\n            // CoreDeviceProxy StartService. Keep stock RP as a second, clean route.\n            debugLog("[SS-V21-ADAPT] composite route=coredevice-first rp-fallback")\n            do {\n                try ensureCoreDeviceConnection()\n                return\n            } catch {\n                let coreError = error\n                debugLog("[SS-V21-ADAPT] coredevice-first FAIL; trying stock tunnel_create_rppairing: \\(coreError.localizedDescription)")\n                invalidateConnection()\n                do {\n                    try ensureRPConnection()\n                    activeTunnelTransport = "stock-rppairing"\n                    debugLog("[SS-V21-ADAPT] stock RPPairing PASS")\n                    debugLog("[SS-V21-ADAPT] RSD_HANDSHAKE_PASS transport=stock-rppairing")\n                    return\n                } catch {\n                    throw IdeviceGatewayError(\n                        .connectionFailed,\n                        reason: "Adaptive transport failed. CoreDeviceProxy: \\(coreError.localizedDescription); stock RPPairing: \\(error.localizedDescription)"\n                    )\n                }\n            }\n        }\n'''
    new='''        if compositePairingFile {\n            debugLog("[SS-V22-RPFIRST] composite route=stock-rppairing-first coredevice-fallback")\n            do {\n                debugLog("[SS-V22-RPFIRST] RP_PRIMARY_START peer=\\(deviceEndpointIp ?? \"nil\") port=\\(remotePairingPort)")\n                try ensureRPConnection()\n                activeTunnelTransport = "stock-rppairing"\n                debugLog("[SS-V22-RPFIRST] RP_PRIMARY_PASS")\n                return\n            } catch {\n                let rpError = error\n                debugLog("[SS-V22-RPFIRST] RP_PRIMARY_FAIL; CoreDevice fallback: \\(rpError.localizedDescription)")\n                invalidateConnection()\n                do {\n                    try ensureCoreDeviceConnection()\n                    debugLog("[SS-V22-RPFIRST] COREDEVICE_FALLBACK_PASS")\n                    return\n                } catch {\n                    throw IdeviceGatewayError(\n                        .connectionFailed,\n                        reason: "Adaptive transport failed. stock RPPairing: \\(rpError.localizedDescription); CoreDeviceProxy: \\(error.localizedDescription)"\n                    )\n                }\n            }\n        }\n'''
    s=once(s,old,new,'adaptive composite route')
    g.write_text(s)
    verify(p.read_text(),s)
    print('v22 RP-first delta applied and verified')

def verify(p,s):
    if p.find('if missingRPKeys.isEmpty') >= p.find('if missingLockdownKeys.isEmpty'): die('RP-first PairingProtocol order changed')
    req=[MARK,'idevice_set_global_timeout(15)','composite route=stock-rppairing-first coredevice-fallback','RP_PRIMARY_START','RP_PRIMARY_PASS','COREDEVICE_FALLBACK_PASS','tunnel_create_rppairing(','tunnel_create_usb(provider, &adapter, &handshake)','RSD Lockdown returned no live UniqueDeviceID']
    miss=[x for x in req if x not in s]
    if miss: die(f'missing V22 gates: {miss}')
    if 'composite route=coredevice-first rp-fallback' in s: die('old CoreDevice-first route remains')
    if '"RootCertificate", "UDID", "EscrowBag", "HostCertificate"' in p or '"RootCertificate", "UDID", "EscrowBag", "HostCertificate"' in s: die('optional classic fields still mandatory')

if __name__=='__main__': main()
