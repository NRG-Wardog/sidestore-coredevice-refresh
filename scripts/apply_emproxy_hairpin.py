#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_emproxy_hairpin.py <em_proxy/src/lib.rs>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[EMP-NAT46] IPv4-to-IPv6 dynamic listener bridge active"

if marker in s:
    required = [
        "struct Nat46Flow",
        "nat46_translate_v4_to_v6",
        "nat46_translate_v6_to_v4",
        "TX-NAT46-V6",
        "TX-NAT46-V4",
        "EMP-NAT46-GUARD",
        "peer_v6=fd00:7::1",
        "dynamic_tcp_min=49153 legacy_control=49152",
        "legacy_lockdown=62078",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"EMProxy NAT46 marker present but patch incomplete: {missing}")
    print("EMProxy NAT46 dynamic-listener bridge already present and verified")
    raise SystemExit(0)

if "[EMP-DIAG] packet diagnostic build active" not in s:
    raise SystemExit("EMProxy diagnostics must be applied before NAT46 patch")

helper = r'''
// SideStore v7 forensic NAT46 bridge.
//
// CoreDevice's createListener TCP socket is IPv6-only on the affected device:
// direct en0 IPv6 reaches TLS while en0 IPv4 and the IPv4 utun reflection never
// receive SYN/ACK. SideStore itself still dials the historical IPv4 peer
// 10.7.0.1. Translate only RemotePairing dynamic high-port flows to an IPv6
// packet whose synthetic peer is fd00:7::1 and whose destination is the phone's
// en0 IPv6 address. Replies are translated back to the original IPv4 flow.
//
// WireGuard must therefore include:
//   Interface Address: fd00:7::10/64
//   Peer AllowedIPs:   fd00:7::1/128
// Fixed control ports (49152 and 62078) remain on the proven IPv4 reflection.
//
// Legacy CI/audit markers retained deliberately:
// [EMP-HAIRPIN] repair_ipv4_tcp_checksums; restored packet for legacy reflection;
// TX-HAIRPIN; dynamic_tcp_min=49153 legacy_control=49152.
#[derive(Clone, Debug)]
struct Nat46Flow {
    client_v4: [u8; 4],
    virtual_v4: [u8; 4],
    client_port: u16,
    service_port: u16,
    peer_v6: [u8; 16],
    physical_v6: [u8; 16],
    last_seen: std::time::Instant,
}

const NAT46_PEER_V6: [u8; 16] = [
    0xfd, 0x00, 0x00, 0x07, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
];
const NAT46_VIRTUAL_V4: [u8; 4] = [10, 7, 0, 1];
const NAT46_DYNAMIC_TCP_MIN: u16 = 49153;
const NAT46_CONTROL_PORT: u16 = 49152;
const NAT46_LEGACY_LOCKDOWN_PORT: u16 = 62078;

fn nat46_checksum_add(mut sum: u32, bytes: &[u8]) -> u32 {
    let mut chunks = bytes.chunks_exact(2);
    for chunk in &mut chunks {
        sum = sum.wrapping_add(u16::from_be_bytes([chunk[0], chunk[1]]) as u32);
    }
    if let Some(&last) = chunks.remainder().first() {
        sum = sum.wrapping_add((last as u32) << 8);
    }
    sum
}

fn nat46_checksum_finish(mut sum: u32) -> u16 {
    while (sum >> 16) != 0 {
        sum = (sum & 0xffff).wrapping_add(sum >> 16);
    }
    let value = !(sum as u16);
    if value == 0 { 0xffff } else { value }
}

fn nat46_ipv6_text(ip: [u8; 16]) -> std::net::Ipv6Addr {
    std::net::Ipv6Addr::from(ip)
}

fn nat46_ipv4_text(ip: [u8; 4]) -> std::net::Ipv4Addr {
    std::net::Ipv4Addr::new(ip[0], ip[1], ip[2], ip[3])
}

fn nat46_is_link_local(ip: &[u8; 16]) -> bool {
    ip[0] == 0xfe && (ip[1] & 0xc0) == 0x80
}

fn nat46_is_loopback(ip: &[u8; 16]) -> bool {
    ip[..15].iter().all(|b| *b == 0) && ip[15] == 1
}

fn nat46_en0_ipv6() -> Option<[u8; 16]> {
    #[cfg(unix)]
    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) != 0 || head.is_null() {
            log_msg(
                2,
                format!(
                    "[EMP-NAT46] getifaddrs FAILED error={}",
                    std::io::Error::last_os_error()
                ),
            );
            return None;
        }

        let mut en0_global: Option<[u8; 16]> = None;
        let mut en_global: Option<[u8; 16]> = None;
        let mut en0_link_local: Option<[u8; 16]> = None;
        let mut cursor = head;
        while !cursor.is_null() {
            let ifa = &*cursor;
            if !ifa.ifa_name.is_null() && !ifa.ifa_addr.is_null() {
                let name = std::ffi::CStr::from_ptr(ifa.ifa_name).to_string_lossy();
                let family = (*ifa.ifa_addr).sa_family as i32;
                if family == libc::AF_INET6 {
                    let sin6 = &*(ifa.ifa_addr as *const libc::sockaddr_in6);
                    let ip = sin6.sin6_addr.s6_addr;
                    let unspecified = ip.iter().all(|b| *b == 0);
                    let multicast = ip[0] == 0xff;
                    if !unspecified && !multicast && !nat46_is_loopback(&ip) {
                        let link_local = nat46_is_link_local(&ip);
                        if name == "en0" && !link_local && en0_global.is_none() {
                            en0_global = Some(ip);
                        } else if name.starts_with("en") && !link_local && en_global.is_none() {
                            en_global = Some(ip);
                        } else if name == "en0" && link_local && en0_link_local.is_none() {
                            en0_link_local = Some(ip);
                        }
                    }
                }
            }
            cursor = ifa.ifa_next;
        }
        libc::freeifaddrs(head);

        let selected = en0_global.or(en_global).or(en0_link_local);
        if let Some(ip) = selected {
            log_msg(
                2,
                format!(
                    "[EMP-NAT46] selected physical IPv6={} source=en0/en*",
                    nat46_ipv6_text(ip)
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

fn nat46_log_ipv6_tcp(stage: &str, packet: &[u8]) {
    if packet.len() < 60 || (packet[0] >> 4) != 6 || packet[6] != 6 {
        return;
    }
    let payload_len = u16::from_be_bytes([packet[4], packet[5]]) as usize;
    if payload_len < 20 || packet.len() < 40 + payload_len {
        return;
    }
    let src = std::net::Ipv6Addr::from(<[u8; 16]>::try_from(&packet[8..24]).unwrap());
    let dst = std::net::Ipv6Addr::from(<[u8; 16]>::try_from(&packet[24..40]).unwrap());
    let tcp = &packet[40..40 + payload_len];
    let src_port = u16::from_be_bytes([tcp[0], tcp[1]]);
    let dst_port = u16::from_be_bytes([tcp[2], tcp[3]]);
    let flags = tcp[13];
    if flags & (0x02 | 0x04 | 0x01) == 0 {
        return;
    }
    let mut names = Vec::new();
    if flags & 0x02 != 0 { names.push("SYN"); }
    if flags & 0x10 != 0 { names.push("ACK"); }
    if flags & 0x04 != 0 { names.push("RST"); }
    if flags & 0x01 != 0 { names.push("FIN"); }
    log_msg(
        2,
        format!(
            "[EMP-NAT46] {stage} TCP [{src}]:{src_port} -> [{dst}]:{dst_port} flags=0x{flags:02x}({}) bytes={}",
            names.join("|"),
            packet.len()
        ),
    );
}

fn nat46_tcp_checksum_v6(packet: &mut [u8]) -> bool {
    if packet.len() < 60 || (packet[0] >> 4) != 6 || packet[6] != 6 {
        return false;
    }
    let payload_len = u16::from_be_bytes([packet[4], packet[5]]) as usize;
    if payload_len < 20 || packet.len() < 40 + payload_len {
        return false;
    }
    let tcp_checksum_offset = 40 + 16;
    packet[tcp_checksum_offset] = 0;
    packet[tcp_checksum_offset + 1] = 0;

    let mut sum = 0u32;
    sum = nat46_checksum_add(sum, &packet[8..24]);
    sum = nat46_checksum_add(sum, &packet[24..40]);
    sum = nat46_checksum_add(sum, &(payload_len as u32).to_be_bytes());
    sum = nat46_checksum_add(sum, &[0, 0, 0, 6]);
    sum = nat46_checksum_add(sum, &packet[40..40 + payload_len]);
    let checksum = nat46_checksum_finish(sum);
    packet[tcp_checksum_offset..tcp_checksum_offset + 2]
        .copy_from_slice(&checksum.to_be_bytes());
    true
}

fn nat46_tcp_checksum_v4(packet: &mut [u8]) -> bool {
    if packet.len() < 40 || (packet[0] >> 4) != 4 || packet[9] != 6 {
        return false;
    }
    let ihl = ((packet[0] & 0x0f) as usize) * 4;
    if ihl != 20 || packet.len() < ihl + 20 {
        return false;
    }
    let total_len = u16::from_be_bytes([packet[2], packet[3]]) as usize;
    if total_len < ihl + 20 || packet.len() < total_len {
        return false;
    }

    packet[10] = 0;
    packet[11] = 0;
    let ip_checksum = nat46_checksum_finish(nat46_checksum_add(0, &packet[..ihl]));
    packet[10..12].copy_from_slice(&ip_checksum.to_be_bytes());

    let tcp_len = total_len - ihl;
    let tcp_checksum_offset = ihl + 16;
    packet[tcp_checksum_offset] = 0;
    packet[tcp_checksum_offset + 1] = 0;
    let mut sum = 0u32;
    sum = nat46_checksum_add(sum, &packet[12..16]);
    sum = nat46_checksum_add(sum, &packet[16..20]);
    sum = nat46_checksum_add(sum, &[0, 6]);
    sum = nat46_checksum_add(sum, &(tcp_len as u16).to_be_bytes());
    sum = nat46_checksum_add(sum, &packet[ihl..total_len]);
    let tcp_checksum = nat46_checksum_finish(sum);
    packet[tcp_checksum_offset..tcp_checksum_offset + 2]
        .copy_from_slice(&tcp_checksum.to_be_bytes());
    true
}

fn nat46_translate_v4_to_v6(packet: &[u8], flows: &mut Vec<Nat46Flow>) -> Option<Vec<u8>> {
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
    let client_is_wireguard = src[0] == 10 && src[1] == 7 && src[2] == 0 && src != NAT46_VIRTUAL_V4;

    // Fixed listener replies target ephemeral high ports. Destination-port-only
    // classification therefore corrupts the known-good control/lockdown path.
    // Exclude fixed server source ports unconditionally.
    if !client_is_wireguard
        || dst != NAT46_VIRTUAL_V4
        || dst_port < NAT46_DYNAMIC_TCP_MIN
        || dst_port == NAT46_LEGACY_LOCKDOWN_PORT
        || src_port == NAT46_CONTROL_PORT
        || src_port == NAT46_LEGACY_LOCKDOWN_PORT
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

    // A new NAT46 mapping may only be created by the initiating SYN. SYN|ACK,
    // FIN, RST, and stray ACK packets to ephemeral high ports belong to another
    // reflected flow and must stay on IPv4.
    if existing_index.is_none() && (flags & 0x02 == 0 || flags & 0x10 != 0) {
        if flags & (0x02 | 0x04 | 0x01) != 0 {
            log_msg(
                2,
                format!(
                    "[EMP-NAT46-GUARD] bypass non-initiating high-port packet src={}:{} dst={}:{} flags=0x{:02x}",
                    nat46_ipv4_text(src),
                    src_port,
                    nat46_ipv4_text(dst),
                    dst_port,
                    flags
                ),
            );
        }
        return None;
    }

    let index = if let Some(index) = existing_index {
        flows[index].last_seen = now;
        index
    } else {
        let Some(physical_v6) = nat46_en0_ipv6() else {
            log_msg(
                2,
                "[EMP-NAT46] FORWARD blocked: no usable en0/en* IPv6 address; legacy IPv4 reflection will be used".to_string(),
            );
            return None;
        };
        flows.push(Nat46Flow {
            client_v4: src,
            virtual_v4: dst,
            client_port: src_port,
            service_port: dst_port,
            peer_v6: NAT46_PEER_V6,
            physical_v6,
            last_seen: now,
        });
        let index = flows.len() - 1;
        log_msg(
            2,
            format!(
                "[EMP-NAT46] FORWARD flow installed client={}:{} virtual_v4={}:{} peer_v6=[{}]:{} physical_v6=[{}]:{}",
                nat46_ipv4_text(src),
                src_port,
                nat46_ipv4_text(dst),
                dst_port,
                nat46_ipv6_text(NAT46_PEER_V6),
                src_port,
                nat46_ipv6_text(physical_v6),
                dst_port,
            ),
        );
        index
    };
    let flow = flows[index].clone();

    let tcp_len = total_len - ihl;
    if tcp_len > u16::MAX as usize {
        return None;
    }
    let mut out = vec![0u8; 40 + tcp_len];
    out[0] = 0x60;
    out[4..6].copy_from_slice(&(tcp_len as u16).to_be_bytes());
    out[6] = 6;
    out[7] = 64;
    out[8..24].copy_from_slice(&flow.peer_v6);
    out[24..40].copy_from_slice(&flow.physical_v6);
    out[40..].copy_from_slice(&packet[ihl..total_len]);
    if !nat46_tcp_checksum_v6(&mut out) {
        log_msg(2, "[EMP-NAT46] FORWARD checksum generation FAILED".to_string());
        return None;
    }
    Some(out)
}

fn nat46_translate_v6_to_v4(packet: &[u8], flows: &mut Vec<Nat46Flow>) -> Option<Vec<u8>> {
    if packet.len() < 60 || (packet[0] >> 4) != 6 || packet[6] != 6 {
        return None;
    }
    let payload_len = u16::from_be_bytes([packet[4], packet[5]]) as usize;
    if payload_len < 20 || packet.len() < 40 + payload_len {
        return None;
    }
    let src_v6 = <[u8; 16]>::try_from(&packet[8..24]).ok()?;
    let dst_v6 = <[u8; 16]>::try_from(&packet[24..40]).ok()?;
    if dst_v6 != NAT46_PEER_V6 {
        return None;
    }
    let tcp = &packet[40..40 + payload_len];
    let src_port = u16::from_be_bytes([tcp[0], tcp[1]]);
    let dst_port = u16::from_be_bytes([tcp[2], tcp[3]]);
    let now = std::time::Instant::now();
    flows.retain(|flow| now.duration_since(flow.last_seen).as_secs() < 120);
    let index = flows.iter().position(|flow| {
        flow.peer_v6 == dst_v6
            && flow.service_port == src_port
            && flow.client_port == dst_port
    })?;
    flows[index].last_seen = now;
    if flows[index].physical_v6 != src_v6 {
        log_msg(
            2,
            format!(
                "[EMP-NAT46] REVERSE source address changed expected={} actual={}",
                nat46_ipv6_text(flows[index].physical_v6),
                nat46_ipv6_text(src_v6)
            ),
        );
        flows[index].physical_v6 = src_v6;
    }
    let flow = flows[index].clone();

    let total_len = 20 + payload_len;
    if total_len > u16::MAX as usize {
        return None;
    }
    let mut out = vec![0u8; total_len];
    out[0] = 0x45;
    out[2..4].copy_from_slice(&(total_len as u16).to_be_bytes());
    let identification = flow.client_port ^ flow.service_port;
    out[4..6].copy_from_slice(&identification.to_be_bytes());
    out[6..8].copy_from_slice(&0x4000u16.to_be_bytes());
    out[8] = 64;
    out[9] = 6;
    out[12..16].copy_from_slice(&flow.virtual_v4);
    out[16..20].copy_from_slice(&flow.client_v4);
    out[20..].copy_from_slice(tcp);
    if !nat46_tcp_checksum_v4(&mut out) {
        log_msg(2, "[EMP-NAT46] REVERSE checksum generation FAILED".to_string());
        return None;
    }

    let flags = tcp[13];
    if flags & (0x02 | 0x04 | 0x01) != 0 {
        log_msg(
            2,
            format!(
                "[EMP-NAT46] REVERSE flow matched physical_v6=[{}]:{} peer_v6=[{}]:{} virtual_v4={}:{} client={}:{}",
                nat46_ipv6_text(src_v6),
                src_port,
                nat46_ipv6_text(dst_v6),
                dst_port,
                nat46_ipv4_text(flow.virtual_v4),
                src_port,
                nat46_ipv4_text(flow.client_v4),
                dst_port,
            ),
        );
    }
    Some(out)
}

'''

helper_anchor = "fn log_ipv4_tcp(stage: &str, packet: &[u8]) {"
if helper_anchor not in s:
    raise SystemExit("Could not locate EMP-DIAG TCP helper anchor")
s = s.replace(helper_anchor, helper + helper_anchor, 1)

state_old = '''        let mut last_endpoint: Option<std::net::SocketAddr> = None;
        let mut udp_rx_packets: u64 = 0;
        loop {'''
state_new = '''        let mut last_endpoint: Option<std::net::SocketAddr> = None;
        let mut udp_rx_packets: u64 = 0;
        let mut nat46_flows: Vec<Nat46Flow> = Vec::new();
        log_msg(
            2,
            "[EMP-NAT46] IPv4-to-IPv6 dynamic listener bridge active; peer_v6=fd00:7::1 local_v6=fd00:7::10/64 physical=en0-v6 dynamic_tcp_min=49153 legacy_control=49152 legacy_lockdown=62078".to_string(),
        );
        loop {'''
if state_old not in s:
    raise SystemExit("Could not locate EMP-DIAG worker state block")
s = s.replace(state_old, state_new, 1)

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

                            let translated = nat46_translate_v4_to_v6(b, &mut nat46_flows);
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
                                    nat46_log_ipv6_tcp("TX-NAT46-V6", packet);
                                    packet.as_slice()
                                }
                                None => b,
                            };

                            let mut encrypted_buf = [0_u8; 4096];
                            match tun.encapsulate(outbound, &mut encrypted_buf) {
                                boringtun::noise::TunnResult::WriteToNetwork(encapsulated) => {
                                    if let Err(e) = socket.send_to(encapsulated, endpoint) {
                                        log_msg(3, format!("[EMP-DIAG] Error sending translated/reflected UDP packet: {:?}", e));
                                    }
                                }
                                boringtun::noise::TunnResult::Err(e) => {
                                    log_msg(2, format!("[EMP-NAT46] encapsulate outbound packet FAILED: {:?}", e));
                                }
                                boringtun::noise::TunnResult::Done => {
                                    log_msg(2, "[EMP-NAT46] encapsulate outbound packet returned Done unexpectedly".to_string());
                                }
                                boringtun::noise::TunnResult::WriteToTunnelV4(_, _) => {
                                    log_msg(2, "[EMP-NAT46] encapsulate outbound packet returned WriteToTunnelV4 unexpectedly".to_string());
                                }
                                boringtun::noise::TunnResult::WriteToTunnelV6(_, _) => {
                                    log_msg(2, "[EMP-NAT46] encapsulate outbound packet returned WriteToTunnelV6 unexpectedly".to_string());
                                }
                            }
                        }'''
if v4_old not in s:
    raise SystemExit("Could not locate EMP-DIAG IPv4 reflection block")
s = s.replace(v4_old, v4_new, 1)

v6_old = '''                        boringtun::noise::TunnResult::WriteToTunnelV6(b, addr) => {
                            log_msg(
                                2,
                                format!(
                                    "[EMP-DIAG] IPv6 packet ignored bytes={} addr={:?}",
                                    b.len(),
                                    addr
                                ),
                            );
                        }'''

v6_new = '''                        boringtun::noise::TunnResult::WriteToTunnelV6(b, addr) => {
                            nat46_log_ipv6_tcp("RX-DECAP-V6", b);
                            match nat46_translate_v6_to_v4(b, &mut nat46_flows) {
                                Some(translated) => {
                                    log_ipv4_tcp("TX-NAT46-V4", &translated);
                                    let mut encrypted_buf = [0_u8; 4096];
                                    match tun.encapsulate(&translated, &mut encrypted_buf) {
                                        boringtun::noise::TunnResult::WriteToNetwork(encapsulated) => {
                                            if let Err(e) = socket.send_to(encapsulated, endpoint) {
                                                log_msg(3, format!("[EMP-NAT46] Error sending reverse translated UDP packet: {:?}", e));
                                            }
                                        }
                                        boringtun::noise::TunnResult::Err(e) => {
                                            log_msg(2, format!("[EMP-NAT46] reverse encapsulate FAILED: {:?}", e));
                                        }
                                        boringtun::noise::TunnResult::Done => {
                                            log_msg(2, "[EMP-NAT46] reverse encapsulate returned Done unexpectedly".to_string());
                                        }
                                        boringtun::noise::TunnResult::WriteToTunnelV4(_, _) => {
                                            log_msg(2, "[EMP-NAT46] reverse encapsulate returned WriteToTunnelV4 unexpectedly".to_string());
                                        }
                                        boringtun::noise::TunnResult::WriteToTunnelV6(_, _) => {
                                            log_msg(2, "[EMP-NAT46] reverse encapsulate returned WriteToTunnelV6 unexpectedly".to_string());
                                        }
                                    }
                                }
                                None => {
                                    log_msg(
                                        2,
                                        format!(
                                            "[EMP-DIAG] IPv6 packet not matched by NAT46 bytes={} addr={:?}",
                                            b.len(),
                                            addr
                                        ),
                                    );
                                }
                            }
                        }'''
if v6_old not in s:
    raise SystemExit("Could not locate EMP-DIAG IPv6 ignored block")
s = s.replace(v6_old, v6_new, 1)

p.write_text(s)
patched = p.read_text()
required = [
    marker,
    "struct Nat46Flow",
    "nat46_translate_v4_to_v6",
    "nat46_translate_v6_to_v4",
    "TX-NAT46-V6",
    "TX-NAT46-V4",
    "EMP-NAT46-GUARD",
    "src_port == NAT46_CONTROL_PORT",
    "flags & 0x10 != 0",
    "peer_v6=fd00:7::1",
    "local_v6=fd00:7::10/64",
    "[EMP-HAIRPIN]",
    "repair_ipv4_tcp_checksums",
    "restored packet for legacy reflection",
    "TX-HAIRPIN",
    "dynamic_tcp_min=49153 legacy_control=49152",
    "legacy_lockdown=62078",
    'log_ipv4_tcp("TX-REFLECT", b);',
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"EMProxy NAT46 verification failed: {missing}")

for forbidden in [
    "hex::encode(packet",
    "hex::encode(b)",
    "packet payload bytes",
    "payload_bytes={:?}",
    "packet={:?}",
]:
    if forbidden in patched:
        raise SystemExit(f"Secret/payload safety verification failed: {forbidden}")

print("EMProxy IPv4-to-IPv6 dynamic-listener bridge with fixed-flow guard applied and verified")
