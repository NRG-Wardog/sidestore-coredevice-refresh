//
//  PacketTunnelProvider.swift
//  TunnelProv - True utun Ingress Hairpin Edition
//

import NetworkExtension
import os.log

@inline(__always)
private func tunnelLog(_ message: @autoclosure () -> String) {
    os_log("[HAIRPIN] %{public}@", type: .default, message())
}

class PacketTunnelProvider: NEPacketTunnelProvider {
    var tunnelIfaceIP: String = TunnelConstants.defaultIfaceIP  // 10.7.1.1
    var tunnelPeerIP: String = TunnelConstants.defaultPeerIP    // 10.7.0.1
    var cachedWiFiIP: UInt32 = 0
    var cachedWiFiIPString: String = ""

    override func startTunnel(options: [String : NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        if let options = options {
            for (key, val) in options {
                tunnelLog("startTunnel option \(key) = \(String(describing: val))")
            }
        }
        
        let providerConfiguration =
            (protocolConfiguration as? NETunnelProviderProtocol)?.providerConfiguration

        if let ifaceIp = options?[TunnelConstants.ifaceIPConfigurationKey] as? String
            ?? providerConfiguration?[TunnelConstants.ifaceIPConfigurationKey] as? String {
            tunnelIfaceIP = ifaceIp
        }
        if let peerIp = options?[TunnelConstants.peerIPConfigurationKey] as? String
            ?? providerConfiguration?[TunnelConstants.peerIPConfigurationKey] as? String {
            tunnelPeerIP = peerIp
        }
        
        // Discover current physical Wi-Fi (en0) IPv4 address
        updateWiFiIP()
        tunnelLog("Initialized with IfaceIP=\(tunnelIfaceIP), PeerIP=\(tunnelPeerIP), WiFiIP=\(cachedWiFiIPString)")
        
        let ifaceEndpoint = CIDREndpoint(tunnelIfaceIP, defaultPrefix: 24)
        let peerEndpoint = CIDREndpoint(tunnelPeerIP, defaultPrefix: 32)
        
        let ifaceIPv4 = NEIPv4Settings(addresses: [ifaceEndpoint.ip], subnetMasks: [ifaceEndpoint.subnetMask])
        let tunnelDestinationIPv4Routes = [
            NEIPv4Route(destinationAddress: peerEndpoint.ip, subnetMask: peerEndpoint.subnetMask),
            NEIPv4Route(destinationAddress: "10.7.0.0", subnetMask: "255.255.255.0")
        ]
        ifaceIPv4.includedRoutes = tunnelDestinationIPv4Routes
        ifaceIPv4.excludedRoutes = [.default()]

        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: peerEndpoint.ip)
        settings.ipv4Settings = ifaceIPv4
        
        setTunnelNetworkSettings(settings) { error in
            if let error = error {
                tunnelLog("Failed to set settings: \(error.localizedDescription)")
                return completionHandler(error)
            }
            tunnelLog("Tunnel settings applied. Starting hairpin packet loop.")
            self.setPackets()
            completionHandler(nil)
        }
    }
    
    func updateWiFiIP() {
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0 else { return }
        defer { freeifaddrs(ifaddr) }
        
        var ptr = ifaddr
        while let current = ptr {
            defer { ptr = current.pointee.ifa_next }
            let ifa = current.pointee
            guard ifa.ifa_addr.pointee.sa_family == UInt8(AF_INET) else { continue }
            let name = String(cString: ifa.ifa_name)
            if name == "en0" {
                var sin = ifa.ifa_addr.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { $0.pointee }
                cachedWiFiIP = sin.sin_addr.s_addr
                var buffer = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
                inet_ntop(AF_INET, &sin.sin_addr, &buffer, socklen_t(INET_ADDRSTRLEN))
                cachedWiFiIPString = String(cString: buffer)
                tunnelLog("Discovered en0 Wi-Fi IP: \(cachedWiFiIPString) (0x\(String(cachedWiFiIP, radix: 16)))")
                return
            }
        }
    }

    func setPackets() {
        packetFlow.readPackets { [self] packets, protocols in
            var modified = packets
            
            for i in modified.indices where protocols[i].int32Value == AF_INET && modified[i].count >= 40 {
                let packetCount = modified[i].count
                modified[i].withUnsafeMutableBytes { (rawBuffer: UnsafeMutableRawBufferPointer) in
                    guard let base = rawBuffer.baseAddress else { return }
                    
                    let ipPtr = base.assumingMemoryBound(to: UInt8.self)
                    let ipHeaderLen = Int((ipPtr[0] & 0x0F) * 4)
                    let proto = ipPtr[9]
                    
                    guard proto == 6 && packetCount >= ipHeaderLen + 20 else {
                        // Fallback non-TCP: standard swap
                        if packetCount >= 20 {
                            let u32 = base.assumingMemoryBound(to: UInt32.self)
                            let s = u32[3]
                            let d = u32[4]
                            u32[3] = d
                            u32[4] = s
                        }
                        return
                    }
                    
                    // Pointers to IPs (in network order)
                    let u32 = base.assumingMemoryBound(to: UInt32.self)
                    let srcIP = u32[3]
                    let dstIP = u32[4]
                    
                    // TCP header pointers
                    let tcpPtr = base.advanced(by: ipHeaderLen).assumingMemoryBound(to: UInt8.self)
                    let srcPort = (UInt16(tcpPtr[0]) << 8) | UInt16(tcpPtr[1])
                    let dstPort = (UInt16(tcpPtr[2]) << 8) | UInt16(tcpPtr[3])
                    let tcpFlags = tcpPtr[13]
                    
                    let peerIP32 = inet_addr("10.7.0.1")
                    let ifaceIP32 = inet_addr("10.7.1.1")
                    
                    // Direction 1: SideStore -> Dynamic Daemon
                    // Outgoing packet to 10.7.0.1 on a dynamic port (!= 49152)
                    if dstIP == peerIP32 && dstPort != 49152 && self.cachedWiFiIP != 0 {
                        let isSYN = (tcpFlags & 0x02) != 0
                        if isSYN {
                            tunnelLog("HAIRPIN_SYN_INJECTED src=\(self.tunnelPeerIP):\(srcPort) dst=\(self.cachedWiFiIPString):\(dstPort)")
                        }
                        
                        // Rewrite: Source becomes 10.7.0.1, Destination becomes en0 Wi-Fi IP
                        u32[3] = peerIP32
                        u32[4] = self.cachedWiFiIP
                        
                        // Recalculate IPv4 and TCP checksums
                        self.recalculateChecksums(base: base, totalLen: packetCount, ipHeaderLen: ipHeaderLen)
                        return
                    }
                    
                    // Direction 2: Dynamic Daemon -> SideStore
                    // Incoming reply from en0 Wi-Fi IP destined for 10.7.0.1
                    if srcIP == self.cachedWiFiIP && dstIP == peerIP32 {
                        let isSYNACK = (tcpFlags & 0x12) == 0x12
                        if isSYNACK {
                            tunnelLog("HAIRPIN_SYNACK_CAPTURED src=\(self.cachedWiFiIPString):\(srcPort) dst=\(self.tunnelIfaceIP):\(dstPort)")
                        }
                        
                        // Rewrite: Source becomes 10.7.0.1, Destination becomes 10.7.1.1 (SideStore)
                        u32[3] = peerIP32
                        u32[4] = ifaceIP32
                        
                        // Recalculate IPv4 and TCP checksums
                        self.recalculateChecksums(base: base, totalLen: packetCount, ipHeaderLen: ipHeaderLen)
                        return
                    }
                    
                    // Default fallback (e.g. control channel on port 49152): classic swap
                    let s = u32[3]
                    let d = u32[4]
                    u32[3] = d
                    u32[4] = s
                    self.recalculateChecksums(base: base, totalLen: packetCount, ipHeaderLen: ipHeaderLen)
                }
            }
            
            self.packetFlow.writePackets(modified, withProtocols: protocols)
            setPackets()
        }
    }
    
    func recalculateChecksums(base: UnsafeMutableRawPointer, totalLen: Int, ipHeaderLen: Int) {
        let ipU8 = base.assumingMemoryBound(to: UInt8.self)
        let tcpU8 = base.advanced(by: ipHeaderLen).assumingMemoryBound(to: UInt8.self)
        let tcpLen = totalLen - ipHeaderLen
        
        // Zero checksum fields
        ipU8[10] = 0
        ipU8[11] = 0
        tcpU8[16] = 0
        tcpU8[17] = 0
        
        // 1. IP Checksum
        var ipSum: UInt32 = 0
        for i in stride(from: 0, to: ipHeaderLen, by: 2) {
            let w = (UInt32(ipU8[i]) << 8) | UInt32(ipU8[i+1])
            ipSum += w
        }
        while (ipSum >> 16) != 0 {
            ipSum = (ipSum & 0xFFFF) + (ipSum >> 16)
        }
        let ipCsum = UInt16(~ipSum & 0xFFFF)
        ipU8[10] = UInt8(ipCsum >> 8)
        ipU8[11] = UInt8(ipCsum & 0xFF)
        
        // 2. TCP Checksum with Pseudo-header
        var tcpSum: UInt32 = 0
        // Pseudo header: Src IP (4 bytes), Dst IP (4 bytes), Zero (1 byte), Proto 6 (1 byte), TCP Length (2 bytes)
        tcpSum += (UInt32(ipU8[12]) << 8) | UInt32(ipU8[13])
        tcpSum += (UInt32(ipU8[14]) << 8) | UInt32(ipU8[15])
        tcpSum += (UInt32(ipU8[16]) << 8) | UInt32(ipU8[17])
        tcpSum += (UInt32(ipU8[18]) << 8) | UInt32(ipU8[19])
        tcpSum += 6 // Protocol TCP
        tcpSum += UInt32(tcpLen)
        
        // TCP segment (header + payload)
        var idx = 0
        while idx < tcpLen - 1 {
            let w = (UInt32(tcpU8[idx]) << 8) | UInt32(tcpU8[idx+1])
            tcpSum += w
            idx += 2
        }
        if idx < tcpLen {
            tcpSum += (UInt32(tcpU8[idx]) << 8)
        }
        
        while (tcpSum >> 16) != 0 {
            tcpSum = (tcpSum & 0xFFFF) + (tcpSum >> 16)
        }
        let tcpCsum = UInt16(~tcpSum & 0xFFFF)
        tcpU8[16] = UInt8(tcpCsum >> 8)
        tcpU8[17] = UInt8(tcpCsum & 0xFF)
    }
}
