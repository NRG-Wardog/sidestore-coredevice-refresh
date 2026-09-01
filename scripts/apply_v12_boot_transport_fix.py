#!/usr/bin/env python3
"""Require a live non-empty UDID and verify the v12 hybrid gateway architecture."""

from pathlib import Path
import sys

BOOT_MARKER = "[SS-BOOT-FIX] strict transport validation active"
V12_MARKER = "[SS-V12-HYBRID]"


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def verify_gateway(app_boot_path: Path) -> None:
    sidestore_root = app_boot_path.parent.parent
    candidates = [
        sidestore_root / "Dependencies" / "minimuxer",
        sidestore_root.parent / "minimuxer",
    ]
    root = next((item for item in candidates if item.exists()), None)
    if root is None:
        die("Could not locate minimuxer for v12 architecture verification")

    api = root / "Sources" / "MinimuxerApi.swift"
    gateway = root / "DeviceGateway" / "idevice" / "IdeviceGateway.swift"
    if not api.exists() or not gateway.exists():
        die("Missing v12 backend/gateway inputs")

    api_text = api.read_text()
    gateway_text = gateway.read_text()
    required_api = [
        V12_MARKER,
        "let resolvedBackend: GatewayBackend = .idevice",
        "previousBackend == resolvedBackend",
    ]
    required_gateway = [
        V12_MARKER,
        "HYBRID_FILE has_rp=",
        "LOCKDOWN_SESSION_AND_COREDEVICE_START",
        "COREDEVICE_TUNNEL_SUCCESS",
        "RP_FALLBACK_START",
        "STRICT_UDID_QUERY_SUCCESS",
    ]
    missing = [item for item in required_api if item not in api_text]
    missing += [item for item in required_gateway if item not in gateway_text]
    if missing:
        die(f"v12 gateway verification failed: {missing}")

    forbidden = [
        "SS-V11-CLEAN-RP",
        "SS-V10-LOCKDOWN-BOOTSTRAP",
        "SS-V9-COREDEVICE",
        "SS-ADAPT",
        "lockdownd_pair(",
        "PAIR_REQUEST_START",
    ]
    leaked = [item for item in forbidden if item in api_text or item in gateway_text]
    if leaked:
        die(f"v12 gateway contains forbidden legacy code: {leaked}")


def verify_boot(text: str) -> None:
    required = [
        BOOT_MARKER,
        "guard let deviceUDID = fetchedUDID, !deviceUDID.isEmpty",
        "UDID present=true length=\\(deviceUDID.count)",
        "PAIRING FILE IS VALID but TRANSPORT FAILED",
        "throw error",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        die(f"v12 boot validation failed: {missing}")
    forbidden = [
        'SUCCEEDED. UDID: \\(deviceUDID',
        'UDID: \\(deviceUDID',
        'UDID: \\(fetchedUDID',
    ]
    leaked = [item for item in forbidden if item in text]
    if leaked:
        die(f"v12 boot privacy validation failed: {leaked}")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: apply_v12_boot_transport_fix.py <SideStore/AppBootManager.swift>")

    path = Path(sys.argv[1])
    text = path.read_text()
    if BOOT_MARKER in text:
        verify_boot(text)
        verify_gateway(path)
        print("v12 strict boot transport validation already present and verified")
        return

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
    new = '''        // Validate both the imported pairing material and the complete live
        // transport. A nil/empty UDID is never success and raw UDID bytes are
        // intentionally excluded from logs.
        debugLog("[SS-BOOT-FIX] strict transport validation active")
        do {
            debugLog("[AppBootManager] startMinimuxer(): Minimuxer fetchUDID() based connection starting...")
            let fetchedUDID = try await fetchUDID()
            guard let deviceUDID = fetchedUDID, !deviceUDID.isEmpty else {
                throw NSError(
                    domain: "com.SideStore.RemotePairingTransport",
                    code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "Hybrid transport returned no live device UDID."]
                )
            }
            debugLog("[SS-BOOT-FIX] Minimuxer fetchUDID transport validation SUCCEEDED. UDID present=true length=\\(deviceUDID.count)")
            self.needsPairingPrompt = false
        } catch {
            if error.isMinimuxerPairingFile {
                debugLog("[AppBootManager] startMinimuxer(): pairing material validation FAILED. \\(error)")
                self.needsPairingPrompt = true
                throw error
            } else {
                self.needsPairingPrompt = false
                debugLog("[SS-BOOT-FIX] Minimuxer fetchUDID FAILED: PAIRING FILE IS VALID but TRANSPORT FAILED. \\(error)")
                throw error
            }
        }
'''
    count = text.count(old)
    if count != 1:
        die(f"Could not locate unique AppBootManager fetchUDID block; found {count}")
    text = text.replace(old, new, 1)
    path.write_text(text)
    verify_boot(path.read_text())
    verify_gateway(path)
    print("v12 strict live-UDID boot validation applied and gateway architecture verified")


if __name__ == "__main__":
    main()
