#!/usr/bin/env python3
"""Stop AppBootManager from logging nil UDID as a successful transport test."""
from pathlib import Path
import sys

MARK='[SS-V23-RPDIAG]'
def die(s): raise SystemExit(s)
def main():
    if len(sys.argv)!=2: die('usage: patch_v23_boot_udid_diag.py <AppBootManager.swift>')
    p=Path(sys.argv[1])
    s=p.read_text()
    if 'NIL_UDID_TRANSPORT_FAIL' in s:
        print('v23 boot UDID diagnostic already present'); return
    old='''            let deviceUDID = try await fetchUDID()\n            debugLog("[AppBootManager] startMinimuxer(): Minimuxer fetchUDID() based connection test SUCCEEDED. UDID: \\(deviceUDID ?? "nil")")\n            self.needsPairingPrompt = false\n'''
    new='''            let deviceUDID = try await fetchUDID()\n            if let deviceUDID, !deviceUDID.isEmpty {\n                debugLog("[AppBootManager] startMinimuxer(): Minimuxer fetchUDID() based connection test SUCCEEDED. UDID present=true")\n                self.needsPairingPrompt = false\n            } else {\n                debugLog("[SS-V23-RPDIAG] NIL_UDID_TRANSPORT_FAIL fetchUDID returned nil/empty; connection test is NOT successful")\n            }\n'''
    n=s.count(old)
    if n!=1: die(f'boot UDID anchor expected 1, found {n}')
    p.write_text(s.replace(old,new,1))
    print('v23 boot nil-UDID diagnostic applied')
if __name__=='__main__': main()
