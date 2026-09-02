#!/usr/bin/env python3
"""Patch EMProxy with an IPv4 dynamic-listener hairpin.

The iOS runtime trace proves that RemotePairing control, PairVerify, listener
creation and TLS-PSK all succeed. The remaining failure is caused by dialing the
listener through the phone's own physical address: remotepairingd completes TLS
and then closes before accepting the first CDTunnel application record.

This patch keeps the client socket on the existing 10.7.0.0/24 VPN route but
translates only initiating dynamic-listener flows to the phone's en0 IPv4
address. The listener therefore sees a distinct peer (10.7.0.1), while the
client retains its original 10.7.0.1 tuple. Fixed RemotePairing/Lockdown ports
remain on the proven stock reflection path.
"""

from __future__ import annotations

from pathlib import Path
import sys

MARKER = "[EMP-NAT44] dynamic listener bridge active"


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def verify(source: str) -> None:
    required = [
        MARKER,
        "struct DynamicNat44Flow",
        "nat44_translate_forward",
        "nat44_translate_reverse",
        "nat44_repair_ipv4_tcp_checksums",
        "nat44_en0_ipv4",
        "TX-NAT44-FWD",
        "TX-NAT44-REV",
        "NAT44_DYNAMIC_TCP_MIN",
        "NAT44_CONTROL_PORT",
        "NAT44_LOCKDOWN_PORT",
        "new mapping requires initiating SYN",
        "let mut nat44_flows: Vec<DynamicNat44Flow>",
    ]
    missing = [item for item in required if item not in source]
    if missing:
        die(f"EMProxy NAT44 verification failed; missing: {missing}")

    forbidden = [
        "payload_bytes",
        "hex::encode",
        "packet={:?}",
        "HostPrivateKey",
        "RootPrivateKey",
        "encryption_key=",
    ]
    leaked = [item for item in forbidden if item in source]
    if leaked:
        die(f"EMProxy NAT44 privacy verification failed: {leaked}")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: apply_emproxy_nat44_dynamic.py <em_proxy/src/lib.rs>")

    path = Path(sys.argv[1])
    if not path.exists():
        die(f"missing EMProxy source: {path}")

    source = path.read_text()
    if MARKER in source:
        verify(source)
        print("EMProxy NAT44 dynamic-listener bridge already present and verified")
        return

    for prerequisite in (
        "[EMP-DIAG] packet diagnostic build active",
        "[EMP-NONBLOCK] UDP socket nonblocking mode enabled",
        "[EMP-V13-PAYLOAD]",
    ):
        if prerequisite not in source:
            die(f"EMProxy NAT44 requires prerequisite marker: {prerequisite}")

    helper = r'''
// SideStore v19 dynamic-listener NAT44 bridge.
//
// Runtime evidence establishes this boundary:
//   * 10.7.0.1:49152 PairVerify succeeds.
//   * createListener returns a live high port.
//   * direct same-device en0 connections complete TLS-PSK.
//   * remotepairingd then sends TLS close_notify before processing CDTunnel.
//
// For only high-port listener flows, preserve the client-facing tuple while
// delivering the packet to the local en0 listener from the VPN peer address:
//
//   client view:   10.7.0.10:ephemeral <-> 10.7.0.1:listener
//   listener view: 10.7.0.1:ephemeral  <-> en0-ipv4:listener
//
// This uses the already-working IPv4 WireGuard route and requires no IPv6
// address or AllowedIPs change. Ports 49152 and 62078 never enter this bridge.
#[derive(Clone, Debug)]
struct DynamicNat44Flow {
    client_v4: [u8; 4],
    virtual_v4: [u8; 4],
    physical_v4: [u8; 4],
    client_port: u16,
    service_port: u16,
    last_seen: std::time::Instant,
}

const NAT44_VIRTUAL_V4: [u8; 4] = [10, 7, 0, 1];
const NAT44_DYNAMIC_TCP_MIN: u16 = 49153;
const NAT44_CONTROL_PORT: u16 = 49152;
const NAT44_LOCKDOWN_PORT: u16 = 62078;

fn nat44_ipv4_text(ip: [u8; 4]) -> std::net::Ipv4Addr {
    std::net::Ipv4Addr::new(ip[0], ip[1], ip[2], ip[3])
}

fn nat44_checksum_add(mut sum: u32, bytes: &[u8]) -> u32 {
    let mut chunks = bytes.chunks_exact(2);
    for chunk in &mut chunks {
        sum = sum.wrapping_add(u16::from_be_bytes([chunk[0], chunk[1]]) as u32);
    }
    if let Some(&last) = chunks.remainder().first() {
        sum = sum.wrapping_add((last as u32) << 8);
    }
    sum
}

fn nat44_checksum_finish(mut sum: u32) -> u16 {
    while (sum >> 16) != 0 {
        sum = (sum & 0xffff).wrapping_add(sum >> 16);
    }
    let value = !(sum as u16);
    if value == 0 { 0xffff } else { value }
}

fn nat44_repair_ipv4_tcp_checksums(packet: &mut [u8]) -> bool {
    if packet.len() < 40 || (packet[0] >> 4) != 4 || packet[9] != 6 {
        return false;
    }
    let ihl = ((packet[0] & 0x0f) as usize) * 4;
    if ihl < 20 || packet.len() < ihl + 20 {
        return false;
    }
    let total_len = u16::from_be_bytes([packet[2], packet[3]]) as usize;
    if total_len < ihl + 20 || total_len > packet.len() {
        return false;
    }
    let fragment = u16::from_be_bytes([packet[6], packet[7]]);
    if fragment & 0x3fff != 0 {
        return false;
    }

    packet[10] = 0;
    packet[11] = 0;
    let ip_checksum = nat44_checksum_finish(nat44_checksum_add(0, &packet[..ihl]));
    packet[10..12].copy_from_slice(&ip_checksum.to_be_bytes());

    let tcp_len = total_len - ihl;
    let tcp_checksum_offset = ihl + 16;
    packet[tcp_checksum_offset] = 0;
    packet[tcp_checksum_offset + 1] = 0;

    let mut sum = 0u32;
    sum = nat44_checksum_add(sum, &packet[12..16]);
    sum = nat44_checksum_add(sum, &packet[16..20]);
    sum = nat44_checksum_add(sum, &[0, 6]);
    sum = nat44_checksum_add(sum, &(tcp_len as u16).to_be_bytes());
    sum = nat44_checksum_add(sum, &packet[ihl..total_len]);
    let tcp_checksum = nat44_checksum_finish(sum);
    packet[tcp_checksum_offset..tcp_checksum_offset + 2]
        .copy_from_slice(&tcp_checksum.to_be_bytes());
    true
}

fn nat44_en0_ipv4() -> Option<[u8; 4]> {
    #[cfg(unix)]
    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) != 0 || head.is_null() {
            log_msg(
                2,
                format!(
                    "[EMP-NAT44] getifaddrs failed error={}",
                    std::io::Error::last_os_error()
                ),
            );
            return None;
        }

        let mut en0: Option<[u8; 4]> = None;
        let mut fallback_en: Option<[u8; 4]> = None;
        let mut cursor = head;
        while !cursor.is_null() {
            let ifa = &*cursor;
            if !ifa.ifa_name.is_null() && !ifa.ifa_addr.is_null() {
                let family = (*ifa.ifa_addr).sa_family as i32;
                if family == libc::AF_INET {
                    let name = std::ffi::CStr::from_ptr(ifa.ifa_name).to_string_lossy();
                    let sin = &*(ifa.ifa_addr as *const libc::sockaddr_in);
                    // s_addr is stored in network byte order; to_ne_bytes()
                    // returns the bytes as laid out in sockaddr memory.
                    let ip = sin.sin_addr.s_addr.to_ne_bytes();
                    let unusable = ip[0] == 0
                        || ip[0] == 127
                        || (ip[0] == 169 && ip[1] == 254)
                        || (ip[0] == 10 && ip[1] == 7 && ip[2] == 0);
                    if !unusable {
                        if name == "en0" && en0.is_none() {
                            en0 = Some(ip);
                        } else if name.starts_with("en") && fallback_en.is_none() {
                            fallback_en = Some(ip);
                        }
                    }
                }
            }
            cursor = ifa.ifa_next;
        }
        libc::freeifaddrs(head);

        let selected = en0.or(fallback_en);
        if let Some(ip) = selected {
            log_msg(
                2,
                format!(
                    "[EMP-NAT44] selected physical IPv4={} source=en0/en*",
                    nat44_ipv4_text(ip)
                ),
            );
        }
        selected
    }

    #[cfg(not(unix))]
    {
        None
    }
}

fn nat44_translate_forward(
    packet: &[u8],
    flows: &mut Vec<DynamicNat44Flow>,
) -> Option<Vec<u8>> {
    if packet.len() < 40 || (packet[0] >> 4) != 4 || packet[9] != 6 {
        return None;
    }
    let ihl = ((packet[0] & 0x0f) as usize) * 4;
    if ihl < 20 || packet.len() < ihl + 20 {
        return None;
    }
    let total_len = u16::from_be_bytes([packet[2], packet[3]]) as usize;
    if total_len < ihl + 20 || total_len > packet.len() {
        return None;
    }
    let fragment = u16::from_be_bytes([packet[6], packet[7]]);
    if fragment & 0x3fff != 0 {
        return None;
    }

    let src = [packet[12], packet[13], packet[14], packet[15]];
    let dst = [packet[16], packet[17], packet[18], packet[19]];
    let src_port = u16::from_be_bytes([packet[ihl], packet[ihl + 1]]);
    let dst_port = u16::from_be_bytes([packet[ihl + 2], packet[ihl + 3]]);
    let flags = packet[ihl + 13];
    let client_is_wireguard =
        src[0] == 10 && src[1] == 7 && src[2] == 0 && src != NAT44_VIRTUAL_V4;

    // Fixed-service replies also target ephemeral high ports. Excluding their
    // source ports prevents control/Lockdown traffic from being misclassified.
    if !client_is_wireguard
        || dst != NAT44_VIRTUAL_V4
        || dst_port < NAT44_DYNAMIC_TCP_MIN
        || dst_port == NAT44_LOCKDOWN_PORT
        || src_port == NAT44_CONTROL_PORT
        || src_port == NAT44_LOCKDOWN_PORT
    {
        return None;
    }

    let now = std::time::Instant::now();
    flows.retain(|flow| now.duration_since(flow.last_seen).as_secs() < 120);
    let existing_index = flows.iter().position(|flow| {
        flow.client_v4 == src
            && flow.virtual_v4 == dst
            && flow.client_port == src_port
            && flow.service_port == dst_port
    });

    // new mapping requires initiating SYN; never create state from ACK/FIN/RST.
    if existing_index.is_none() && (flags & 0x02 == 0 || flags & 0x10 != 0) {
        return None;
    }

    let index = if let Some(index) = existing_index {
        flows[index].last_seen = now;
        index
    } else {
        let Some(physical_v4) = nat44_en0_ipv4() else {
            log_msg(
                2,
                "[EMP-NAT44] forward blocked: no usable en0/en* IPv4 address".to_string(),
            );
            return None;
        };
        flows.push(DynamicNat44Flow {
            client_v4: src,
            virtual_v4: dst,
            physical_v4,
            client_port: src_port,
            service_port: dst_port,
            last_seen: now,
        });
        let index = flows.len() - 1;
        log_msg(
            2,
            format!(
                "[EMP-NAT44] FORWARD flow installed client={}:{} virtual={}:{} physical={}:{}",
                nat44_ipv4_text(src),
                src_port,
                nat44_ipv4_text(dst),
                dst_port,
                nat44_ipv4_text(physical_v4),
                dst_port
            ),
        );
        index
    };
    let flow = flows[index].clone();

    let mut out = packet[..total_len].to_vec();
    out[12..16].copy_from_slice(&flow.virtual_v4);
    out[16..20].copy_from_slice(&flow.physical_v4);
    if !nat44_repair_ipv4_tcp_checksums(&mut out) {
        log_msg(2, "[EMP-NAT44] forward checksum repair failed".to_string());
        return None;
    }
    Some(out)
}

fn nat44_translate_reverse(
    packet: &[u8],
    flows: &mut Vec<DynamicNat44Flow>,
) -> Option<Vec<u8>> {
    if packet.len() < 40 || (packet[0] >> 4) != 4 || packet[9] != 6 {
        return None;
    }
    let ihl = ((packet[0] & 0x0f) as usize) * 4;
    if ihl < 20 || packet.len() < ihl + 20 {
        return None;
    }
    let total_len = u16::from_be_bytes([packet[2], packet[3]]) as usize;
    if total_len < ihl + 20 || total_len > packet.len() {
        return None;
    }
    let fragment = u16::from_be_bytes([packet[6], packet[7]]);
    if fragment & 0x3fff != 0 {
        return None;
    }

    let src = [packet[12], packet[13], packet[14], packet[15]];
    let dst = [packet[16], packet[17], packet[18], packet[19]];
    let src_port = u16::from_be_bytes([packet[ihl], packet[ihl + 1]]);
    let dst_port = u16::from_be_bytes([packet[ihl + 2], packet[ihl + 3]]);

    let now = std::time::Instant::now();
    flows.retain(|flow| now.duration_since(flow.last_seen).as_secs() < 120);
    let index = flows.iter().position(|flow| {
        flow.physical_v4 == src
            && flow.virtual_v4 == dst
            && flow.service_port == src_port
            && flow.client_port == dst_port
    })?;
    flows[index].last_seen = now;
    let flow = flows[index].clone();

    let mut out = packet[..total_len].to_vec();
    out[12..16].copy_from_slice(&flow.virtual_v4);
    out[16..20].copy_from_slice(&flow.client_v4);
    if !nat44_repair_ipv4_tcp_checksums(&mut out) {
        log_msg(2, "[EMP-NAT44] reverse checksum repair failed".to_string());
        return None;
    }
    Some(out)
}

'''

    helper_anchor = "fn log_ipv4_tcp(stage: &str, packet: &[u8]) {"
    if source.count(helper_anchor) != 1:
        die(f"EMProxy NAT44 helper anchor count={source.count(helper_anchor)}")
    source = source.replace(helper_anchor, helper + helper_anchor, 1)

    state_old = '''        let mut last_endpoint: Option<std::net::SocketAddr> = None;
        let mut udp_rx_packets: u64 = 0;
        loop {'''
    state_new = '''        let mut last_endpoint: Option<std::net::SocketAddr> = None;
        let mut udp_rx_packets: u64 = 0;
        let mut nat44_flows: Vec<DynamicNat44Flow> = Vec::new();
        log_msg(
            2,
            "[EMP-NAT44] dynamic listener bridge active; virtual=10.7.0.1 physical=en0-ipv4 dynamic_min=49153 fixed_ports=49152,62078".to_string(),
        );
        loop {'''
    if source.count(state_old) != 1:
        die(f"EMProxy NAT44 worker-state anchor count={source.count(state_old)}")
    source = source.replace(state_old, state_new, 1)

    v4_old = '''                        boringtun::noise::TunnResult::WriteToTunnelV4(b, _addr) => {
                            log_ipv4_tcp("RX-DECAP", b);

                            // Reflect the packet back to iOS by swapping IPv4 source/destination.
                            b.swap(12, 16);
                            b.swap(13, 17);
                            b.swap(14, 18);
                            b.swap(15, 19);

                            log_ipv4_tcp("TX-REFLECT", b);

                            let mut buf = [0_u8; 2048];
                            match tun.encapsulate(b, &mut buf) {
                                boringtun::noise::TunnResult::WriteToNetwork(encapsulated) => {
                                    if let Err(e) = socket.send_to(encapsulated, endpoint) {
                                        log_msg(3, format!("[EMP-DIAG] Error sending reflected UDP packet: {:?}", e));
                                    }
                                }
                                boringtun::noise::TunnResult::Err(e) => {
                                    log_msg(2, format!("[EMP-DIAG] encapsulate reflected IPv4 FAILED: {:?}", e));
                                }
                                boringtun::noise::TunnResult::Done => {
                                    log_msg(2, "[EMP-DIAG] encapsulate reflected IPv4 returned Done unexpectedly".to_string());
                                }
                                boringtun::noise::TunnResult::WriteToTunnelV4(_, _) => {
                                    log_msg(2, "[EMP-DIAG] encapsulate reflected IPv4 returned WriteToTunnelV4 unexpectedly".to_string());
                                }
                                boringtun::noise::TunnResult::WriteToTunnelV6(_, _) => {
                                    log_msg(2, "[EMP-DIAG] encapsulate reflected IPv4 returned WriteToTunnelV6 unexpectedly".to_string());
                                }
                            }
                        }'''

    v4_new = '''                        boringtun::noise::TunnResult::WriteToTunnelV4(b, _addr) => {
                            log_ipv4_tcp("RX-DECAP", b);

                            let translated = nat44_translate_reverse(b, &mut nat44_flows)
                                .or_else(|| nat44_translate_forward(b, &mut nat44_flows));
                            if translated.is_none() {
                                // Proven fixed-port path: ordinary IPv4 reflection.
                                b.swap(12, 16);
                                b.swap(13, 17);
                                b.swap(14, 18);
                                b.swap(15, 19);
                                log_ipv4_tcp("TX-REFLECT", b);
                            }

                            let outbound: &[u8] = match translated.as_ref() {
                                Some(packet) => {
                                    let reverse = packet[16] == 10
                                        && packet[17] == 7
                                        && packet[18] == 0
                                        && packet[19] != 1;
                                    let stage = if reverse { "TX-NAT44-REV" } else { "TX-NAT44-FWD" };
                                    log_ipv4_tcp(stage, packet);
                                    packet.as_slice()
                                }
                                None => b,
                            };

                            let mut encrypted_buf = [0_u8; 4096];
                            match tun.encapsulate(outbound, &mut encrypted_buf) {
                                boringtun::noise::TunnResult::WriteToNetwork(encapsulated) => {
                                    if let Err(e) = socket.send_to(encapsulated, endpoint) {
                                        log_msg(3, format!("[EMP-NAT44] send translated/reflected packet failed: {:?}", e));
                                    }
                                }
                                boringtun::noise::TunnResult::Err(e) => {
                                    log_msg(2, format!("[EMP-NAT44] encapsulate outbound IPv4 failed: {:?}", e));
                                }
                                boringtun::noise::TunnResult::Done => {
                                    log_msg(2, "[EMP-NAT44] encapsulate outbound IPv4 returned Done".to_string());
                                }
                                boringtun::noise::TunnResult::WriteToTunnelV4(_, _) => {
                                    log_msg(2, "[EMP-NAT44] encapsulate outbound IPv4 returned WriteToTunnelV4".to_string());
                                }
                                boringtun::noise::TunnResult::WriteToTunnelV6(_, _) => {
                                    log_msg(2, "[EMP-NAT44] encapsulate outbound IPv4 returned WriteToTunnelV6".to_string());
                                }
                            }
                        }'''

    if source.count(v4_old) != 1:
        die(f"EMProxy NAT44 IPv4 worker anchor count={source.count(v4_old)}")
    source = source.replace(v4_old, v4_new, 1)

    path.write_text(source)
    verify(path.read_text())
    print("EMProxy NAT44 dynamic-listener bridge applied and verified")


if __name__ == "__main__":
    main()
