#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_coredevice_lockdown.py <IdeviceGateway.swift>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-V10-LOCKDOWN-BOOTSTRAP]"
if marker in s:
    required = [
        "lockdownProvider",
        "idevice_new_tcp_socket",
        "lockdownd_new",
        "lockdownd_pair",
        "idevice_pairing_file_serialize",
        "idevice_tcp_provider_new",
        "tunnel_create_usb",
        "LOCKDOWN_PAIR_SUCCESS",
        "COREDEVICE_TUNNEL_SUCCESS",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"v10 Lockdown bootstrap marker present but patch incomplete: {missing}")
    print("v10 Lockdown bootstrap + CoreDeviceProxy patch already present and verified")
    raise SystemExit(0)

old_vars = '''    private var pairingFile: OpaquePointer? = nil
    private var adapter: OpaquePointer? = nil
    private var handshake: OpaquePointer? = nil
'''
new_vars = '''    private var pairingFile: OpaquePointer? = nil
    // v10: CoreDeviceProxy needs a traditional Lockdown PairRecord. When the
    // imported file is an RP pairing record, SideStore bootstraps a Lockdown
    // record through the proven 10.7.0.1:62078 reflection path and caches it.
    private var lockdownProvider: OpaquePointer? = nil
    private let lockdownPairingCacheKey = "com.SideStore.CoreDeviceLockdownPairing.v10"
    private let lockdownHostIDKey = "com.SideStore.CoreDeviceLockdownHostID.v10"
    private let lockdownSystemBUIDKey = "com.SideStore.CoreDeviceLockdownSystemBUID.v10"
    private var adapter: OpaquePointer? = nil
    private var handshake: OpaquePointer? = nil
'''
if old_vars not in s:
    raise SystemExit("Could not locate IdeviceGateway state variables")
s = s.replace(old_vars, new_vars, 1)

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
'''
new_cleanup = '''        // The TCP provider owns the Lockdown pairing file passed to it.
        // Release the provider first; keep the imported RP pairing record
        // independent so the diagnostic fallback remains available.
        if let lockdownProvider = self.lockdownProvider {
            debugLog("[SS-V9-COREDEVICE] PROVIDER_FREE")
            idevice_provider_free(lockdownProvider)
            self.lockdownProvider = nil
        }
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
'''
if old_cleanup not in s:
    raise SystemExit("Could not locate cleanup pairing-file block")
s = s.replace(old_cleanup, new_cleanup, 1)

old_set_ip = '''        self.deviceEndpointIp = ip
        
        // Invalidate current cached connections
'''
new_set_ip = '''        self.deviceEndpointIp = ip

        // A TcpProvider captures the endpoint address. Recreate it if the
        // configured endpoint changes; the cached PairRecord remains valid.
        if let lockdownProvider = self.lockdownProvider {
            debugLog("[SS-V9-COREDEVICE] PROVIDER_INVALIDATE endpoint_changed=true")
            idevice_provider_free(lockdownProvider)
            self.lockdownProvider = nil
        }
        
        // Invalidate current cached connections
'''
if old_set_ip not in s:
    raise SystemExit("Could not locate setDeviceEndpointIp assignment")
s = s.replace(old_set_ip, new_set_ip, 1)

old_type = '''            self.pairingFileType = pairingType
            isRPPairing = (pairingType == .rppairing)
'''
new_type = '''            self.pairingFileType = pairingType
            isRPPairing = (pairingType == .rppairing)
            debugLog("[SS-V9-COREDEVICE] PAIRING_TYPE type=\\(pairingType.rawValue) coredevice_proxy_direct_eligible=\\(pairingType == .lockdown)")
            if pairingType == .rppairing {
                // Keep the historical marker for build-stack compatibility, but
                // v10 now resolves the limitation by creating a Lockdown record.
                debugLog("[SS-V9-COREDEVICE] RP_FILE_CANNOT_USE_COREDEVICE_PROXY_DIRECT bootstrap_required=true")
                debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] RP_FILE_BOOTSTRAP_ELIGIBLE true")
            }
'''
if old_type not in s:
    raise SystemExit("Could not locate pairing type assignment")
s = s.replace(old_type, new_type, 1)

ensure_pos = s.find("    private func ensureRPConnection() throws {")
if ensure_pos < 0:
    raise SystemExit("Could not locate ensureRPConnection")

helper = r'''    private func stableLockdownIdentifier(forKey key: String) -> String {
        let defaults = UserDefaults.standard
        if let existing = defaults.string(forKey: key), !existing.isEmpty {
            return existing
        }
        let created = UUID().uuidString.uppercased()
        defaults.set(created, forKey: key)
        return created
    }

    private func parseLockdownPairingFile(_ data: Data, source: String) throws -> OpaquePointer {
        var parsed: OpaquePointer? = nil
        var parseErr: UnsafeMutablePointer<IdeviceFfiError>? = nil
        data.withUnsafeBytes { (buf: UnsafeRawBufferPointer) in
            guard let base = buf.baseAddress?.assumingMemoryBound(to: UInt8.self) else { return }
            parseErr = idevice_pairing_file_from_bytes(base, UInt(data.count), &parsed)
        }
        if let parseErr {
            let msg = getErrorMessage(from: parseErr)
            idevice_error_free(parseErr)
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Cached Lockdown PairRecord parse failed (\(source)): \(msg)")
        }
        guard let parsed else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Cached Lockdown PairRecord parse returned nil (\(source))")
        }
        return parsed
    }

    private func cacheLockdownPairingFile(_ pairing: OpaquePointer) throws {
        var bytes: UnsafeMutablePointer<UInt8>? = nil
        var length: UInt = 0
        let serializeErr = idevice_pairing_file_serialize(pairing, &bytes, &length)
        if let serializeErr {
            let msg = getErrorMessage(from: serializeErr)
            idevice_error_free(serializeErr)
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Lockdown PairRecord serialization failed: \(msg)")
        }
        guard let bytes, length > 0 else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Lockdown PairRecord serialization returned no data")
        }
        let data = Data(bytes: bytes, count: Int(length))
        idevice_data_free(bytes, length)
        UserDefaults.standard.set(data, forKey: lockdownPairingCacheKey)
        debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] CACHE_SAVE_SUCCESS bytes=\(data.count)")
    }

    private func loadCachedLockdownPairingFile() -> OpaquePointer? {
        guard let data = UserDefaults.standard.data(forKey: lockdownPairingCacheKey), !data.isEmpty else {
            debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] CACHE_MISS")
            return nil
        }
        do {
            let parsed = try parseLockdownPairingFile(data, source: "UserDefaults")
            debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] CACHE_HIT bytes=\(data.count)")
            return parsed
        } catch {
            debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] CACHE_INVALID reason=\(error.localizedDescription)")
            UserDefaults.standard.removeObject(forKey: lockdownPairingCacheKey)
            return nil
        }
    }

    // Create a traditional Lockdown PairRecord directly against lockdownd over
    // the already-proven LocalVPN reflection path. iOS displays its standard
    // "Trust This Computer" prompt on first bootstrap. The generated record is
    // persisted and subsequently feeds CoreDeviceProxy without RemotePairing's
    // dynamic createListener path.
    private func bootstrapLockdownPairingFile(deviceEndpointIp: String) throws -> OpaquePointer {
        if let cached = loadCachedLockdownPairingFile() {
            return cached
        }

        debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] START endpoint=\(deviceEndpointIp) port=62078")
        var rawDevice: OpaquePointer? = nil
        var socketErr: UnsafeMutablePointer<IdeviceFfiError>? = nil
        let label = "SideStore-LockdownBootstrap"
        try label.withCString { labelPtr in
            try withSockaddr(ip: deviceEndpointIp, port: 62078) { sockaddrPtr, sockaddrLen in
                socketErr = idevice_new_tcp_socket(
                    sockaddrPtr,
                    sockaddrLen,
                    labelPtr,
                    &rawDevice
                )
            }
        }
        if let socketErr {
            let msg = getErrorMessage(from: socketErr)
            idevice_error_free(socketErr)
            throw IdeviceGatewayError(.connectionFailed, reason: "Raw lockdownd connection failed: \(msg)")
        }
        guard let rawDevice else {
            throw IdeviceGatewayError(.connectionFailed, reason: "Raw lockdownd connection returned nil")
        }
        debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] RAW_LOCKDOWN_CONNECTED")

        var lockdownClient: OpaquePointer? = nil
        let clientErr = lockdownd_new(rawDevice, &lockdownClient)
        if let clientErr {
            let msg = getErrorMessage(from: clientErr)
            idevice_error_free(clientErr)
            idevice_free(rawDevice)
            throw IdeviceGatewayError(.connectionFailed, reason: "lockdownd_new failed: \(msg)")
        }
        guard let lockdownClient else {
            throw IdeviceGatewayError(.connectionFailed, reason: "lockdownd_new returned nil")
        }
        defer { lockdownd_client_free(lockdownClient) }

        let hostID = stableLockdownIdentifier(forKey: lockdownHostIDKey)
        let systemBUID = stableLockdownIdentifier(forKey: lockdownSystemBUIDKey)
        let hostName = "SideStore"
        var generatedPairing: OpaquePointer? = nil
        var pairErr: UnsafeMutablePointer<IdeviceFfiError>? = nil
        debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] PAIR_REQUEST_START trust_prompt_expected=true")
        hostID.withCString { hostIDPtr in
            systemBUID.withCString { buidPtr in
                hostName.withCString { hostNamePtr in
                    pairErr = lockdownd_pair(
                        lockdownClient,
                        hostIDPtr,
                        buidPtr,
                        hostNamePtr,
                        &generatedPairing
                    )
                }
            }
        }
        if let pairErr {
            let code = pairErr.pointee.code
            let subCode = pairErr.pointee.sub_code
            let msg = getErrorMessage(from: pairErr)
            idevice_error_free(pairErr)
            debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] LOCKDOWN_PAIR_FAILED code=\(code) subCode=\(subCode) message=\(msg)")
            throw IdeviceGatewayError(.connectionFailed, reason: "Lockdown pairing failed: \(msg)")
        }
        guard let generatedPairing else {
            throw IdeviceGatewayError(.invalidPairingFile, reason: "Lockdown pairing succeeded without a PairRecord")
        }

        do {
            try cacheLockdownPairingFile(generatedPairing)
        } catch {
            idevice_pairing_file_free(generatedPairing)
            throw error
        }
        debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] LOCKDOWN_PAIR_SUCCESS")
        return generatedPairing
    }

    // Preferred iOS 17.4+ path: create CoreDeviceProxy over the stable
    // Lockdown/62078 connection. For an imported Lockdown record we consume the
    // existing pointer. For an imported RP record we bootstrap/cache a separate
    // Lockdown record while preserving the RP pointer for forensic fallback.
    private func ensureCoreDeviceProxyConnection(deviceEndpointIp: String) throws {
        debugLog("[SS-V9-COREDEVICE] START endpoint=\(deviceEndpointIp) lockdown_port=62078")

        if adapter != nil && handshake != nil {
            debugLog("[SS-V9-COREDEVICE] REUSE_EXISTING_TUNNEL")
            return
        }

        if lockdownProvider == nil {
            let consumesPrimaryPairing: Bool
            let providerPairingFile: OpaquePointer
            if pairingFileType == .lockdown {
                guard let pairingFile else {
                    throw IdeviceGatewayError(.invalidPairingFile, reason: "Imported Lockdown PairRecord is nil")
                }
                consumesPrimaryPairing = true
                providerPairingFile = pairingFile
                debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] PROVIDER_INPUT source=imported_lockdown")
            } else if pairingFileType == .rppairing {
                consumesPrimaryPairing = false
                providerPairingFile = try bootstrapLockdownPairingFile(deviceEndpointIp: deviceEndpointIp)
                debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] PROVIDER_INPUT source=bootstrapped_lockdown")
            } else {
                throw IdeviceGatewayError(.invalidPairingFile, reason: "Unsupported pairing type for CoreDeviceProxy: \(pairingFileType.rawValue)")
            }

            var provider: OpaquePointer? = nil
            var providerErr: UnsafeMutablePointer<IdeviceFfiError>? = nil
            let label = "SideStore-CoreDeviceProxy"
            try label.withCString { labelPtr in
                try withSockaddr(ip: deviceEndpointIp, port: 62078) { sockaddrPtr, _ in
                    debugLog("[SS-V9-COREDEVICE] PROVIDER_CREATE_START endpoint=\(deviceEndpointIp)")
                    providerErr = idevice_tcp_provider_new(
                        sockaddrPtr,
                        providerPairingFile,
                        labelPtr,
                        &provider
                    )
                }
            }

            if let providerErr {
                let msg = getErrorMessage(from: providerErr)
                debugLog("[SS-V9-COREDEVICE] PROVIDER_CREATE_FAILED message=\(msg)")
                idevice_error_free(providerErr)
                // idevice_tcp_provider_new consumes only after validating address
                // and label. Both are valid here, but if it returned early retain
                // conservative ownership cleanup for the generated pointer.
                if !consumesPrimaryPairing {
                    idevice_pairing_file_free(providerPairingFile)
                }
                throw IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy TCP provider creation failed: \(msg)")
            }

            guard let provider else {
                if !consumesPrimaryPairing {
                    idevice_pairing_file_free(providerPairingFile)
                }
                throw IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy TCP provider creation returned nil")
            }

            if consumesPrimaryPairing {
                self.pairingFile = nil
            }
            self.lockdownProvider = provider
            debugLog("[SS-V9-COREDEVICE] PROVIDER_CREATE_SUCCESS endpoint=\(deviceEndpointIp)")
        }

        guard let lockdownProvider else {
            throw IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy provider unavailable")
        }

        debugLog("[SS-V9-COREDEVICE] COREDEVICE_TUNNEL_START service=com.apple.internal.devicecompute.CoreDeviceProxy")
        let tunnelErr = tunnel_create_usb(lockdownProvider, &adapter, &handshake)
        if let tunnelErr {
            let code = tunnelErr.pointee.code
            let subCode = tunnelErr.pointee.sub_code
            let msg = getErrorMessage(from: tunnelErr)
            idevice_error_free(tunnelErr)
            debugLog("[SS-V9-COREDEVICE] COREDEVICE_TUNNEL_FAILED code=\(code) subCode=\(subCode) message=\(msg)")
            invalidateConnection()
            if pairingFileType == .rppairing {
                // A structurally valid cache may still have been revoked by iOS.
                // Remove it so the next call performs a clean Trust bootstrap.
                UserDefaults.standard.removeObject(forKey: lockdownPairingCacheKey)
                if let provider = self.lockdownProvider {
                    idevice_provider_free(provider)
                    self.lockdownProvider = nil
                }
                debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] CACHE_INVALIDATED_AFTER_TUNNEL_FAILURE")
            }
            throw IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy tunnel failed: \(msg)")
        }

        guard adapter != nil, handshake != nil else {
            invalidateConnection()
            throw IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy tunnel returned incomplete adapter/handshake")
        }

        debugLog("[SS-V9-COREDEVICE] COREDEVICE_TUNNEL_SUCCESS rsd_handshake_ready=true")
        debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] TRANSPORT_SUCCESS path=lockdown-coredevice-proxy")
    }

'''
s = s[:ensure_pos] + helper + s[ensure_pos:]

old_guard_section = '''        guard let pairingFile = pairingFile else {
            debugLog("[IdeviceGateway] ensureRPConnection() failed because pairingFile is nil")
            throw IdeviceGatewayError(.invalidPairingFile, reason: "pairingFile is nil")
        }

        guard let deviceEndpointIp = deviceEndpointIp else {
            debugLog("[IdeviceGateway] ensureRPConnection() failed because deviceEndpointIp is nil")
            throw IdeviceGatewayError(.deviceEndpointIpNotAvailable)
        }

        let hostname = MinimuxerConstants.appName
'''
new_guard_section = '''        guard let deviceEndpointIp = deviceEndpointIp else {
            debugLog("[IdeviceGateway] ensureRPConnection() failed because deviceEndpointIp is nil")
            throw IdeviceGatewayError(.deviceEndpointIpNotAvailable)
        }

        if pairingFileType == .lockdown || pairingFileType == .rppairing {
            debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] SELECT transport=lockdown-coredevice-proxy pairing_type=\(pairingFileType.rawValue)")
            do {
                try ensureCoreDeviceProxyConnection(deviceEndpointIp: deviceEndpointIp)
                return
            } catch {
                lastError = error
                debugLog("[SS-V10-LOCKDOWN-BOOTSTRAP] COREDEVICE_PATH_FAILED pairing_type=\(pairingFileType.rawValue) reason=\(error.localizedDescription)")
                if pairingFileType == .lockdown {
                    throw error
                }
                // Retain the old RP transport strictly as a diagnostic fallback.
                // The v8 log already proves it fails at the dynamic listener, but
                // preserving it gives a complete causal trace if bootstrap is denied.
                debugLog("[SS-V9-COREDEVICE] SELECT transport=remote-pairing fallback_reason=coredevice_bootstrap_failed")
            }
        }

        guard let pairingFile = pairingFile else {
            debugLog("[IdeviceGateway] ensureRPConnection() failed because pairingFile is nil")
            throw IdeviceGatewayError(.invalidPairingFile, reason: "pairingFile is nil")
        }

        let hostname = MinimuxerConstants.appName
'''
if old_guard_section not in s:
    raise SystemExit("Could not locate ensureRPConnection guard section")
s = s.replace(old_guard_section, new_guard_section, 1)

required = [
    marker,
    "[SS-V9-COREDEVICE] PAIRING_TYPE",
    "RP_FILE_CANNOT_USE_COREDEVICE_PROXY",
    "idevice_new_tcp_socket",
    "lockdownd_new",
    "lockdownd_pair",
    "idevice_pairing_file_serialize",
    "idevice_data_free",
    "idevice_tcp_provider_new",
    "tunnel_create_usb",
    "LOCKDOWN_PAIR_SUCCESS",
    "COREDEVICE_TUNNEL_SUCCESS",
    "TRANSPORT_SUCCESS path=lockdown-coredevice-proxy",
    "PROVIDER_FREE",
]
missing = [x for x in required if x not in s]
if missing:
    raise SystemExit(f"v10 verification failed: {missing}")

p.write_text(s)
print("v10 automatic Lockdown PairRecord bootstrap + CoreDeviceProxy transport applied and verified")
