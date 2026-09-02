#!/usr/bin/env python3
# V21: canonical classic pair record and strict live Lockdown validation.
from pathlib import Path
import re, sys

M="[SS-V21-LOCKDOWN]"

def die(x): raise SystemExit(x)

def rep(s,old,new,label):
    n=s.count(old)
    if n!=1: die(f"{label}: anchor count={n}")
    return s.replace(old,new,1)

def sub(s,pattern,new,label):
    s,n=re.subn(pattern,new,s,count=1,flags=re.M|re.S)
    if n!=1: die(f"{label}: pattern count={n}")
    return s

def main():
    if len(sys.argv)!=2: die("usage: patch_v21_gateway.py <LibimobiledeviceGateway.swift>")
    p=Path(sys.argv[1])
    if not p.is_file(): die(f"missing {p}")
    s=p.read_text()

    if f"{M} device lookup start" not in s:
        s=rep(s,
'''        var device: idevice_t? = nil
        let opts = idevice_options(rawValue: IDEVICE_LOOKUP_USBMUX.rawValue | IDEVICE_LOOKUP_NETWORK.rawValue)
''',
'''        debugLog("[SS-V21-LOCKDOWN] device lookup start transport=network-usbmux")
        var device: idevice_t? = nil
        let opts = idevice_options(rawValue: IDEVICE_LOOKUP_USBMUX.rawValue | IDEVICE_LOOKUP_NETWORK.rawValue)
''',"device lookup start")
        s=rep(s,
'''        guard err == IDEVICE_E_SUCCESS, let device = device else {
            throw LibimobiledeviceGatewayError(.connectionFailed, reason: "idevice_new_with_options failed with code \\(err.rawValue)")
        }
        defer { idevice_free(device) }
''',
'''        guard err == IDEVICE_E_SUCCESS, let device = device else {
            debugLog("[SS-V21-LOCKDOWN] device lookup failed code=\\(err.rawValue)")
            throw LibimobiledeviceGatewayError(.connectionFailed, reason: "idevice_new_with_options failed with code \\(err.rawValue)")
        }
        debugLog("[SS-V21-LOCKDOWN] device lookup pass")
        defer { idevice_free(device) }
''',"device lookup result")
        s=rep(s,
'''            var client: lockdownd_client_t? = nil
            let err = lockdownd_client_new_with_handshake(device, &client, nil)
''',
'''            debugLog("[SS-V21-LOCKDOWN] lockdownd handshake start")
            var client: lockdownd_client_t? = nil
            let err = lockdownd_client_new_with_handshake(device, &client, nil)
''',"lockdown start")
        s=rep(s,
'''            guard err == LOCKDOWN_E_SUCCESS, let client = client else {
                throw LibimobiledeviceGatewayError(.connectionFailed, reason: "lockdownd_client_new_with_handshake failed with code \\(err.rawValue)")
            }
            defer { lockdownd_client_free(client) }
''',
'''            guard err == LOCKDOWN_E_SUCCESS, let client = client else {
                debugLog("[SS-V21-LOCKDOWN] lockdownd handshake failed code=\\(err.rawValue)")
                throw LibimobiledeviceGatewayError(.connectionFailed, reason: "lockdownd_client_new_with_handshake failed with code \\(err.rawValue)")
            }
            debugLog("[SS-V21-LOCKDOWN] lockdownd handshake pass")
            defer { lockdownd_client_free(client) }
''',"lockdown result")

    if f"{M} pairing selected=" not in s:
        s=rep(s,
'''        let pairingType = try PairingProtocol.validatePairingFile(from: plist)
        self.pairingFileData = data
        self.pairingFileType = pairingType
        self.isRPPairing = (pairingType == .rppairing)

        if pairingType == .rppairing {
''',
'''        let pairingType = try PairingProtocol.validatePairingFile(from: plist)
        let rpKeys = ["private_key", "public_key", "identifier"]
        let lockdownKeys = [
            "WiFiMACAddress", "SystemBUID", "RootPrivateKey", "HostPrivateKey",
            "HostID", "RootCertificate", "UDID", "EscrowBag", "HostCertificate",
            "DeviceCertificate"
        ]
        let composite = rpKeys.allSatisfy { plist[$0] != nil }
            && lockdownKeys.allSatisfy { plist[$0] != nil }

        if pairingType == .lockdown {
            var classicRecord = plist
            rpKeys.forEach { classicRecord.removeValue(forKey: $0) }
            self.pairingFileData = try PropertyListSerialization.data(
                fromPropertyList: classicRecord, format: .xml, options: 0
            )
        } else {
            self.pairingFileData = data
        }
        self.pairingFileType = pairingType
        self.isRPPairing = (pairingType == .rppairing)
        debugLog("[SS-V21-LOCKDOWN] pairing selected=\\(pairingType.rawValue) composite=\\(composite)")

        if pairingType == .rppairing {
''',"gateway pairing")
        s=rep(s,
'        debugLog("[LibimobiledeviceGateway] Initialized successfully with \\(pairingType.rawValue) pairing for UDID: \\(udid)")\n',
'        debugLog("[SS-V21-LOCKDOWN] gateway initialized pairing=\\(pairingType.rawValue)")\n',
"gateway ready log")

    if f"{M} UniqueDeviceID query start" not in s:
        s=sub(s,
r'''    func syncFetchUDID\(\) throws -> String\? \{
.*?
    \}

    func syncGetLockdownValue''',
'''    func syncFetchUDID() throws -> String? {
        try verifyInitialized()
        debugLog("[SS-V21-LOCKDOWN] UniqueDeviceID query start")
        guard let value = try syncGetLockdownValue(key: "UniqueDeviceID"),
              !value.isEmpty else {
            debugLog("[SS-V21-LOCKDOWN] UniqueDeviceID query failed")
            throw LibimobiledeviceGatewayError(
                .connectionFailed,
                reason: "live Lockdown transport returned no UniqueDeviceID"
            )
        }
        self.cachedUDID = value
        debugLog("[SS-V21-LOCKDOWN] UniqueDeviceID query pass")
        return value
    }

    func syncGetLockdownValue''',"strict UDID")

    p.write_text(s)
    print("v21 libimobiledevice gateway patch: PASS")

if __name__=="__main__": main()
