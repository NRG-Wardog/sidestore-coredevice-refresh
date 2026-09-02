#!/usr/bin/env python3
"""Patch pinned minimuxer with an adaptive composite-pairing transport.

Policy:
  * Keep upstream PairingProtocol semantics unchanged. A merged iLoader record
    still classifies as RPPairing, matching current SideStore/minimuxer.
  * Detect that the same plist also contains a complete classic Lockdown record.
  * For a composite record, try CoreDeviceProxy over the classic Lockdown record
    first. This is the route that the device trace already proved can reach
    StartService(CoreDeviceProxy).
  * If CoreDeviceProxy fails, fall back to the stock, unmodified
    tunnel_create_rppairing implementation from the released IDevice binary.
  * RP-only records use stock RPPairing; Lockdown-only records use CoreDeviceProxy.
  * Once either route yields Adapter + RsdHandshake, all modern services use RSD.

No EMProxy NAT, no custom TLS/CDTunnel implementation, and no v14 route matrix.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
from typing import NoReturn

MARKER = "[SS-V21-ADAPT]"


def die(message: str) -> NoReturn:
    raise SystemExit(message)


def require_file(path: Path) -> None:
    if not path.is_file():
        die(f"missing expected file: {path}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_network_observer(path: Path) -> None:
    text = path.read_text()
    if f"{MARKER} retaining explicit LocalVPN peer" in text:
        return
    old = '''                    let effectiveIp = await isOverridden
                            ? (manager.isOverridePeerIpReachable ? overrideIp : nil)            // when override active, we don't question user intent
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)  // only if not overriden, we try to use auto discovered
'''
    new = '''                    if isOverridden, let overrideIp {
                        debugLog("[SS-V21-ADAPT] retaining explicit LocalVPN peer while reachability converges")
                    }
                    let effectiveIp = await isOverridden
                            ? overrideIp
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)
'''
    text = replace_once(text, old, new, "NetworkObserver explicit peer")
    path.write_text(text)


def patch_idevice_gateway(path: Path) -> None:
    text = path.read_text()
    if f"{MARKER} adaptive transport READY" in text:
        verify_gateway(text)
        return

    state_old = '''    private var pairingFile: OpaquePointer? = nil
    private var adapter: OpaquePointer? = nil
'''
    state_new = '''    private var pairingFile: OpaquePointer? = nil
    // [SS-V21-ADAPT] A merged iLoader plist carries both identities. Keep the
    // classic half as bytes so each consuming TcpProvider gets a fresh handle.
    private var lockdownPairingFileData: Data? = nil
    private var compositePairingFile = false
    private var activeTunnelTransport: String? = nil
    private var adapter: OpaquePointer? = nil
'''
    text = replace_once(text, state_old, state_new, "gateway state")

    text, cleanup_n = re.subn(
        r'(        isInitialized = false\n        self\.pairingFileData = nil\n)',
        r'\1        self.lockdownPairingFileData = nil\n        self.compositePairingFile = false\n        self.activeTunnelTransport = nil\n',
        text,
        count=1,
    )
    if cleanup_n != 1:
        die(f"gateway cleanup replacement count={cleanup_n}")

    start_old = '''            let pairingType = try Self.validatePairingFile(from: plist)
            self.pairingFileData = data
            self.pairingFileType = pairingType
            isRPPairing = (pairingType == .rppairing)
'''
    start_new = '''            let pairingType = try Self.validatePairingFile(from: plist)

            let rpKeys = ["private_key", "public_key", "identifier"]
            let lockdownKeys = [
                "WiFiMACAddress", "SystemBUID", "RootPrivateKey", "HostPrivateKey",
                "HostID", "RootCertificate", "UDID", "EscrowBag", "HostCertificate",
                "DeviceCertificate"
            ]
            let hasRP = rpKeys.allSatisfy { plist?[$0] != nil }
            let hasLockdown = lockdownKeys.allSatisfy { plist?[$0] != nil }
            self.compositePairingFile = hasRP && hasLockdown

            if hasLockdown, var classic = plist {
                // Keep a canonical classic copy; rp_pairing_file_from_bytes still
                // receives the untouched merged bytes on the upstream RP path.
                for key in ["private_key", "public_key", "identifier", "alt_irk"] {
                    classic.removeValue(forKey: key)
                }
                self.lockdownPairingFileData = try PropertyListSerialization.data(
                    fromPropertyList: classic,
                    format: .xml,
                    options: 0
                )
            } else {
                self.lockdownPairingFileData = nil
            }

            self.pairingFileData = data
            self.pairingFileType = pairingType
            isRPPairing = (pairingType == .rppairing)
            debugLog("[SS-V21-ADAPT] pairing rp=\\(hasRP) lockdown=\\(hasLockdown) composite=\\(self.compositePairingFile) upstream_mode=\\(pairingType.rawValue)")
'''
    text = replace_once(text, start_old, start_new, "gateway pairing detection")

    lockdown_parse_old = '''            // Parse pairing file content XML plist to self.pairingFile IdevicePairingFile*
            try data.withUnsafeBytes { (buf: UnsafeRawBufferPointer) in
                if let baseAddress = buf.baseAddress?.assumingMemoryBound(to: UInt8.self) {
                    verboseLog("[IdeviceGateway] start() loading lockdown pairing file bytes")
                    let err = idevice_pairing_file_from_bytes(baseAddress, UInt(data.count), &pairingFile)
'''
    lockdown_parse_new = '''            // Parse the canonical classic record when available.
            let classicData = self.lockdownPairingFileData ?? data
            try classicData.withUnsafeBytes { (buf: UnsafeRawBufferPointer) in
                if let baseAddress = buf.baseAddress?.assumingMemoryBound(to: UInt8.self) {
                    verboseLog("[IdeviceGateway] start() loading lockdown pairing file bytes")
                    let err = idevice_pairing_file_from_bytes(baseAddress, UInt(classicData.count), &pairingFile)
'''
    text = replace_once(text, lockdown_parse_old, lockdown_parse_new, "gateway lockdown parse")

    ensure_anchor = '''    private func ensureRPConnection() throws {
'''
    helpers = r'''    // [SS-V21-ADAPT] idevice_tcp_provider_new consumes its pairing handle,
    // so CoreDeviceProxy gets a freshly parsed classic record on every attempt.
    private func makeLockdownProvider() throws -> OpaquePointer {
        guard let deviceEndpointIp = deviceEndpointIp else {
            throw IdeviceGatewayError(.deviceEndpointIpNotAvailable)
        }
        guard let data = lockdownPairingFileData else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "classic Lockdown record unavailable")
        }

        var freshPairingFile: OpaquePointer? = nil
        try data.withUnsafeBytes { (buf: UnsafeRawBufferPointer) in
            guard let base = buf.baseAddress?.assumingMemoryBound(to: UInt8.self) else {
                throw IdeviceGatewayError(.invalidPairingFile, reason: "empty classic Lockdown record")
            }
            if let parseErr = idevice_pairing_file_from_bytes(base, UInt(data.count), &freshPairingFile) {
                let message = getErrorMessage(from: parseErr)
                safeFreeError(parseErr)
                throw IdeviceGatewayError(.invalidPairingFile, reason: "classic record parse failed: \(message)")
            }
        }
        guard let freshPairingFile else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "classic record parse returned nil")
        }

        var provider: OpaquePointer? = nil
        var providerErr: UnsafeMutablePointer<IdeviceFfiError>? = nil
        try MinimuxerConstants.appName.withCString { labelPtr in
            try withSockaddr(ip: deviceEndpointIp, port: MinimuxerConstants.lockdowndPort) { sockaddrPtr, _ in
                debugLog("[SS-V21-ADAPT] core provider START endpoint=\(deviceEndpointIp):\(MinimuxerConstants.lockdowndPort)")
                providerErr = idevice_tcp_provider_new(sockaddrPtr, freshPairingFile, labelPtr, &provider)
            }
        }
        if let providerErr {
            let message = getErrorMessage(from: providerErr)
            safeFreeError(providerErr)
            throw IdeviceGatewayError(.connectionFailed, reason: "TCP provider failed: \(message)")
        }
        guard let provider else {
            throw IdeviceGatewayError(.connectionFailed, reason: "TCP provider returned nil")
        }
        debugLog("[SS-V21-ADAPT] core provider PASS")
        return provider
    }

    private func ensureCoreDeviceConnection() throws {
        if adapter != nil && handshake != nil { return }
        let provider = try makeLockdownProvider()
        defer { idevice_provider_free(provider) }

        debugLog("[SS-V21-ADAPT] tunnel_create_usb START")
        let err = tunnel_create_usb(provider, &adapter, &handshake)
        if let err {
            let message = getErrorMessage(from: err)
            safeFreeError(err)
            invalidateConnection()
            debugLog("[SS-V21-ADAPT] tunnel_create_usb FAIL \(message)")
            throw IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy tunnel failed: \(message)")
        }
        guard adapter != nil, handshake != nil else {
            invalidateConnection()
            throw IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy returned no adapter/RSD handshake")
        }
        activeTunnelTransport = "coredevice-lockdown"
        debugLog("[SS-V21-ADAPT] tunnel_create_usb PASS")
        debugLog("[SS-V21-ADAPT] RSD_HANDSHAKE_PASS transport=coredevice-lockdown")
    }

    private func ensureAdaptiveConnection() throws {
        if adapter != nil && handshake != nil { return }

        if compositePairingFile {
            // Device-specific evidence wins: this iPhone previously reached
            // CoreDeviceProxy StartService. Keep stock RP as a second, clean route.
            debugLog("[SS-V21-ADAPT] composite route=coredevice-first rp-fallback")
            do {
                try ensureCoreDeviceConnection()
                return
            } catch {
                let coreError = error
                debugLog("[SS-V21-ADAPT] coredevice-first FAIL; trying stock tunnel_create_rppairing: \(coreError.localizedDescription)")
                invalidateConnection()
                do {
                    try ensureRPConnection()
                    activeTunnelTransport = "stock-rppairing"
                    debugLog("[SS-V21-ADAPT] stock RPPairing PASS")
                    debugLog("[SS-V21-ADAPT] RSD_HANDSHAKE_PASS transport=stock-rppairing")
                    return
                } catch {
                    throw IdeviceGatewayError(
                        .connectionFailed,
                        reason: "Adaptive transport failed. CoreDeviceProxy: \(coreError.localizedDescription); stock RPPairing: \(error.localizedDescription)"
                    )
                }
            }
        }

        if isRPPairing {
            debugLog("[SS-V21-ADAPT] RP-only route=stock-rppairing")
            try ensureRPConnection()
            activeTunnelTransport = "stock-rppairing"
            debugLog("[SS-V21-ADAPT] stock RPPairing PASS")
        } else {
            debugLog("[SS-V21-ADAPT] Lockdown-only route=coredevice")
            try ensureCoreDeviceConnection()
        }
    }

    // [SS-V21-ADAPT] adaptive transport READY
'''
    text = replace_once(text, ensure_anchor, helpers + ensure_anchor, "gateway adaptive helpers")

    svc_start = text.find("    private func performWithService<T>(")
    svc_end = text.find("    private func performWithUsbmuxdService<T>(", svc_start)
    if svc_start < 0 or svc_end < 0:
        die("performWithService range missing")
    svc = text[svc_start:svc_end]
    count = svc.count("try ensureRPConnection()")
    if count != 2:
        die(f"performWithService expected 2 RP acquisition calls, found {count}")
    svc = svc.replace("try ensureRPConnection()", "try ensureAdaptiveConnection()")
    text = text[:svc_start] + svc + text[svc_end:]

    either_old = '''        debugLog("[IdeviceGateway] performWithEitherService(\\(serviceName)) started, isRPPairing: \\(isRPPairing) (mode = .\\(pairingFileType))")
        if isRPPairing {
            return try performWithService(connect: connectRP, cleanup: cleanup, serviceName: serviceName, action: action)
        } else {
            return try performWithTcpService(connect: connectLockdown, cleanup: cleanup, serviceName: serviceName, action: action)
        }
'''
    either_old = either_old.replace('\\\\(', '\\(')
    either_new = '''        debugLog("[SS-V21-ADAPT] service route=RSD name=\\(serviceName) transport=\\(activeTunnelTransport ?? \"pending\")")
        return try performWithService(connect: connectRP, cleanup: cleanup, serviceName: serviceName, action: action)
'''
    if either_old not in text:
        pattern = re.compile(
            r'(    private func performWithEitherService<T>\([\s\S]*?\) throws -> T \{)'
            r'[\s\S]*?'
            r'(\n    \}\n\n    private func syncFetchUDID)', re.M
        )
        match = pattern.search(text)
        if not match:
            die("performWithEitherService anchor missing")
        text = text[:match.start()] + match.group(1) + "\n" + either_new + "    }\n\n    private func syncFetchUDID" + text[match.end():]
    else:
        text = text.replace(either_old, either_new, 1)

    fetch_pat = re.compile(
        r'    private func syncFetchUDID\(\) throws -> String\? \{[\s\S]*?'
        r'\n    \}\n\n    private func syncGetLockdownValue', re.M
    )
    fetch_new = r'''    private func syncFetchUDID() throws -> String? {
        debugLog("[SS-V21-ADAPT] UDID query START")
        try verifyInitialized()
        let udid: String? = try performWithService(
            connect: lockdownd_connect_rsd,
            cleanup: lockdownd_client_free,
            serviceName: "lockdownd-udid"
        ) { client in
            var plistVal: plist_t? = nil
            let valErr = lockdownd_get_value(client, "UniqueDeviceID", nil, &plistVal)
            if let valErr {
                let message = getErrorMessage(from: valErr)
                safeFreeError(valErr)
                throw IdeviceGatewayError(.connectionFailed, reason: "UniqueDeviceID GetValue failed: \(message)")
            }
            guard let plistVal else { return nil }
            defer { safeFreePlist(plistVal) }
            return getRustPlistString(plistVal)
        }
        guard let udid, !udid.isEmpty else {
            throw IdeviceGatewayError(.connectionFailed, reason: "RSD Lockdown returned no live UniqueDeviceID")
        }
        debugLog("[SS-V21-ADAPT] UDID_PASS transport=\(activeTunnelTransport ?? "unknown")")
        return udid
    }

    private func syncGetLockdownValue'''
    text, n = fetch_pat.subn(fetch_new, text, count=1)
    if n != 1:
        die(f"syncFetchUDID replacement count={n}")

    ddi_old = '''        if isRPPairing {
            try mountPersonalizedDdiRsd(image: image, trustcache: trustcache, manifest: manifest)
            return
        }

        try mountPersonalizedDdiIdevice(image: image, trustcache: trustcache, manifest: manifest)
'''
    ddi_new = '''        debugLog("[SS-V21-ADAPT] personalized DDI route=RSD")
        try mountPersonalizedDdiRsd(image: image, trustcache: trustcache, manifest: manifest)
        return
'''
    if ddi_old in text:
        text = text.replace(ddi_old, ddi_new, 1)
    elif f"{MARKER} personalized DDI route=RSD" not in text:
        die("personalized DDI anchor missing")

    verify_gateway(text)
    path.write_text(text)


def verify_gateway(text: str) -> None:
    required = [
        "private var lockdownPairingFileData: Data?",
        "private var compositePairingFile = false",
        f"{MARKER} pairing rp=",
        "idevice_tcp_provider_new(",
        "tunnel_create_usb(provider, &adapter, &handshake)",
        f"{MARKER} composite route=coredevice-first rp-fallback",
        f"{MARKER} stock RPPairing PASS",
        f"{MARKER} tunnel_create_usb PASS",
        f"{MARKER} service route=RSD",
        f"{MARKER} UDID_PASS",
        f"{MARKER} personalized DDI route=RSD",
        f"{MARKER} adaptive transport READY",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        die(f"gateway verification missing: {missing}")
    if "return try performWithTcpService(connect: connectLockdown" in text:
        die("generic service graph still selects direct dynamic TCP service")
    if "tunnel_create_rppairing(" not in text or "private func ensureRPConnection() throws" not in text:
        die("stock RPPairing fallback missing")


def verify(root: Path) -> None:
    pairing = root / "DeviceGateway" / "PairingProtocol.swift"
    gateway = root / "DeviceGateway" / "idevice" / "IdeviceGateway.swift"
    network = root / "Sources" / "Services" / "NetworkObserverService.swift"
    for path in (pairing, gateway, network):
        require_file(path)

    p = pairing.read_text()
    rp_pos = p.find("if missingRPKeys.isEmpty")
    lockdown_pos = p.find("if missingLockdownKeys.isEmpty")
    if rp_pos < 0 or lockdown_pos < 0 or rp_pos >= lockdown_pos:
        die("PairingProtocol upstream RP-first semantics changed unexpectedly")

    verify_gateway(gateway.read_text())
    if f"{MARKER} retaining explicit LocalVPN peer" not in network.read_text():
        die("NetworkObserver explicit peer policy missing")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v21_adaptive_coredevice.py <minimuxer-root>")
    root = Path(sys.argv[1])
    for path in (
        root / "DeviceGateway" / "PairingProtocol.swift",
        root / "DeviceGateway" / "idevice" / "IdeviceGateway.swift",
        root / "Sources" / "Services" / "NetworkObserverService.swift",
    ):
        require_file(path)
    patch_network_observer(root / "Sources" / "Services" / "NetworkObserverService.swift")
    patch_idevice_gateway(root / "DeviceGateway" / "idevice" / "IdeviceGateway.swift")
    verify(root)
    print("v21 adaptive CoreDevice-first / stock-RP fallback patch verified")


if __name__ == "__main__":
    main()
