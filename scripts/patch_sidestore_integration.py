#!/usr/bin/env python3
"""Wire SideStore's Lockdown pairing mode to the proven CoreDevice RSD path."""

from __future__ import annotations

from pathlib import Path
import sys


MARKER = "[SIDESTORE_COREDEVICE] TRANSPORT_CREATE_START"
REFRESH_MARKER = "[SELF_REFRESH]"


def die(message: str) -> None:
    raise SystemExit(message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_region(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        die(f"{label}: start anchor not found")
    end_index = text.find(end, start_index)
    if end_index < 0:
        die(f"{label}: end anchor not found")
    return text[:start_index] + replacement + text[end_index:]


def patch_coredevice_route_selection(minimuxer: Path) -> None:
    pairing_path = minimuxer / "Common" / "PairingProtocol.swift"
    pairing_text = pairing_path.read_text(encoding="utf-8")
    pairing_marker = "Composite records must use Lockdown/CoreDevice"
    if pairing_marker not in pairing_text:
        old_validation = '''        let requiredRPKeys = [
            "private_key",
            "public_key",
            "identifier"
        ]
        let missingRPKeys = requiredRPKeys.filter { plist[$0] == nil }
        if missingRPKeys.isEmpty {
            return .rppairing
        }

        let requiredLockdownKeys = [
            "WiFiMACAddress",
            "SystemBUID",
            "RootPrivateKey",
            "HostPrivateKey",
            "HostID",
            "RootCertificate",
            "UDID",
            "EscrowBag",
            "HostCertificate",
            "DeviceCertificate"
        ]
        let missingLockdownKeys = requiredLockdownKeys.filter { plist[$0] == nil }
        if missingLockdownKeys.isEmpty {
            return .lockdown
        }
'''
        new_validation = '''        let requiredLockdownKeys = [
            "WiFiMACAddress",
            "SystemBUID",
            "RootPrivateKey",
            "HostPrivateKey",
            "HostID",
            "RootCertificate",
            "UDID",
            "EscrowBag",
            "HostCertificate",
            "DeviceCertificate"
        ]
        let missingLockdownKeys = requiredLockdownKeys.filter { plist[$0] == nil }
        // Composite records must use Lockdown/CoreDevice; RemotePairing TCP is not
        // viable for same-device operation on current iOS releases.
        if missingLockdownKeys.isEmpty {
            return .lockdown
        }

        let requiredRPKeys = [
            "private_key",
            "public_key",
            "identifier"
        ]
        let missingRPKeys = requiredRPKeys.filter { plist[$0] == nil }
        if missingRPKeys.isEmpty {
            return .rppairing
        }
'''
        pairing_text = replace_once(
            pairing_text,
            old_validation,
            new_validation,
            "prefer Lockdown for composite pairing records",
        )
        pairing_path.write_text(pairing_text, encoding="utf-8")
    if pairing_marker not in pairing_text:
        die("composite pairing mode preference missing")

    observer_path = minimuxer / "Sources" / "Services" / "NetworkObserverService.swift"
    observer_text = observer_path.read_text(encoding="utf-8")
    endpoint_marker = "[SIDESTORE_COREDEVICE] ENDPOINT_SELECT"
    if endpoint_marker not in observer_text:
        old_selection = '''                    let overrideIp = await manager.overridePeerIp
                    let isOverridden = !(overrideIp ?? "").isEmpty

                    let effectiveIp = await isOverridden
                            ? (manager.isOverridePeerIpReachable ? overrideIp : nil)            // when override active, we don't question user intent
                            : (manager.isDerivedPeerIpReachable ? manager.derivedPeerIp : nil)  // only if not overriden, we try to use auto discovered
                    let effectivePeer = isOverridden ? "overridePeer" : "derivedPeerIp"
'''
        new_selection = '''                    let overrideIp = await manager.overridePeerIp
                    let overrideReachable = await manager.isOverridePeerIpReachable
                    let derivedIp = await manager.derivedPeerIp
                    let derivedReachable = await manager.isDerivedPeerIpReachable

                    let effectiveIp: String?
                    let effectivePeer: String
                    if let overrideIp, overrideReachable {
                        effectiveIp = overrideIp
                        effectivePeer = "overridePeer"
                    } else if let derivedIp, derivedReachable {
                        effectiveIp = derivedIp
                        effectivePeer = "derivedPeerIp"
                    } else {
                        effectiveIp = nil
                        effectivePeer = "none"
                    }
                    debugLog(
                        "[SIDESTORE_COREDEVICE] ENDPOINT_SELECT " +
                        "override_present=\\(overrideIp != nil) override_reachable=\\(overrideReachable) " +
                        "derived_present=\\(derivedIp != nil) derived_reachable=\\(derivedReachable) " +
                        "selected_source=\\(effectivePeer)"
                    )
'''
        observer_text = replace_once(
            observer_text,
            old_selection,
            new_selection,
            "CoreDevice endpoint fallback",
        )
        observer_path.write_text(observer_text, encoding="utf-8")
    if endpoint_marker not in observer_text:
        die("CoreDevice endpoint selection logging missing")

    implementation_path = minimuxer / "Sources" / "MinimuxerImpl.swift"
    implementation_text = implementation_path.read_text(encoding="utf-8")
    utun_marker = "[SIDESTORE_COREDEVICE] LOCALVPN_UTUN_ACCEPTED"
    if utun_marker not in implementation_text:
        old_ipsec_requirement = '''                // check iKEv2 too if in lockdown mode and ios >= 26.4
                if !isrppairing && !net.isIKEv2IPSecAvailable {
                    if #available(iOS 26.4, *) {
                        debugLog("[minimuxer] minimuxer not ready: no ipsec interface (required for lockdown on iOS 26.4+)")
                        return .failure(.invalidVPN("utun is present but no ipsec/IKEv2 interface found — LocalDevVPN may not support the lockdown protocol on iOS 26.4+"))
                    }
                }
'''
        new_utun_policy = '''                if !isrppairing {
                    verboseLog("[SIDESTORE_COREDEVICE] LOCALVPN_UTUN_ACCEPTED transport=lockdown-coredevice")
                }
'''
        implementation_text = replace_once(
            implementation_text,
            old_ipsec_requirement,
            new_utun_policy,
            "remove obsolete IKEv2 requirement from CoreDevice path",
        )
        implementation_path.write_text(implementation_text, encoding="utf-8")
    if utun_marker not in implementation_text:
        die("CoreDevice LocalDevVPN utun policy missing")

    gateway_path = minimuxer / "DeviceGateway" / "idevice" / "IdeviceGateway.swift"
    gateway_text = gateway_path.read_text(encoding="utf-8")
    pairing_mode_marker = "[SIDESTORE_COREDEVICE] PAIRING_MODE_SELECTED"
    if pairing_mode_marker not in gateway_text:
        gateway_text = replace_once(
            gateway_text,
            '''            self.pairingFileData = parsedPairingFile.rawData
            self.pairingFileType = parsedPairingFile.mode
            self.isRPPairing = (parsedPairingFile.mode == .rppairing)
''',
            '''            self.pairingFileData = parsedPairingFile.rawData
            self.pairingFileType = parsedPairingFile.mode
            self.isRPPairing = (parsedPairingFile.mode == .rppairing)
            debugLog("[SIDESTORE_COREDEVICE] PAIRING_MODE_SELECTED mode=\\(parsedPairingFile.mode)")
''',
            "pairing mode selection logging",
        )
        gateway_path.write_text(gateway_text, encoding="utf-8")
    if pairing_mode_marker not in gateway_text:
        die("CoreDevice pairing mode selection logging missing")


def patch_gateway(minimuxer: Path) -> None:
    path = minimuxer / "DeviceGateway" / "idevice" / "IdeviceGateway.swift"
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        verify_gateway(text)
        return

    text = replace_once(
        text,
        """import DeviceGatewayAPI
internal import MinimuxerCommon
""",
        """import DeviceGatewayAPI
internal import MinimuxerCommon

func sideStoreTransportLog(_ message: UnsafePointer<CChar>?) {
    guard let message else { return }
    debugLog(String(cString: message))
}
""",
        "Rust transport logger",
    )

    text = replace_once(
        text,
        r"""    public func setLogging(_ enabled: Bool) {
        DeviceGatewayLogging.setLogging(enabled)
        debugLog("[IdeviceGateway] setLogging(\(enabled)) called")
""",
        r"""    public func setLogging(_ enabled: Bool) {
        idevice_set_transport_log_callback(sideStoreTransportLog)
        DeviceGatewayLogging.setLogging(enabled)
        debugLog("[IdeviceGateway] setLogging(\(enabled)) called")
""",
        "register Rust transport logger",
    )

    text = replace_once(
        text,
        """    private var pairingFile: OpaquePointer? = nil
    private var adapter: OpaquePointer? = nil
    private var handshake: OpaquePointer? = nil
""",
        """    private var pairingFile: OpaquePointer? = nil
    private var coreDeviceProvider: OpaquePointer? = nil
    private var adapter: OpaquePointer? = nil
    private var handshake: OpaquePointer? = nil
    private let ffiQueue = DispatchQueue(label: "com.sidestore.idevice-gateway")
""",
        "CoreDevice provider state",
    )

    text = replace_once(
        text,
        r"""    public func setRemotePairingPort(_ port: UInt16) {
        debugLog("[IdeviceGateway] setRemotePairingPort(\(port)) called")
        guard self.remotePairingPort != port else { return }
        self.remotePairingPort = port
        invalidateConnection()
    }
""",
        r"""    public func setRemotePairingPort(_ port: UInt16) {
        ffiQueue.sync {
            debugLog("[IdeviceGateway] setRemotePairingPort(\(port)) called")
            guard self.remotePairingPort != port else { return }
            self.remotePairingPort = port
            invalidateConnection()
        }
    }
""",
        "serialize remote pairing endpoint updates",
    )

    old_cleanup = """    private func cleanup() {
        debugLog("[IdeviceGateway] cleanup() called")
        isInitialized = false
        self.pairingFileData = nil

        if let pairingFile = self.pairingFile {
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
        lastError = nil
        if let handshake = handshake {
            verboseLog("[IdeviceGateway] cleanup() freeing handshake")
            rsd_handshake_free(handshake)
            self.handshake = nil
        }
        if let adapter = adapter {
            verboseLog("[IdeviceGateway] cleanup() freeing adapter")
            adapter_free(adapter)
            self.adapter = nil
        }
    }
""".replace(
        "self.pairingFileData = nil\n\n        if",
        "self.pairingFileData = nil\n        \n        if",
    )
    new_cleanup = """    private func cleanup() {
        debugLog("[IdeviceGateway] cleanup() called")
        isInitialized = false
        self.pairingFileData = nil

        releaseTransport()
        if let pairingFile = self.pairingFile {
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
        lastError = nil
    }

    private func releaseTransport() {
        if let handshake = handshake {
            rsd_handshake_free(handshake)
            self.handshake = nil
        }
        if let adapter = adapter {
            adapter_free(adapter)
            self.adapter = nil
        }
        if let provider = coreDeviceProvider {
            idevice_provider_free(provider)
            self.coreDeviceProvider = nil
        }
    }
"""
    text = replace_once(text, old_cleanup, new_cleanup, "transport cleanup order")

    text = replace_once(
        text,
        """    private func invalidateConnection() {
        debugLog("[IdeviceGateway] invalidateConnection() called - clearing stale adapter and handshake")
        if let handshake = handshake {
            rsd_handshake_free(handshake)
            self.handshake = nil
        }
        if let adapter = adapter {
            adapter_free(adapter)
            self.adapter = nil
        }
    }
""",
        """    private func invalidateConnection() {
        debugLog("[IdeviceGateway] invalidateConnection() called - clearing transport handles")
        releaseTransport()
    }
""",
        "connection invalidation",
    )

    text = replace_once(
        text,
        r"""    public func setDeviceEndpointIp(_ ip: String?) {
        debugLog("[IdeviceGateway] setDeviceEndpointIp(\(ip ?? "nil")) called")
        guard self.deviceEndpointIp != ip else {
            debugLog("[IdeviceGateway] setDeviceEndpointIp: IP is already \(ip ?? "nil"), skipping invalidation")
            return
        }
        self.deviceEndpointIp = ip

        // Invalidate current cached connections
        if handshake != nil {
            debugLog("[IdeviceGateway] setDeviceEndpointIp invalidating handshake")
            rsd_handshake_free(handshake)
            self.handshake = nil
        }
        if adapter != nil {
            debugLog("[IdeviceGateway] setDeviceEndpointIp invalidating adapter")
            adapter_free(adapter)
            self.adapter = nil
        }
    }
""".replace(
            "self.deviceEndpointIp = ip\n\n        //",
            "self.deviceEndpointIp = ip\n        \n        //",
        ),
        r"""    public func setDeviceEndpointIp(_ ip: String?) {
        ffiQueue.sync {
            debugLog("[IdeviceGateway] setDeviceEndpointIp(\(ip ?? "nil")) called")
            guard self.deviceEndpointIp != ip else {
                debugLog("[IdeviceGateway] setDeviceEndpointIp: IP is already \(ip ?? "nil"), skipping invalidation")
                return
            }
            self.deviceEndpointIp = ip
            invalidateConnection()
        }
    }
""",
        "serialize endpoint invalidation",
    )

    ensure_coredevice = r'''    private func ensureCoreDeviceConnection() throws {
        if adapter != nil, handshake != nil, coreDeviceProvider != nil,
           tunnel_heartbeat_is_active() {
            verboseLog("[SIDESTORE_COREDEVICE] TRANSPORT_REUSE")
            return
        }

        if adapter != nil || handshake != nil || coreDeviceProvider != nil {
            debugLog("[SIDESTORE_COREDEVICE] TRANSPORT_STALE heartbeat_active=\(tunnel_heartbeat_is_active())")
            releaseTransport()
        }

        guard pairingFileType == .lockdown else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "CoreDevice transport requires a Lockdown pairing file")
        }
        guard let endpoint = deviceEndpointIp else {
            throw IdeviceGatewayError(.deviceEndpointIpNotAvailable)
        }
        guard let data = pairingFileData else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Lockdown pairing data is unavailable")
        }

        debugLog("[SIDESTORE_COREDEVICE] TRANSPORT_CREATE_START endpoint=\(endpoint) port=62078")
        var providerPairing: OpaquePointer? = nil
        let parseError = data.withUnsafeBytes { bytes in
            idevice_pairing_file_from_bytes(
                bytes.baseAddress?.assumingMemoryBound(to: UInt8.self),
                UInt(data.count),
                &providerPairing
            )
        }
        if let parseError {
            let message = getErrorMessage(from: parseError)
            idevice_error_free(parseError)
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Lockdown pairing parse failed: \(message)")
        }
        guard let providerPairing else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Lockdown pairing parse returned nil")
        }

        var provider: OpaquePointer? = nil
        var providerError: UnsafeMutablePointer<IdeviceFfiError>? = nil
        try MinimuxerConstants.appName.withCString { label in
            try withSockaddr(ip: endpoint, port: MinimuxerConstants.lockdowndPort) { address, _ in
                providerError = idevice_tcp_provider_new(address, providerPairing, label, &provider)
            }
        }
        // idevice_tcp_provider_new consumes providerPairing for valid address/label inputs.
        if let providerError {
            let message = getErrorMessage(from: providerError)
            idevice_error_free(providerError)
            throw IdeviceGatewayError(.connectionFailed, reason: "CoreDevice provider creation failed: \(message)")
        }
        guard let provider else {
            throw IdeviceGatewayError(.connectionFailed, reason: "CoreDevice provider creation returned nil")
        }
        coreDeviceProvider = provider
        debugLog("[SIDESTORE_COREDEVICE] PROVIDER_CREATE_PASS")

        let tunnelError = tunnel_create_usb(provider, &adapter, &handshake)
        if let tunnelError {
            let message = getErrorMessage(from: tunnelError)
            let code = tunnelError.pointee.code
            let subCode = tunnelError.pointee.sub_code
            idevice_error_free(tunnelError)
            releaseTransport()
            debugLog("[SIDESTORE_COREDEVICE] TRANSPORT_CREATE_FAIL code=\(code) subcode=\(subCode) error=\(message)")
            throw IdeviceGatewayError(.connectionFailed, reason: "CoreDevice tunnel failed: \(message)")
        }
        guard adapter != nil, handshake != nil, tunnel_heartbeat_is_active() else {
            releaseTransport()
            throw IdeviceGatewayError(.connectionFailed, reason: "CoreDevice tunnel returned incomplete handles")
        }
        debugLog("[SIDESTORE_COREDEVICE] TRANSPORT_CREATE_PASS")
    }

'''
    text = replace_once(
        text,
        "    private func ensureRPConnection() throws {",
        ensure_coredevice + """    private func ensureRPConnection() throws {
        if !isRPPairing {
            try ensureCoreDeviceConnection()
            return
        }
""",
        "CoreDevice connection method",
    )

    text = replace_once(
        text,
        """        if isRPPairing {
            return try performWithService(connect: connectRP, cleanup: cleanup, serviceName: serviceName, action: action)
        } else {
            return try performWithTcpService(connect: connectLockdown, cleanup: cleanup, serviceName: serviceName, action: action)
        }
""",
        """        _ = connectLockdown
        return try performWithService(connect: connectRP, cleanup: cleanup, serviceName: serviceName, action: action)
""",
        "all services over RSD",
    )
    text = replace_once(text, "        if isRPPairing {\n            do {", "        if isRPPairing || pairingFileType == .lockdown {\n            do {", "fetch UDID over RSD")
    text = replace_once(text, "        if isRPPairing {\n            try mountPersonalizedDdiRsd", "        if isRPPairing || pairingFileType == .lockdown {\n            try mountPersonalizedDdiRsd", "DDI over RSD")

    new_stage = r'''    private func syncYeetAppAfc(bundleId: String, ipaBytes: Data) throws {
        debugLog("[SELF_REFRESH] AFC_CONNECT_START bundle_id=\(bundleId)")
        try verifyInitialized()
        try performWithEitherService(
            connectRP: afc_client_connect_rsd,
            connectLockdown: afc_client_connect,
            cleanup: afc_client_free,
            serviceName: "AFC client"
        ) { client in
            debugLog("[SELF_REFRESH] AFC_CONNECT_PASS")
            let stagingDir = MinimuxerConstants.pkgPath
            _ = stagingDir.withCString { afc_make_directory(client, $0) }
            let bundleDir = "\(stagingDir)/\(bundleId)"
            _ = bundleDir.withCString { afc_make_directory(client, $0) }

            let path = "\(bundleDir)/app.ipa"
            var fileHandle: OpaquePointer? = nil
            debugLog("[SELF_REFRESH] AFC_FILE_OPEN_START path=\(path)")
            let openError = path.withCString {
                afc_file_open(client, $0, AfcFopenMode(rawValue: 4), &fileHandle)
            }
            if let openError {
                let message = getErrorMessage(from: openError)
                idevice_error_free(openError)
                throw IdeviceGatewayError(.serviceError, reason: "AFC file open failed: \(message)")
            }
            guard let fileHandle else {
                throw IdeviceGatewayError(.serviceError, reason: "AFC file open returned nil")
            }
            debugLog("[SELF_REFRESH] AFC_FILE_OPEN_PASS")

            var closeNeeded = true
            defer {
                if closeNeeded, let closeError = afc_file_close(fileHandle) {
                    idevice_error_free(closeError)
                }
            }

            let chunkSize = 32 * 1024
            var offset = 0
            var chunkIndex = 0
            debugLog("[SELF_REFRESH] IPA_STAGE_START size=\(ipaBytes.count)")
            try ipaBytes.withUnsafeBytes { bytes in
                guard let base = bytes.baseAddress?.assumingMemoryBound(to: UInt8.self) else {
                    if ipaBytes.isEmpty { return }
                    throw IdeviceGatewayError(.serviceError, reason: "IPA data has no base address")
                }
                while offset < ipaBytes.count {
                    let requested = min(chunkSize, ipaBytes.count - offset)
                    chunkIndex += 1
                    let started = CFAbsoluteTimeGetCurrent()
                    debugLog("[SELF_REFRESH] AFC_WRITE_BEGIN chunk_index=\(chunkIndex) offset=\(offset) requested=\(requested)")
                    let writeError = afc_file_write(fileHandle, base.advanced(by: offset), requested)
                    let elapsedMs = Int((CFAbsoluteTimeGetCurrent() - started) * 1000)
                    if let writeError {
                        let message = getErrorMessage(from: writeError)
                        idevice_error_free(writeError)
                        debugLog("[SELF_REFRESH] AFC_WRITE_FAIL chunk_index=\(chunkIndex) elapsed_ms=\(elapsedMs) error=\(message)")
                        throw IdeviceGatewayError(.serviceError, reason: "AFC write failed at offset \(offset): \(message)")
                    }
                    // A nil FFI result means write_entire completed the full requested slice.
                    offset += requested
                    debugLog("[SELF_REFRESH] AFC_WRITE_RETURN chunk_index=\(chunkIndex) written=\(requested) elapsed_ms=\(elapsedMs)")
                }
            }
            debugLog("[SELF_REFRESH] STAGING_WRITE_LOOP_DONE staged_bytes=\(offset)")

            debugLog("[SELF_REFRESH] AFC_FILE_CLOSE_START")
            closeNeeded = false
            if let closeError = afc_file_close(fileHandle) {
                let message = getErrorMessage(from: closeError)
                idevice_error_free(closeError)
                throw IdeviceGatewayError(.serviceError, reason: "AFC file close failed: \(message)")
            }
            debugLog("[SELF_REFRESH] AFC_FILE_CLOSE_PASS")

            let (_, stagedSize) = try afcGetFileInfo(client: client, path: path)
            let sizeMatches = stagedSize == Int64(ipaBytes.count)
            debugLog("[SELF_REFRESH] STAGED_FILE_SIZE=\(stagedSize)")
            debugLog("[SELF_REFRESH] STAGED_FILE_SIZE_MATCH=\(sizeMatches)")
            guard sizeMatches else {
                throw IdeviceGatewayError(
                    .serviceError,
                    reason: "Staged IPA size mismatch: expected \(ipaBytes.count), got \(stagedSize)"
                )
            }
            debugLog("[SELF_REFRESH] SIDESTORE_STAGE_PASS bundle_id=\(bundleId) bytes=\(stagedSize)")
        }
    }

'''
    text = replace_region(
        text,
        "    private func syncYeetAppAfc(bundleId: String, ipaBytes: Data) throws {",
        "    private func syncInstallIpa(bundleId: String) throws {",
        new_stage,
        "AFC staging implementation",
    )

    new_install = r'''    private func verifyInstalledBundle(client: OpaquePointer, bundleId: String) throws {
        debugLog("[SELF_REFRESH] POST_INSTALL_BROWSE_START bundle_id=\(bundleId)")
        var result: UnsafeMutableRawPointer? = nil
        var count = 0
        let browseError = bundleId.withCString { bundlePointer in
            var bundleIds: [UnsafePointer<CChar>?] = [bundlePointer]
            return installation_proxy_get_apps(client, nil, &bundleIds, 1, &result, &count)
        }
        if let browseError {
            let message = getErrorMessage(from: browseError)
            idevice_error_free(browseError)
            throw IdeviceGatewayError(.serviceError, reason: "Post-install browse failed: \(message)")
        }
        debugLog("[SELF_REFRESH] POST_INSTALL_BROWSE_PASS count=\(count)")
        guard let result else {
            throw IdeviceGatewayError(.serviceError, reason: "Post-install browse returned no result")
        }
        let applications = result.assumingMemoryBound(to: plist_t?.self)
        defer { idevice_plist_array_free(applications, UInt(count)) }

        var matchedIdentifier: String? = nil
        var matchedVersion: String? = nil
        for index in 0..<count {
            guard let application = applications[index] else { continue }
            if let identifierNode = plist_dict_get_item(application, "CFBundleIdentifier") {
                let identifier = getRustPlistString(identifierNode)
                if identifier == bundleId {
                    matchedIdentifier = identifier
                    if let versionNode = plist_dict_get_item(application, "CFBundleShortVersionString") {
                        matchedVersion = getRustPlistString(versionNode)
                    }
                    break
                }
            }
        }
        guard matchedIdentifier == bundleId else {
            throw IdeviceGatewayError(.serviceError, reason: "Installed bundle was not found: \(bundleId)")
        }
        debugLog("[SELF_REFRESH] INSTALLED_APP_LOOKUP_PASS bundle_id=\(bundleId) version=\(matchedVersion ?? "unknown")")
        debugLog("[SELF_REFRESH] SIDESTORE_POST_INSTALL_VERIFY_PASS bundle_id=\(bundleId)")
    }

    private func syncInstallIpa(bundleId: String) throws {
        debugLog("[SELF_REFRESH] INSTALL_PROXY_CONNECT_START bundle_id=\(bundleId)")
        try verifyInitialized()
        try performWithEitherService(
            connectRP: installation_proxy_connect_rsd,
            connectLockdown: installation_proxy_connect,
            cleanup: installation_proxy_client_free,
            serviceName: "instproxy"
        ) { client in
            debugLog("[SELF_REFRESH] INSTALL_PROXY_CONNECT_PASS")
            let path = "PublicStaging/\(bundleId)/app.ipa"
            debugLog("[SELF_REFRESH] SIDESTORE_INSTALL_REQUEST_START path=\(path)")
            let installError = path.withCString { installation_proxy_install(client, $0, nil) }
            if let installError {
                let message = getErrorMessage(from: installError)
                idevice_error_free(installError)
                throw IdeviceGatewayError(.serviceError, reason: "IPA install failed: \(message)")
            }
            // installation_proxy_install waits for the terminal Complete status.
            debugLog("[SELF_REFRESH] SIDESTORE_INSTALL_REQUEST_PASS bundle_id=\(bundleId)")
            debugLog("[SELF_REFRESH] SIDESTORE_INSTALL_COMPLETE bundle_id=\(bundleId)")
            try verifyInstalledBundle(client: client, bundleId: bundleId)
        }
    }

'''
    text = replace_region(
        text,
        "    private func syncInstallIpa(bundleId: String) throws {",
        "    private func getAppPaths(appId: String) throws -> (container: String, bundlePath: String) {",
        new_install,
        "InstallationProxy implementation",
    )

    text = replace_once(
        text,
        """            let plistArray = resultPtr.assumingMemoryBound(to: plist_t?.self)
            var container = ""
""",
        """            let plistArray = resultPtr.assumingMemoryBound(to: plist_t?.self)
            defer { idevice_plist_array_free(plistArray, UInt(outLen)) }
            var container = ""
""",
        "browse result ownership",
    )
    text = replace_once(text, "            free(outResult)\n", "", "remove incorrect browse free")

    old_heartbeat = """       debugLog("[IdeviceGateway] performHeartbeat() called, interval: \\(interval)")
       try verifyInitialized()
       try performWithEitherService(
"""
    new_heartbeat = """       debugLog("[IdeviceGateway] performHeartbeat() called, interval: \\(interval)")
       try verifyInitialized()
       if !isRPPairing {
           try ensureCoreDeviceConnection()
           guard tunnel_heartbeat_is_active() else {
               throw IdeviceGatewayError(.connectionFailed, reason: "CoreDevice heartbeat is inactive")
           }
           newInterval.pointee = 60
           verboseLog("[SIDESTORE_COREDEVICE] HEARTBEAT_ACTIVE")
           return
       }
       try performWithEitherService(
"""
    text = replace_once(text, old_heartbeat, new_heartbeat, "avoid duplicate heartbeat client")

    text = replace_once(
        text,
        """        var err: UnsafeMutablePointer<IdeviceFfiError>? = nil
        var peerDevicePtr: UnsafeMutablePointer<RpPairingPeerDeviceC>? = nil
""",
        """        var err: UnsafeMutablePointer<IdeviceFfiError>? = nil
""",
        "pinned rppairing result type",
    )
    text = replace_once(
        text,
        """                    },
                    pinContextPtr,
                    &peerDevicePtr
                )""",
        """                    },
                    pinContextPtr
                )""",
        "pinned rppairing arguments",
    )
    text = replace_once(
        text,
        """        var peerName = hostName
        var peerModel = hostModel
        var peerUdid: String? = nil
        var peerAltIrk: [UInt8]? = nil

        if let peer = peerDevicePtr {
            defer { rppairing_peer_device_free(peer) }
            let p = peer.pointee
            if let namePtr = p.name {
                peerName = String(cString: namePtr)
            }
            if let modelPtr = p.model {
                peerModel = String(cString: modelPtr)
            }
            if let udidPtr = p.udid {
                peerUdid = String(cString: udidPtr)
            }
            peerAltIrk = withUnsafeBytes(of: p.alt_irk) { Array($0) }
        }

        return try finalizeAndSavePairedDevice(
            rpf: rpf,
            hostName: peerName,
            hostModel: peerModel,
            outPath: outPath,
            fallbackUdid: peerUdid ?? identifier,
            initialAltIrk: peerAltIrk
        )""",
        """        // This idevice revision updates rpf in place and exposes no peer-result struct.
        return try finalizeAndSavePairedDevice(
            rpf: rpf,
            hostName: hostName,
            hostModel: hostModel,
            outPath: outPath,
            fallbackUdid: identifier,
            initialAltIrk: nil
        )""",
        "pinned rppairing metadata",
    )

    dispatch_count = text.count("withFFIDispatch {")
    if dispatch_count < 10:
        die(f"expected gateway async wrappers, found {dispatch_count}")
    text = text.replace("withFFIDispatch {", "withFFIDispatch(on: self.ffiQueue) {")
    path.write_text(text, encoding="utf-8")
    verify_gateway(text)


def patch_heartbeat_service(minimuxer: Path) -> None:
    path = minimuxer / "Sources" / "Services" / "HeartbeatService.swift"
    text = path.read_text(encoding="utf-8")
    marker = "CoreDevice heartbeat is maintained by Rust; poll its state without spinning."
    if marker not in text:
        text = replace_once(
            text,
            """                currentInterval = try await self.gateway.performHeartbeat(interval: currentInterval)
                lastBeatSuccessful = true
                lastErrorDescription = nil""",
            f"""                currentInterval = try await self.gateway.performHeartbeat(interval: currentInterval)
                lastBeatSuccessful = true
                lastErrorDescription = nil
                // {marker}
                if self.gateway.getPairingFileType() == .lockdown {{
                    try? await Task.sleep(nanoseconds: MinimuxerConstants.heartbeatSleepNs)
                }}""",
            "Lockdown heartbeat polling delay",
        )
        path.write_text(text, encoding="utf-8")
    if marker not in text:
        die("Lockdown heartbeat polling delay missing")


def verify_gateway(text: str) -> None:
    required = [
        MARKER,
        "coreDeviceProvider",
        "idevice_set_transport_log_callback(sideStoreTransportLog)",
        "tunnel_create_usb(provider, &adapter, &handshake)",
        "tunnel_heartbeat_is_active()",
        "withFFIDispatch(on: self.ffiQueue)",
        "ffiQueue.sync",
        "SIDESTORE_STAGE_PASS",
        "STAGED_FILE_SIZE_MATCH",
        "SIDESTORE_INSTALL_COMPLETE",
        "SIDESTORE_POST_INSTALL_VERIFY_PASS",
        "idevice_plist_array_free(applications, UInt(count))",
        "idevice_plist_array_free(plistArray, UInt(outLen))",
    ]
    missing = [needle for needle in required if needle not in text]
    if missing:
        die(f"gateway verification failed: {missing}")
    if "free(outResult)" in text:
        die("gateway still uses free(outResult) for a Rust plist array")
    unsupported = ["RpPairingPeerDeviceC", "rppairing_peer_device_free"]
    present = [needle for needle in unsupported if needle in text]
    if present:
        die(f"gateway still uses unsupported pinned idevice APIs: {present}")


def patch_sign_marker(sidestore: Path) -> None:
    path = sidestore / "SideStore" / "Core" / "Operations" / "PipelineOperations" / "ResignAppOperation.swift"
    text = path.read_text(encoding="utf-8")
    marker = "SIDESTORE_SIGN_PASS"
    if marker not in text:
        text = replace_once(
            text,
            """        // Use appBundleURL since we need an app bundle, not .ipa.
        guard let resignedAppBundle = ALTApplication(fileURL: appBundleURL) else { throw OperationError.invalidApp }

        self.debugLog("[ResignAppOperation] Resigned app \\(self.context.bundleIdentifier) to \\(resignedAppBundle.bundleIdentifier).")
""".replace(
                "OperationError.invalidApp }\n\n        self.debugLog",
                "OperationError.invalidApp }\n        \n        self.debugLog",
            ),
            """        // Use appBundleURL since we need an app bundle, not .ipa.
        guard let resignedAppBundle = ALTApplication(fileURL: appBundleURL) else { throw OperationError.invalidApp }
        #if !targetEnvironment(simulator)
        guard resignedAppBundle.provisioningProfile != nil else { throw OperationError.invalidApp }
        #endif

        if appBundle.isAltStoreApp {
            self.debugLog("[SELF_REFRESH] SIDESTORE_SIGN_PASS bundle_id=\\(resignedAppBundle.bundleIdentifier)")
        }
        self.debugLog("[ResignAppOperation] Resigned app \\(self.context.bundleIdentifier) to \\(resignedAppBundle.bundleIdentifier).")
""",
            "SideStore signing marker",
        )
        path.write_text(text, encoding="utf-8")


def patch_relaunch_verification(sidestore: Path) -> None:
    path = sidestore / "AltStore" / "AppDelegate.swift"
    text = path.read_text(encoding="utf-8")
    if "SELF_REFRESH_COMPLETE" in text:
        return
    text = replace_once(
        text,
        """            debugLog("[AppDelegate] reconcileSelfReinstallation: App reinstallation confirmed (BundlePath changed)! Applying staged updates to SideStore app in database.")
            let context = DatabaseManager.shared.persistentContainer.newBackgroundContext()
""",
        """            debugLog("[AppDelegate] reconcileSelfReinstallation: App reinstallation confirmed (BundlePath changed)! Applying staged updates to SideStore app in database.")
            debugLog("[SELF_REFRESH] SIDESTORE_POST_INSTALL_VERIFY_PASS bundle_path_changed=true")
            let context = DatabaseManager.shared.persistentContainer.newBackgroundContext()
""",
        "relaunch path verification",
    )
    text = replace_once(
        text,
        """            var didSave = false
            context.performAndWait {
""",
        """            var didSave = false
            var didReconcile = false
            context.performAndWait {
""",
        "reconcile state",
    )
    text = replace_once(
        text,
        """                    if let _ = InstalledApp.deserialize(from: jsonData, format: .json, context: context) {
                        if context.hasChanges {
""",
        """                    if let _ = InstalledApp.deserialize(from: jsonData, format: .json, context: context) {
                        didReconcile = true
                        if context.hasChanges {
""",
        "reconcile success state",
    )
    text = replace_once(
        text,
        """            if didSave {
                Task {
                    await WidgetDataManager.publishCurrentInstalledApps(in: context)
                }
            }
""",
        """            if didSave {
                Task {
                    await WidgetDataManager.publishCurrentInstalledApps(in: context)
                }
            }
            if didReconcile {
                debugLog("[SELF_REFRESH] SELF_REFRESH_COMPLETE bundle_path_changed=true database_reconciled=true")
            }
""",
        "self-refresh completion marker",
    )
    path.write_text(text, encoding="utf-8")


def verify_side_store(sidestore: Path) -> None:
    resign = (sidestore / "SideStore" / "Core" / "Operations" / "PipelineOperations" / "ResignAppOperation.swift").read_text(encoding="utf-8")
    app_delegate = (sidestore / "AltStore" / "AppDelegate.swift").read_text(encoding="utf-8")
    if "SIDESTORE_SIGN_PASS" not in resign:
        die("SideStore signing marker missing")
    if "SELF_REFRESH_COMPLETE" not in app_delegate or "bundle_path_changed=true" not in app_delegate:
        die("SideStore relaunch verification missing")


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: patch_sidestore_integration.py <minimuxer-root> <sidestore-root>")
    minimuxer = Path(sys.argv[1]).resolve()
    sidestore = Path(sys.argv[2]).resolve()
    if not (minimuxer / "DeviceGateway" / "idevice" / "IdeviceGateway.swift").is_file():
        die(f"invalid minimuxer checkout: {minimuxer}")
    if not (sidestore / "AltStore.xcodeproj").is_dir():
        die(f"invalid SideStore checkout: {sidestore}")

    patch_coredevice_route_selection(minimuxer)
    patch_gateway(minimuxer)
    patch_heartbeat_service(minimuxer)
    patch_sign_marker(sidestore)
    patch_relaunch_verification(sidestore)
    verify_side_store(sidestore)
    print("V29 SideStore CoreDevice integration patch applied and verified")


if __name__ == "__main__":
    main()
