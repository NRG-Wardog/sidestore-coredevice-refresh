#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_coredevice_lockdown.py <IdeviceGateway.swift>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-V9-COREDEVICE]"
if marker in s:
    required = [
        "lockdownProvider",
        "tunnel_create_usb",
        "idevice_tcp_provider_new",
        "RP_FILE_CANNOT_USE_COREDEVICE_PROXY",
        "COREDEVICE_TUNNEL_SUCCESS",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"v9 CoreDevice marker present but patch incomplete: {missing}")
    print("v9 CoreDeviceProxy lockdown patch already present and verified")
    raise SystemExit(0)

old_vars = '''    private var pairingFile: OpaquePointer? = nil
    private var adapter: OpaquePointer? = nil
    private var handshake: OpaquePointer? = nil
'''
new_vars = '''    private var pairingFile: OpaquePointer? = nil
    // v9: A lockdown TCP provider owns the traditional pairing file once
    // idevice_tcp_provider_new succeeds. Keep it alive across RSD reconnects.
    private var lockdownProvider: OpaquePointer? = nil
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
new_cleanup = '''        // Provider must be released before any still-unconsumed pairing file.
        // For lockdown mode idevice_tcp_provider_new consumes the file, so
        // self.pairingFile is set to nil immediately after provider creation.
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
        // configured endpoint changes.
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
            debugLog("[SS-V9-COREDEVICE] PAIRING_TYPE type=\\(pairingType.rawValue) coredevice_proxy_eligible=\\(pairingType == .lockdown)")
            if pairingType == .rppairing {
                debugLog("[SS-V9-COREDEVICE] RP_FILE_CANNOT_USE_COREDEVICE_PROXY reason=lockdown_certificate_record_required fallback=remote_pairing")
            }
'''
if old_type not in s:
    raise SystemExit("Could not locate pairing type assignment")
s = s.replace(old_type, new_type, 1)

ensure_pos = s.find("    private func ensureRPConnection() throws {")
if ensure_pos < 0:
    raise SystemExit("Could not locate ensureRPConnection")
helper = r'''    // v9 preferred iOS 17.4+ path: use the traditional lockdown pairing
    // record to start CoreDeviceProxy over the already-working LocalVPN TCP
    // reflection path. This bypasses RemotePairing createListener entirely.
    private func ensureCoreDeviceProxyConnection(deviceEndpointIp: String) throws {
        debugLog("[SS-V9-COREDEVICE] START endpoint=\(deviceEndpointIp) lockdown_port=62078")

        if adapter != nil && handshake != nil {
            debugLog("[SS-V9-COREDEVICE] REUSE_EXISTING_TUNNEL")
            return
        }

        if lockdownProvider == nil {
            guard let pairingFile = pairingFile else {
                let error = IdeviceGatewayError(.invalidPairingFile, reason: "Lockdown pairing file is nil before provider creation")
                lastError = error
                throw error
            }

            var provider: OpaquePointer? = nil
            var providerErr: UnsafeMutablePointer<IdeviceFfiError>? = nil
            let label = "SideStore-CoreDeviceProxy"
            try label.withCString { labelPtr in
                try withSockaddr(ip: deviceEndpointIp, port: 62078) { sockaddrPtr, _ in
                    debugLog("[SS-V9-COREDEVICE] PROVIDER_CREATE_START endpoint=\(deviceEndpointIp)")
                    providerErr = idevice_tcp_provider_new(
                        sockaddrPtr,
                        pairingFile,
                        labelPtr,
                        &provider
                    )
                }
            }

            if let providerErr {
                let msg = getErrorMessage(from: providerErr)
                debugLog("[SS-V9-COREDEVICE] PROVIDER_CREATE_FAILED message=\(msg)")
                defer { idevice_error_free(providerErr) }
                let error = IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy TCP provider creation failed: \(msg)")
                lastError = error
                throw error
            }

            guard let provider else {
                let error = IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy TCP provider creation returned nil")
                lastError = error
                throw error
            }

            // idevice_tcp_provider_new consumes the traditional pairing record.
            self.pairingFile = nil
            self.lockdownProvider = provider
            debugLog("[SS-V9-COREDEVICE] PROVIDER_CREATE_SUCCESS endpoint=\(deviceEndpointIp)")
        }

        guard let lockdownProvider else {
            let error = IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy provider unavailable")
            lastError = error
            throw error
        }

        debugLog("[SS-V9-COREDEVICE] COREDEVICE_TUNNEL_START service=com.apple.internal.devicecompute.CoreDeviceProxy")
        let tunnelErr = tunnel_create_usb(lockdownProvider, &adapter, &handshake)
        if let tunnelErr {
            let code = tunnelErr.pointee.code
            let subCode = tunnelErr.pointee.sub_code
            let msg = getErrorMessage(from: tunnelErr)
            debugLog("[SS-V9-COREDEVICE] COREDEVICE_TUNNEL_FAILED code=\(code) subCode=\(subCode) message=\(msg)")
            idevice_error_free(tunnelErr)
            invalidateConnection()
            let error = IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy tunnel failed: \(msg)")
            lastError = error
            throw error
        }

        guard adapter != nil, handshake != nil else {
            invalidateConnection()
            let error = IdeviceGatewayError(.connectionFailed, reason: "CoreDeviceProxy tunnel returned incomplete adapter/handshake")
            lastError = error
            throw error
        }

        debugLog("[SS-V9-COREDEVICE] COREDEVICE_TUNNEL_SUCCESS rsd_handshake_ready=true")
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

        if pairingFileType == .lockdown {
            debugLog("[SS-V9-COREDEVICE] SELECT transport=lockdown-coredevice-proxy")
            try ensureCoreDeviceProxyConnection(deviceEndpointIp: deviceEndpointIp)
            return
        }

        guard let pairingFile = pairingFile else {
            debugLog("[IdeviceGateway] ensureRPConnection() failed because pairingFile is nil")
            throw IdeviceGatewayError(.invalidPairingFile, reason: "pairingFile is nil")
        }

        debugLog("[SS-V9-COREDEVICE] SELECT transport=remote-pairing fallback_reason=pairing_type_\(pairingFileType.rawValue)")
        let hostname = MinimuxerConstants.appName
'''
if old_guard_section not in s:
    raise SystemExit("Could not locate ensureRPConnection guard section")
s = s.replace(old_guard_section, new_guard_section, 1)

required = [
    "[SS-V9-COREDEVICE] PAIRING_TYPE",
    "RP_FILE_CANNOT_USE_COREDEVICE_PROXY",
    "idevice_tcp_provider_new",
    "tunnel_create_usb",
    "COREDEVICE_TUNNEL_START",
    "COREDEVICE_TUNNEL_SUCCESS",
    "PROVIDER_FREE",
]
missing = [x for x in required if x not in s]
if missing:
    raise SystemExit(f"v9 verification failed: {missing}")

p.write_text(s)
print("v9 CoreDeviceProxy lockdown transport patch applied and verified")
