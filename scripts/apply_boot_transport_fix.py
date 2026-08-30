#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_boot_transport_fix.py <SideStore/AppBootManager.swift>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-BOOT-FIX] strict transport validation active"

if marker in s:
    required = [
        "guard let deviceUDID",
        "deviceUDID.isEmpty",
        "UDID present=true length=",
        "PAIRING FILE IS VALID but TRANSPORT FAILED",
        "throw error",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"Boot transport fix marker present but patch incomplete: {missing}")
    if "SUCCEEDED. UDID: \\(deviceUDID" in s:
        raise SystemExit("Raw UDID success logging remains")
    print("Strict boot transport validation already present and verified")
    raise SystemExit(0)

old = '''        // Validate the pairing by trying to fetch the UDID
        do {
            debugLog("[AppBootManager] startMinimuxer(): Minimuxer fetchUDID() based connection starting...")
            let deviceUDID = try await fetchUDID()
            debugLog("[AppBootManager] startMinimuxer(): Minimuxer fetchUDID() based connection test SUCCEEDED. UDID: \\(deviceUDID ?? \"nil\")")
            self.needsPairingPrompt = false
        } catch {
            if error.isMinimuxerPairingFile {
                debugLog("[AppBootManager] startMinimuxer(): Minimuxer fetchUDID() based connection test FAILED. \\(error)")
                self.needsPairingPrompt = true
                throw error
            } else {
                debugLog("[AppBootManager] startMinimuxer(): Minimuxer fetchUDID() based connection test FAILED but PAIRING FILE IS VALID. \\(error)")
            }
        }
'''

new = '''        // Validate the pairing and the complete RemotePairing transport by fetching
        // a non-empty UDID. A nil/empty value is not success: it means the transport
        // never reached a usable lockdown/RSD session. Never log the UDID itself.
        debugLog("[SS-BOOT-FIX] strict transport validation active")
        do {
            debugLog("[AppBootManager] startMinimuxer(): Minimuxer fetchUDID() based connection starting...")
            let fetchedUDID = try await fetchUDID()
            guard let deviceUDID = fetchedUDID, !deviceUDID.isEmpty else {
                throw NSError(
                    domain: "com.SideStore.RemotePairingTransport",
                    code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "RemotePairing transport returned no device UDID."]
                )
            }
            debugLog("[SS-BOOT-FIX] Minimuxer fetchUDID transport validation SUCCEEDED. UDID present=true length=\\(deviceUDID.count)")
            self.needsPairingPrompt = false
        } catch {
            if error.isMinimuxerPairingFile {
                debugLog("[AppBootManager] startMinimuxer(): Minimuxer fetchUDID() based connection test FAILED. \\(error)")
                self.needsPairingPrompt = true
                throw error
            } else {
                // The pairing file is valid, but the transport is not. Propagate the
                // error so boot cannot report a false-ready minimuxer state.
                self.needsPairingPrompt = false
                debugLog("[SS-BOOT-FIX] Minimuxer fetchUDID FAILED: PAIRING FILE IS VALID but TRANSPORT FAILED. \\(error)")
                throw error
            }
        }
'''

if old not in s:
    raise SystemExit("Could not locate exact AppBootManager fetchUDID validation block")
s = s.replace(old, new, 1)
p.write_text(s)

patched = p.read_text()
required = [
    marker,
    "guard let deviceUDID = fetchedUDID, !deviceUDID.isEmpty",
    "UDID present=true length=\\(deviceUDID.count)",
    "PAIRING FILE IS VALID but TRANSPORT FAILED",
    "throw error",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"Boot transport fix verification failed: {missing}")

for forbidden in [
    'SUCCEEDED. UDID: \\(deviceUDID',
    'UDID: \\(deviceUDID',
    'UDID: \\(fetchedUDID',
]:
    if forbidden in patched:
        raise SystemExit(f"Privacy verification failed: raw UDID logging remains: {forbidden}")

print("Strict AppBootManager transport validation applied and verified")
