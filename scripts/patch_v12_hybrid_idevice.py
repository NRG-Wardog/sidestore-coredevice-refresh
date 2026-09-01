#!/usr/bin/env python3
"""Patch minimuxer's IdeviceGateway for v12 hybrid Lockdown/CoreDeviceProxy transport.

The imported iLoader plist can contain both a traditional Lockdown PairRecord and
RemotePairing credentials. Upstream classifies that file as RemotePairing first,
which hides the already-trusted Lockdown record. v12 validates both capabilities,
uses the existing Lockdown record to start CoreDeviceProxy without pairing again,
and retains the original exact-endpoint RemotePairing tunnel only as a fallback.
"""

from __future__ import annotations

from pathlib import Path
import sys

MARKER = "[SS-V12-HYBRID]"


def fail(message: str) -> "NoReturn":
    raise SystemExit(message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        fail(f"{label}: expected exactly one source anchor, found {count}")
    return text.replace(old, new, 1)


def verify(text: str) -> None:
    required = [
        MARKER,
        "HYBRID_FILE has_rp=",
        "LOCKDOWN_PAIRING_PARSE_SUCCESS",
        "LOCKDOWN_PROVIDER_CREATE_SUCCESS",
        "LOCKDOWN_SESSION_AND_COREDEVICE_START",
        "COREDEVICE_TUNNEL_SUCCESS",
        "RP_FALLBACK_START",
        "RP_FALLBACK_SUCCESS",
        "STRICT_UDID_QUERY_SUCCESS",
        "RSD_LOCKDOWN_CONNECT_FAILED",
        "idevice_pairing_file_from_bytes",
        "idevice_tcp_provider_new",
        "tunnel_create_usb",
        "tunnel_create_rppairing",
        "hasRemotePairingCapability",
        "hasLockdownCapability",
        "let useRsdTransport = hasRemotePairingCapability || hasLockdownCapability",
        "Transport resets and tunnel handshakes are not evidence",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        fail(f"v12 hybrid verification failed; missing: {missing}")

    forbidden = [
        "lockdownd_pair(",
        "PAIR_REQUEST_START",
        "SS-V10-LOCKDOWN-BOOTSTRAP",
        "SS-V9-COREDEVICE",
        "SS-ADAPT",
        "DYNAMIC_CANDIDATES",
        "source-bound",
        "NAT46",
        "getRustPlistString returned UDID:",
    ]
    leaked = [item for item in forbidden if item in text]
    if leaked:
        fail(f"v12 hybrid verification failed; forbidden legacy/sensitive code remains: {leaked}")

    if text.count("tunnel_create_rppairing(") != 1:
        fail("v12 hybrid verification failed; expected one exact-endpoint RP fallback call")
    if text.count("tunnel_create_usb(") != 1:
        fail("v12 hybrid verification failed; expected one CoreDeviceProxy tunnel call")


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: patch_v12_hybrid_idevice.py <DeviceGateway/idevice/IdeviceGateway.swift>")

    path = Path(sys.argv[1])
    source = path.read_text()
    if MARKER in source:
        verify(source)
        print("v12 hybrid IdeviceGateway patch already present and verified")
        return

    old_state = '''    private var pairingFile: OpaquePointer? = nil
    private var adapter: OpaquePointer? = nil
    private var handshake: OpaquePointer? = nil
    private var deviceEndpointIp: String? = nil
    private var remotePairingPort: UInt16 = MinimuxerConstants.remotePairingPort
    private var isInitialized = false
'''
    new_state = '''    // RP handle only. The Lockdown PairRecord is reparsed from pairingFileData
    // for each CoreDeviceProxy attempt and consumed by a short-lived TcpProvider.
    private var pairingFile: OpaquePointer? = nil
    private var hasRemotePairingCapability = false
    private var hasLockdownCapability = false
    private var adapter: OpaquePointer? = nil
    private var handshake: OpaquePointer? = nil
    private var deviceEndpointIp: String? = nil
    private var remotePairingPort: UInt16 = MinimuxerConstants.remotePairingPort
    private var isInitialized = false
'''
    source = replace_once(source, old_state, new_state, "state variables")

    old_cleanup = '''        if let pairingFile = self.pairingFile {
            verboseLog("[IdeviceGateway] cleanup() freeing pairingFile")
            if isRPPairing {
                rp_pairing_file_free(pairingFile)
            } else {
                idevice_pairing_file_free(pairingFile)
            }
            self.pairingFile = nil
        }

        isRPPairing = false
        pairingFileType = .unknown
'''
    new_cleanup = '''        if let pairingFile = self.pairingFile {
            verboseLog("[IdeviceGateway] cleanup() freeing RemotePairing file")
            rp_pairing_file_free(pairingFile)
            self.pairingFile = nil
        }

        hasRemotePairingCapability = false
        hasLockdownCapability = false
        isRPPairing = false
        pairingFileType = .unknown
'''
    source = replace_once(source, old_cleanup, new_cleanup, "cleanup ownership")

    start_anchor = '''        let plist = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any]
'''
    start_index = source.find(start_anchor)
    if start_index < 0:
        fail("syncStart: could not locate plist parsing anchor")
    end_anchor = '''        isInitialized = true
'''
    end_index = source.find(end_anchor, start_index)
    if end_index < 0:
        fail("syncStart: could not locate initialization terminator")
    end_index += len(end_anchor)

    new_start = '''        guard let plist = try? PropertyListSerialization.propertyList(
            from: data,
            options: [],
            format: nil
        ) as? [String: Any] else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Pairing data is not a plist dictionary")
        }

        let remotePairingKeys = ["private_key", "public_key", "identifier"]
        let lockdownKeys = [
            "DeviceCertificate", "HostCertificate", "HostPrivateKey",
            "RootCertificate", "RootPrivateKey", "HostID", "SystemBUID",
            "WiFiMACAddress", "UDID", "EscrowBag"
        ]
        let declaresRemotePairing = remotePairingKeys.allSatisfy { plist[$0] != nil }
        let declaresLockdown = lockdownKeys.allSatisfy { plist[$0] != nil }
        guard declaresRemotePairing || declaresLockdown else {
            _ = try Self.validatePairingFile(from: plist)
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Pairing file exposes no supported capability")
        }

        self.pairingFileData = data
        self.pairingFileType = declaresRemotePairing ? .rppairing : .lockdown
        self.hasRemotePairingCapability = false
        self.hasLockdownCapability = false

        if declaresRemotePairing {
            var parsedRP: OpaquePointer? = nil
            var parseError: UnsafeMutablePointer<IdeviceFfiError>? = nil
            data.withUnsafeBytes { (buf: UnsafeRawBufferPointer) in
                guard let base = buf.baseAddress?.assumingMemoryBound(to: UInt8.self) else { return }
                parseError = rp_pairing_file_from_bytes(base, UInt(data.count), &parsedRP)
            }
            if let parseError {
                let message = getErrorMessage(from: parseError)
                idevice_error_free(parseError)
                debugLog("[SS-V12-HYBRID] RP_PAIRING_PARSE_FAILED message=\\(message)")
            } else if let parsedRP {
                self.pairingFile = parsedRP
                self.hasRemotePairingCapability = true
                debugLog("[SS-V12-HYBRID] RP_PAIRING_PARSE_SUCCESS")
            }
        }

        if declaresLockdown {
            do {
                let parsedLockdown = try makeLockdownPairingFile(from: data)
                idevice_pairing_file_free(parsedLockdown)
                self.hasLockdownCapability = true
                debugLog("[SS-V12-HYBRID] LOCKDOWN_PAIRING_PARSE_SUCCESS")
            } catch {
                debugLog("[SS-V12-HYBRID] LOCKDOWN_PAIRING_PARSE_FAILED message=\\(error.localizedDescription)")
            }
        }

        guard hasRemotePairingCapability || hasLockdownCapability else {
            throw IdeviceGatewayError(
                .invalidPairingFile,
                reason: "Neither RemotePairing nor Lockdown credentials could be parsed"
            )
        }

        // Existing service helpers use this flag to select RSD-backed services.
        // Both v12 transports produce adapter + RSD handshake, so both use that path.
        let useRsdTransport = hasRemotePairingCapability || hasLockdownCapability
        isRPPairing = useRsdTransport
        debugLog(
            "[SS-V12-HYBRID] HYBRID_FILE has_rp=\\(hasRemotePairingCapability) " +
            "has_lockdown=\\(hasLockdownCapability) service_route=rsd"
        )
        isInitialized = true
'''
    source = source[:start_index] + new_start + source[end_index:]

    ensure_start = source.find("    private func ensureRPConnection() throws {")
    ensure_end = source.find("    private func isPairingError", ensure_start)
    if ensure_start < 0 or ensure_end < 0:
        fail("connection patch: could not locate ensureRPConnection function boundaries")

    new_connection_code = r'''    private func makeLockdownPairingFile(from data: Data? = nil) throws -> OpaquePointer {
        guard let data = data ?? pairingFileData, !data.isEmpty else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Lockdown pairing data is unavailable")
        }

        var parsed: OpaquePointer? = nil
        var parseError: UnsafeMutablePointer<IdeviceFfiError>? = nil
        data.withUnsafeBytes { (buf: UnsafeRawBufferPointer) in
            guard let base = buf.baseAddress?.assumingMemoryBound(to: UInt8.self) else { return }
            parseError = idevice_pairing_file_from_bytes(base, UInt(data.count), &parsed)
        }
        if let parseError {
            let message = getErrorMessage(from: parseError)
            idevice_error_free(parseError)
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Lockdown PairRecord parse failed: \(message)")
        }
        guard let parsed else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Lockdown PairRecord parser returned nil")
        }
        return parsed
    }

    private func ensureCoreDeviceProxyConnection(deviceEndpointIp: String) throws {
        guard hasLockdownCapability else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "No Lockdown capability is available")
        }

        let lockdownPairing = try makeLockdownPairingFile()
        var unconsumedPairing: OpaquePointer? = lockdownPairing
        defer {
            if let unconsumedPairing {
                idevice_pairing_file_free(unconsumedPairing)
            }
        }

        var provider: OpaquePointer? = nil
        var providerError: UnsafeMutablePointer<IdeviceFfiError>? = nil
        let label = "SideStore-v12-CoreDeviceProxy"
        debugLog("[SS-V12-HYBRID] LOCKDOWN_PROVIDER_CREATE_START endpoint=\(deviceEndpointIp)")
        try label.withCString { labelPtr in
            try withSockaddr(ip: deviceEndpointIp, port: MinimuxerConstants.lockdowndPort) { sockaddrPtr, _ in
                providerError = idevice_tcp_provider_new(
                    sockaddrPtr,
                    lockdownPairing,
                    labelPtr,
                    &provider
                )
            }
        }

        if let providerError {
            let message = getErrorMessage(from: providerError)
            idevice_error_free(providerError)
            throw IdeviceGatewayError(.connectionFailed, reason: "Lockdown TCP provider creation failed: \(message)")
        }
        // idevice_tcp_provider_new consumes the PairingFile whenever it returns success,
        // even if a future ABI regression were to return a nil provider.
        unconsumedPairing = nil
        guard let provider else {
            throw IdeviceGatewayError(.connectionFailed, reason: "Lockdown TCP provider creation returned nil")
        }
        defer { idevice_provider_free(provider) }
        debugLog("[SS-V12-HYBRID] LOCKDOWN_PROVIDER_CREATE_SUCCESS")

        var candidateAdapter: OpaquePointer? = nil
        var candidateHandshake: OpaquePointer? = nil
        debugLog(
            "[SS-V12-HYBRID] LOCKDOWN_SESSION_AND_COREDEVICE_START " +
            "endpoint=\(deviceEndpointIp) lockdown_port=\(MinimuxerConstants.lockdowndPort) " +
            "service=com.apple.internal.devicecompute.CoreDeviceProxy"
        )
        let tunnelError = tunnel_create_usb(provider, &candidateAdapter, &candidateHandshake)
        if let tunnelError {
            let code = tunnelError.pointee.code
            let subCode = tunnelError.pointee.sub_code
            let message = getErrorMessage(from: tunnelError)
            idevice_error_free(tunnelError)
            if let candidateHandshake { rsd_handshake_free(candidateHandshake) }
            if let candidateAdapter { adapter_free(candidateAdapter) }
            debugLog(
                "[SS-V12-HYBRID] COREDEVICE_TUNNEL_FAILED code=\(code) " +
                "subCode=\(subCode) message=\(message)"
            )
            throw IdeviceGatewayError(.connectionFailed, reason: "Existing Lockdown session/CoreDeviceProxy failed: \(message)")
        }

        guard let candidateAdapter, let candidateHandshake else {
            if let candidateHandshake { rsd_handshake_free(candidateHandshake) }
            if let candidateAdapter { adapter_free(candidateAdapter) }
            throw IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy returned an incomplete RSD tunnel")
        }

        adapter = candidateAdapter
        handshake = candidateHandshake
        debugLog("[SS-V12-HYBRID] COREDEVICE_TUNNEL_SUCCESS rsd_handshake_ready=true")
    }

    private func ensureExactRemotePairingFallback(deviceEndpointIp: String) throws {
        guard hasRemotePairingCapability, let pairingFile else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "No RemotePairing capability is available")
        }

        let hostname = MinimuxerConstants.appName
        var candidateAdapter: OpaquePointer? = nil
        var candidateHandshake: OpaquePointer? = nil
        var tunnelError: UnsafeMutablePointer<IdeviceFfiError>? = nil
        debugLog(
            "[SS-V12-HYBRID] RP_FALLBACK_START endpoint=\(deviceEndpointIp) " +
            "control_port=\(remotePairingPort) dynamic_route=same_endpoint_first_and_only"
        )
        try hostname.withCString { hostPtr in
            try withSockaddr(ip: deviceEndpointIp, port: remotePairingPort) { sockaddrPtr, sockaddrLen in
                tunnelError = tunnel_create_rppairing(
                    sockaddrPtr,
                    sockaddrLen,
                    hostPtr,
                    pairingFile,
                    nil,
                    nil,
                    &candidateAdapter,
                    &candidateHandshake
                )
            }
        }

        if let tunnelError {
            let code = tunnelError.pointee.code
            let subCode = tunnelError.pointee.sub_code
            let message = getErrorMessage(from: tunnelError)
            idevice_error_free(tunnelError)
            if let candidateHandshake { rsd_handshake_free(candidateHandshake) }
            if let candidateAdapter { adapter_free(candidateAdapter) }
            debugLog(
                "[SS-V12-HYBRID] RP_FALLBACK_FAILED code=\(code) " +
                "subCode=\(subCode) message=\(message)"
            )
            throw IdeviceGatewayError(.connectionFailed, reason: "Exact-endpoint RemotePairing tunnel failed: \(message)")
        }

        guard let candidateAdapter, let candidateHandshake else {
            if let candidateHandshake { rsd_handshake_free(candidateHandshake) }
            if let candidateAdapter { adapter_free(candidateAdapter) }
            throw IdeviceGatewayError(.connectionFailed, reason: "RemotePairing returned an incomplete RSD tunnel")
        }

        adapter = candidateAdapter
        handshake = candidateHandshake
        debugLog("[SS-V12-HYBRID] RP_FALLBACK_SUCCESS rsd_handshake_ready=true")
    }

    private func ensureRPConnection() throws {
        debugLog(
            "[SS-V12-HYBRID] CONNECTION_START adapter_ready=\(adapter != nil) " +
            "handshake_ready=\(handshake != nil)"
        )
        if adapter != nil && handshake != nil {
            debugLog("[SS-V12-HYBRID] CONNECTION_REUSE")
            return
        }

        invalidateConnection()
        guard let deviceEndpointIp else {
            throw IdeviceGatewayError(.deviceEndpointIpNotAvailable)
        }

        var failures: [String] = []
        if hasLockdownCapability {
            do {
                debugLog("[SS-V12-HYBRID] TRANSPORT_SELECT path=existing-lockdown-coredevice-proxy")
                try ensureCoreDeviceProxyConnection(deviceEndpointIp: deviceEndpointIp)
                return
            } catch {
                invalidateConnection()
                failures.append("CoreDeviceProxy: \(error.localizedDescription)")
                debugLog("[SS-V12-HYBRID] COREDEVICE_PATH_FAILED reason=\(error.localizedDescription)")
            }
        }

        if hasRemotePairingCapability {
            do {
                debugLog("[SS-V12-HYBRID] TRANSPORT_SELECT path=exact-endpoint-remote-pairing-fallback")
                try ensureExactRemotePairingFallback(deviceEndpointIp: deviceEndpointIp)
                return
            } catch {
                invalidateConnection()
                failures.append("RemotePairing: \(error.localizedDescription)")
                debugLog("[SS-V12-HYBRID] RP_PATH_FAILED reason=\(error.localizedDescription)")
            }
        }

        let reason = failures.isEmpty
            ? "Pairing file has no usable transport capability"
            : failures.joined(separator: " | ")
        let error = IdeviceGatewayError(.connectionFailed, reason: reason)
        lastError = error
        throw error
    }

'''
    source = source[:ensure_start] + new_connection_code + source[ensure_end:]

    old_pairing_classifier = r'''        if let msgPtr = err.pointee.message {
            let msg = String(cString: msgPtr).lowercased()
            if msg.contains("invalidconf") || msg.contains("pairing") || msg.contains("handshake") ||
                msg.contains("connection reset") || msg.contains("connectionreset") {
                return true
            }
        }
'''
    new_pairing_classifier = r'''        if let msgPtr = err.pointee.message {
            let msg = String(cString: msgPtr).lowercased()
            // Transport resets and tunnel handshakes are not evidence that the
            // already-parsed pairing material is invalid. Keep this classifier
            // limited to explicit credential/consent failures.
            if msg.contains("invalidconf") || msg.contains("invalidhostid") ||
                msg.contains("pairing rejected") || msg.contains("user denied pairing") {
                return true
            }
        }
'''
    source = replace_once(
        source,
        old_pairing_classifier,
        new_pairing_classifier,
        "pairing error classifier",
    )

    old_fetch_start = '''            var lockdownClient: OpaquePointer? = nil
            verboseLog("[IdeviceGateway] fetchUDID() connecting lockdownd_connect_rsd")
'''
    new_fetch_start = '''            var lockdownClient: OpaquePointer? = nil
            debugLog("[SS-V12-HYBRID] STRICT_UDID_QUERY_START")
            verboseLog("[IdeviceGateway] fetchUDID() connecting lockdownd_connect_rsd")
'''
    source = replace_once(source, old_fetch_start, new_fetch_start, "strict UDID start")

    old_ensure_failure = r'''                debugLog("[IdeviceGateway] fetchUDID() ensureRPConnection failed with error: \(error)")
                return nil
'''
    new_ensure_failure = r'''                debugLog("[SS-V12-HYBRID] STRICT_UDID_QUERY_FAILED stage=transport reason=\(error.localizedDescription)")
                return nil
'''
    source = replace_once(source, old_ensure_failure, new_ensure_failure, "UDID transport failure diagnostics")

    old_first_rsd_failure = r'''            if let firstErr = connectErr {
                debugLog("[IdeviceGateway] fetchUDID() lockdownd_connect_rsd failed on existing connection, invalidating and retrying with fresh connection")
                idevice_error_free(firstErr)
                invalidateConnection()
'''
    new_first_rsd_failure = r'''            if let firstErr = connectErr {
                let code = firstErr.pointee.code
                let subCode = firstErr.pointee.sub_code
                let message = getErrorMessage(from: firstErr)
                debugLog("[SS-V12-HYBRID] RSD_LOCKDOWN_CONNECT_FAILED attempt=1 code=\(code) subCode=\(subCode) message=\(message)")
                idevice_error_free(firstErr)
                invalidateConnection()
'''
    source = replace_once(source, old_first_rsd_failure, new_first_rsd_failure, "first RSD lockdown failure diagnostics")

    old_second_rsd_failure = r'''                    if let secondErr = connectErr {
                        debugLog("[IdeviceGateway] fetchUDID() lockdownd_connect_rsd retry failed")
                        idevice_error_free(secondErr)
                        invalidateConnection()
                        return nil
                    }
'''
    new_second_rsd_failure = r'''                    if let secondErr = connectErr {
                        let code = secondErr.pointee.code
                        let subCode = secondErr.pointee.sub_code
                        let message = getErrorMessage(from: secondErr)
                        debugLog("[SS-V12-HYBRID] RSD_LOCKDOWN_CONNECT_FAILED attempt=2 code=\(code) subCode=\(subCode) message=\(message)")
                        idevice_error_free(secondErr)
                        invalidateConnection()
                        return nil
                    }
'''
    source = replace_once(source, old_second_rsd_failure, new_second_rsd_failure, "second RSD lockdown failure diagnostics")

    old_value_failure = r'''            if let valErr = valErr {
                debugLog("[IdeviceGateway] fetchUDID() lockdownd_get_value failed")
                safeFreeError(valErr)
                return nil
            }
'''
    new_value_failure = r'''            if let valErr = valErr {
                let code = valErr.pointee.code
                let subCode = valErr.pointee.sub_code
                let message = getErrorMessage(from: valErr)
                debugLog("[SS-V12-HYBRID] STRICT_UDID_QUERY_FAILED stage=get_value code=\(code) subCode=\(subCode) message=\(message)")
                safeFreeError(valErr)
                return nil
            }
'''
    source = replace_once(source, old_value_failure, new_value_failure, "UDID value failure diagnostics")

    old_udid_return = r'''                let udid = getRustPlistString(plistVal)
                verboseLog("[IdeviceGateway] fetchUDID() getRustPlistString returned UDID: \(String(describing: udid))")
                return udid
'''
    new_udid_return = r'''                let udid = getRustPlistString(plistVal)
                guard let udid, !udid.isEmpty else {
                    debugLog("[SS-V12-HYBRID] STRICT_UDID_QUERY_FAILED reason=empty_value")
                    return nil
                }
                debugLog("[SS-V12-HYBRID] STRICT_UDID_QUERY_SUCCESS present=true length=\(udid.count)")
                return udid
'''
    source = replace_once(source, old_udid_return, new_udid_return, "strict UDID result")

    path.write_text(source)
    verify(path.read_text())
    print("v12 hybrid existing-Lockdown/CoreDeviceProxy + exact RP fallback patch applied and verified")


if __name__ == "__main__":
    main()
