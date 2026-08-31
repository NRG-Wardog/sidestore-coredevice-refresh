#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_v11_clean_libimobile.py <LibimobiledeviceGateway.swift>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-V11-CLEAN-RP]"

if "import Darwin\n" not in s:
    anchor = "import Foundation\n"
    if anchor not in s:
        raise SystemExit("Could not locate Foundation import")
    s = s.replace(anchor, anchor + "import Darwin\n", 1)

helper_anchor = "private let kDefaultTimeoutMs: Int32 = 120000\n"
helper = r'''

// v11 clean RP: RemotePairing control stays on the LocalVPN peer, while the
// CoreDevice dynamic listener is attempted only through kernel TCP routes.
// This deliberately excludes the v7-v10 NAT46/source-bind/Lockdown bootstrap stack.
private func ssV11DynamicTunnelHosts(controlHost: String) -> [String] {
    var hosts: [String] = ["127.0.0.1", "::1"]

    var head: UnsafeMutablePointer<ifaddrs>? = nil
    if getifaddrs(&head) == 0, let first = head {
        defer { freeifaddrs(head) }
        var cursor: UnsafeMutablePointer<ifaddrs>? = first
        while let ptr = cursor {
            let ifa = ptr.pointee
            if let namePtr = ifa.ifa_name,
               String(cString: namePtr) == "en0",
               let addr = ifa.ifa_addr {
                let family = Int32(addr.pointee.sa_family)
                if family == AF_INET {
                    var sin = UnsafeRawPointer(addr).assumingMemoryBound(to: sockaddr_in.self).pointee
                    var buffer = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
                    if inet_ntop(AF_INET, &sin.sin_addr, &buffer, socklen_t(INET_ADDRSTRLEN)) != nil {
                        hosts.append(String(cString: buffer))
                    }
                } else if family == AF_INET6 {
                    var sin6 = UnsafeRawPointer(addr).assumingMemoryBound(to: sockaddr_in6.self).pointee
                    var buffer = [CChar](repeating: 0, count: Int(INET6_ADDRSTRLEN))
                    if inet_ntop(AF_INET6, &sin6.sin6_addr, &buffer, socklen_t(INET6_ADDRSTRLEN)) != nil {
                        let ip = String(cString: buffer)
                        if ip.lowercased().hasPrefix("fe80:") {
                            hosts.append("\(ip)%en0")
                        } else if ip != "::1" {
                            hosts.append(ip)
                        }
                    }
                }
            }
            cursor = ifa.ifa_next
        }
    }

    // Preserve upstream behavior only as a last resort.
    hosts.append(controlHost)
    var seen = Set<String>()
    return hosts.filter { seen.insert($0).inserted }
}
'''
if "private func ssV11DynamicTunnelHosts" not in s:
    if helper_anchor not in s:
        raise SystemExit("Could not locate Libimobiledevice helper anchor")
    s = s.replace(helper_anchor, helper_anchor + helper, 1)

old_tunnel = '''            var tunnelInfo = rppairing_tunnel_info_t()
            var tunnel: rppairing_tunnel_t? = nil
            let tunErr = rppairing_tunnel_connect(host, tunnelPort, psk, pskLen, &tunnelInfo, &tunnel)
            guard tunErr == RPPAIRING_E_SUCCESS, let tunnel = tunnel else {
                debugLog("[LibimobiledeviceGateway] rppairing_tunnel_connect failed: \\(tunErr.rawValue)")
                throw LibimobiledeviceGatewayError(.connectionFailed, reason: "rppairing_tunnel_connect failed: code \\(tunErr.rawValue)")
            }
'''
new_tunnel = '''            var tunnelInfo = rppairing_tunnel_info_t()
            var tunnel: rppairing_tunnel_t? = nil
            var lastTunnelError: Int32 = -1
            let dynamicHosts = ssV11DynamicTunnelHosts(controlHost: host)
            debugLog("[SS-V11-CLEAN-RP] DYNAMIC_CANDIDATES count=\\(dynamicHosts.count) port=\\(tunnelPort)")

            for (index, candidateHost) in dynamicHosts.enumerated() {
                tunnelInfo = rppairing_tunnel_info_t()
                tunnel = nil
                debugLog("[SS-V11-CLEAN-RP] DYNAMIC_CONNECT_START index=\\(index) host=\\(candidateHost) port=\\(tunnelPort)")
                let candidateErr = rppairing_tunnel_connect(
                    candidateHost,
                    tunnelPort,
                    psk,
                    pskLen,
                    &tunnelInfo,
                    &tunnel
                )
                lastTunnelError = Int32(candidateErr.rawValue)
                if candidateErr == RPPAIRING_E_SUCCESS, tunnel != nil {
                    debugLog("[SS-V11-CLEAN-RP] DYNAMIC_CONNECT_SUCCESS index=\\(index) host=\\(candidateHost) port=\\(tunnelPort)")
                    break
                }
                if let failedTunnel = tunnel {
                    rppairing_tunnel_close(failedTunnel)
                    tunnel = nil
                }
                debugLog("[SS-V11-CLEAN-RP] DYNAMIC_CONNECT_FAILED index=\\(index) host=\\(candidateHost) code=\\(candidateErr.rawValue)")
            }

            guard let tunnel = tunnel else {
                throw LibimobiledeviceGatewayError(
                    .connectionFailed,
                    reason: "[SS-V11-CLEAN-RP] all dynamic RemotePairing tunnel candidates failed; last code \\(lastTunnelError)"
                )
            }
'''
if old_tunnel in s:
    s = s.replace(old_tunnel, new_tunnel, 1)
elif "DYNAMIC_CANDIDATES" not in s:
    raise SystemExit("Could not locate upstream rppairing_tunnel_connect block")

old_fetch = '''    func syncFetchUDID() throws -> String? {
        try verifyInitialized()
        do {
            if let hwUdid = try syncGetLockdownValue(key: "UniqueDeviceID"), !hwUdid.isEmpty {
                debugLog("[LibimobiledeviceGateway] syncFetchUDID: retrieved hardware UDID: \\(hwUdid)")
                self.cachedUDID = hwUdid
                return hwUdid
            }
        } catch {
            debugLog("[LibimobiledeviceGateway] syncFetchUDID: failed to query lockdown: \\(error)")
        }
        return cachedUDID
    }
'''
new_fetch = '''    func syncFetchUDID() throws -> String? {
        try verifyInitialized()
        debugLog("[SS-V11-CLEAN-RP] STRICT_UDID_QUERY_START")
        do {
            guard let hwUdid = try syncGetLockdownValue(key: "UniqueDeviceID"), !hwUdid.isEmpty else {
                throw LibimobiledeviceGatewayError(
                    .connectionFailed,
                    reason: "[SS-V11-CLEAN-RP] RemotePairing/RSD lockdown returned no UniqueDeviceID"
                )
            }
            self.cachedUDID = hwUdid
            debugLog("[SS-V11-CLEAN-RP] STRICT_UDID_QUERY_SUCCESS")
            return hwUdid
        } catch {
            cleanupRPTunnel()
            debugLog("[SS-V11-CLEAN-RP] STRICT_UDID_QUERY_FAILED error=\\(error.localizedDescription)")
            throw error
        }
    }
'''
if old_fetch in s:
    s = s.replace(old_fetch, new_fetch, 1)
elif "STRICT_UDID_QUERY_START" not in s:
    raise SystemExit("Could not locate syncFetchUDID fallback block")

old_verify = '''        let connErr = rppairing_pair_verify(client, &mutIdentity)
        guard connErr == RPPAIRING_E_SUCCESS else {
'''
new_verify = '''        debugLog("[SS-V11-CLEAN-RP] CONTROL_PAIR_VERIFY_START host=\\(host) port=\\(port)")
        let connErr = rppairing_pair_verify(client, &mutIdentity)
        guard connErr == RPPAIRING_E_SUCCESS else {
'''
if old_verify in s:
    s = s.replace(old_verify, new_verify, 1)

old_verify_success = '''        debugLog("[LibimobiledeviceGateway] rppairing_pair_verify succeeded!")

        return try body(client)
'''
new_verify_success = '''        debugLog("[LibimobiledeviceGateway] rppairing_pair_verify succeeded!")
        debugLog("[SS-V11-CLEAN-RP] CONTROL_PAIR_VERIFY_SUCCESS host=\\(host) port=\\(port)")

        return try body(client)
'''
if old_verify_success in s:
    s = s.replace(old_verify_success, new_verify_success, 1)

p.write_text(s)
patched = p.read_text()
required = [
    marker,
    "ssV11DynamicTunnelHosts",
    "DYNAMIC_CANDIDATES",
    "DYNAMIC_CONNECT_START",
    "DYNAMIC_CONNECT_SUCCESS",
    "STRICT_UDID_QUERY_START",
    "STRICT_UDID_QUERY_SUCCESS",
    "CONTROL_PAIR_VERIFY_START",
    "CONTROL_PAIR_VERIFY_SUCCESS",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"v11 clean libimobile verification failed: {missing}")
if "return cachedUDID\n    }" in patched:
    raise SystemExit("v11 clean libimobile verification failed: stale cached-UDID success fallback remains")
print("v11 clean RemotePairingKit/libimobiledevice transport patch applied and verified")
