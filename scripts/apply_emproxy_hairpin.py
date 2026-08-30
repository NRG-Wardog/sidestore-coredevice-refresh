#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_emproxy_hairpin.py <em_proxy/src/lib.rs>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[EMP-HAIRPIN] physical-listener hairpin active"

if marker in s:
    required = [
        "hairpin_translate_ipv4_tcp",
        "repair_ipv4_tcp_checksums",
        "hairpin_en0_ipv4",
        "TX-HAIRPIN",
        "FORWARD flow installed",
        "REVERSE flow matched",
        "src_port != legacy_control_port",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"EMProxy hairpin marker present but patch incomplete: {missing}")
    print("EMProxy physical-listener hairpin already present and verified")
    raise SystemExit(0)

if "[EMP-DIAG] packet diagnostic build active" not in s:
    raise SystemExit("EMProxy diagnostics must be applied before hairpin patch")

helper = r'''
#[derive(Clone, Debug)]
struct HairpinFlow {
    client_ip: [u8; 4],
    virtual_ip: [u8; 4],
    physical_ip: [u8; 4],
    client_port: u16,
    service_port: u16,
    last_seen: std::time::Instant,
}

fn checksum_add(mut sum: u32, bytes: &[u8]) -> u32 {
    let mut chunks = bytes.chunks_exact(2);
    for chunk in &mut chunks {
        sum = sum.wrapping_add(u16::from_be_bytes([chunk[0], chunk[1]]) as u32);
    }
    if let Some(&last) = chunks.remainder().first() {
        sum = sum.wrapping_add((last as u32) << 8);
    }
    sum
}

fn checksum_finish(mut sum: u32) -> u16 {
    while (sum >> 16) != 0 {
        sum = (sum & 0xffff) + (sum >> 16);
    }
    !(sum as u16)
}

fn repair_ipv4_tcp_checksums(packet: &mut [u8]) -> bool {
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

    // Hairpin only complete, unfragmented TCP packets. DF is fine; reject MF or
    // any non-zero fragment offset so a rewritten transport checksum is always valid.
    let frag = u16::from_be_bytes([packet[6], packet[7]]);
    if (frag & 0x3fff) != 0 {
        return false;
    }

    packet[10] = 0;
    packet[11] = 0;
    let ip_sum = checksum_finish(checksum_add(0, &packet[..ihl]));
    packet[10..12].copy_from_slice(&ip_sum.to_be_bytes());

    let tcp_len = total_len - ihl;
    let tcp_checksum_offset = ihl + 16;
    packet[tcp_checksum_offset] = 0;
    packet[tcp_checksum_offset + 1] = 0;

    let src = [packet[12], packet[13], packet[14], packet[15]];
    let dst = [packet[16], packet[17], packet[18], packet[19]];
    let mut sum = 0u32;
    sum = checksum_add(sum, &src);
    sum = checksum_add(sum, &dst);
    sum = sum.wrapping_add(packet[9] as u32); // zero + protocol in pseudo-header
    sum = sum.wrapping_add(tcp_len as u32);
    sum = checksum_add(sum, &packet[ihl..total_len]);
    let tcp_sum = checksum_finish(sum);
    packet[tcp_checksum_offset..tcp_checksum_offset + 2]
        .copy_from_slice(&tcp_sum.to_be_bytes());
    true
}

fn hairpin_en0_ipv4() -> Option<[u8; 4]> {
    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) != 0 || head.is_null() {
            return None;
        }

        let mut cursor = head;
        let mut preferred: Option<[u8; 4]> = None;
        let mut fallback: Option<[u8; 4]> = None;
        while !cursor.is_null() {
            let ifa = &*cursor;
            if !ifa.ifa_name.is_null() && !ifa.ifa_addr.is_null() {
                let name = std::ffi::CStr::from_ptr(ifa.ifa_name).to_string_lossy();
                if (*ifa.ifa_addr).sa_family as i32 == libc::AF_INET {
                    let sin = &*(ifa.ifa_addr as *const libc::sockaddr_in);
                    // s_addr is network-order storage; native bytes are the actual
                    // on-wire IPv4 octets on iOS/macOS.
                    let octets = sin.sin_addr.s_addr.to_ne_bytes();
                    let usable = octets != [0, 0, 0, 0] && octets[0] != 127;
                    if usable && name == "en0" {
                        preferred = Some(octets);
                        break;
                    }
                    if usable && name.starts_with("en") && fallback.is_none() {
                        fallback = Some(octets);
                    }
                }
            }
            cursor = ifa.ifa_next;
        }
        libc::freeifaddrs(head);
        preferred.or(fallback)
    }
}

fn ip4_text(ip: [u8; 4]) -> std::net::Ipv4Addr {
    std::net::Ipv4Addr::new(ip[0], ip[1], ip[2], ip[3])
}

fn hairpin_translate_ipv4_tcp(packet: &mut [u8], flows: &mut Vec<HairpinFlow>) -> bool {
    if packet.len() < 40 || (packet[0] >> 4) != 4 || packet[9] != 6 {
        return false;
    }
    let ihl = ((packet[0] & 0x0f) as usize) * 4;
    if ihl < 20 || packet.len() < ihl + 20 {
        return false;
    }

    let src = [packet[12], packet[13], packet[14], packet[15]];
    let dst = [packet[16], packet[17], packet[18], packet[19]];
    let src_port = u16::from_be_bytes([packet[ihl], packet[ihl + 1]]);
    let dst_port = u16::from_be_bytes([packet[ihl + 2], packet[ihl + 3]]);
    let flags = packet[ihl + 13];
    let now = std::time::Instant::now();
    flows.retain(|flow| now.duration_since(flow.last_seen).as_secs() < 120);

    // Reverse leg arrives from the physical listener and is addressed back to the
    // virtual WireGuard peer. Match it before forward detection because the client
    // source port is also normally in the ephemeral range.
    if let Some(index) = flows.iter().position(|flow| {
        src == flow.physical_ip
            && dst == flow.virtual_ip
            && src_port == flow.service_port
            && dst_port == flow.client_port
    }) {
        let flow = flows[index].clone();
        flows[index].last_seen = now;
        let original_src = src;
        let original_dst = dst;

        // Present the response to SideStore exactly as if it came from 10.7.0.1.
        packet[12..16].copy_from_slice(&flow.virtual_ip);
        packet[16..20].copy_from_slice(&flow.client_ip);
        if !repair_ipv4_tcp_checksums(packet) {
            // Restore the untouched addressing before the caller falls back to the
            // legacy reflection path.
            packet[12..16].copy_from_slice(&original_src);
            packet[16..20].copy_from_slice(&original_dst);
            log_msg(2, "[EMP-HAIRPIN] REVERSE checksum repair FAILED; restored packet for legacy reflection".to_string());
            return false;
        }
        if flags & (0x02 | 0x04 | 0x01) != 0 {
            log_msg(
                2,
                format!(
                    "[EMP-HAIRPIN] REVERSE flow matched physical={}:{} virtual={}:{} client={}:{}",
                    ip4_text(flow.physical_ip),
                    flow.service_port,
                    ip4_text(flow.virtual_ip),
                    flow.service_port,
                    ip4_text(flow.client_ip),
                    flow.client_port
                ),
            );
        }
        return true;
    }

    // RemotePairing control is fixed at 49152 and already works with ordinary
    // reflection. createListener() uses the iOS high ephemeral range above it.
    // Restrict hairpinning to high-port requests and explicitly exclude packets
    // sourced by the legacy control listener. Its SYN|ACK destination is an
    // ephemeral client port, so checking destination port alone is insufficient.
    let virtual_peer = [10, 7, 0, 1];
    let legacy_control_port: u16 = 49152;
    let client_is_wireguard = src[0] == 10 && src[1] == 7 && src[2] == 0 && src != virtual_peer;
    let dynamic_candidate = dst == virtual_peer
        && dst_port >= 49153
        && src_port != legacy_control_port;
    if !client_is_wireguard || !dynamic_candidate {
        return false;
    }

    let existing = flows.iter().position(|flow| {
        flow.client_ip == src
            && flow.client_port == src_port
            && flow.virtual_ip == dst
            && flow.service_port == dst_port
    });

    let (physical_ip, is_new) = if let Some(index) = existing {
        flows[index].last_seen = now;
        (flows[index].physical_ip, false)
    } else {
        let Some(physical_ip) = hairpin_en0_ipv4() else {
            if flags & 0x02 != 0 {
                log_msg(2, "[EMP-HAIRPIN] FORWARD no en0/en* IPv4 available; using legacy reflection".to_string());
            }
            return false;
        };
        flows.push(HairpinFlow {
            client_ip: src,
            virtual_ip: dst,
            physical_ip,
            client_port: src_port,
            service_port: dst_port,
            last_seen: now,
        });
        (physical_ip, true)
    };

    let original_src = src;
    let original_dst = dst;

    // Inject an inbound packet whose source is the WireGuard peer (allowed by the
    // client's cryptokey route) but whose destination is the phone's physical Wi-Fi
    // address where iOS exposes the RemotePairing dynamic listener.
    packet[12..16].copy_from_slice(&dst);
    packet[16..20].copy_from_slice(&physical_ip);
    if !repair_ipv4_tcp_checksums(packet) {
        packet[12..16].copy_from_slice(&original_src);
        packet[16..20].copy_from_slice(&original_dst);
        log_msg(2, "[EMP-HAIRPIN] FORWARD checksum repair FAILED; restored packet for legacy reflection".to_string());
        return false;
    }

    if is_new || flags & 0x02 != 0 {
        log_msg(
            2,
            format!(
                "[EMP-HAIRPIN] FORWARD flow installed client={}:{} virtual={}:{} physical={}:{} checksum=rebuilt",
                ip4_text(src),
                src_port,
                ip4_text(dst),
                dst_port,
                ip4_text(physical_ip),
                dst_port
            ),
        );
    }
    true
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
        let mut hairpin_flows: Vec<HairpinFlow> = Vec::new();
        log_msg(2, "[EMP-HAIRPIN] physical-listener hairpin active; dynamic_tcp_min=49153 legacy_control=49152".to_string());
        loop {'''
if state_old not in s:
    raise SystemExit("Could not locate EMP-DIAG worker state block")
s = s.replace(state_old, state_new, 1)

old_translate = '''                            log_ipv4_tcp("RX-DECAP", b);

                            // Reflect the packet back to iOS by swapping IPv4 source/destination.
                            b.swap(12, 16);
                            b.swap(13, 17);
                            b.swap(14, 18);
                            b.swap(15, 19);

                            log_ipv4_tcp("TX-REFLECT", b);
'''
new_translate = '''                            log_ipv4_tcp("RX-DECAP", b);

                            // First try the stateful physical-interface hairpin for
                            // createListener()'s high TCP ports. If the packet is not a
                            // hairpin flow (or en0 is unavailable), preserve the proven
                            // source/destination reflection used by RemotePairing control.
                            if hairpin_translate_ipv4_tcp(b, &mut hairpin_flows) {
                                log_ipv4_tcp("TX-HAIRPIN", b);
                            } else {
                                b.swap(12, 16);
                                b.swap(13, 17);
                                b.swap(14, 18);
                                b.swap(15, 19);
                                log_ipv4_tcp("TX-REFLECT", b);
                            }
'''
if old_translate not in s:
    raise SystemExit("Could not locate EMP-DIAG reflection translation block")
s = s.replace(old_translate, new_translate, 1)

p.write_text(s)
patched = p.read_text()
required = [
    marker,
    "struct HairpinFlow",
    "repair_ipv4_tcp_checksums",
    "hairpin_en0_ipv4",
    "hairpin_translate_ipv4_tcp",
    "dynamic_tcp_min=49153",
    "src_port != legacy_control_port",
    "FORWARD flow installed",
    "REVERSE flow matched",
    "restored packet for legacy reflection",
    'log_ipv4_tcp("TX-HAIRPIN", b);',
    'log_ipv4_tcp("TX-REFLECT", b);',
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"EMProxy hairpin verification failed: {missing}")

# Packet diagnostics may report packet metadata and payload LENGTH only. Reject only
# explicit payload/content encoders or raw-buffer debug patterns; em_proxy legitimately
# uses base64 elsewhere, so a blanket `base64::` ban is a false positive.
for forbidden in [
    "hex::encode(packet",
    "hex::encode(b)",
    "packet payload bytes",
    "payload_bytes={:?}",
    "packet={:?}",
]:
    if forbidden in patched:
        raise SystemExit(f"Secret/payload safety verification failed: {forbidden}")

print("Stateful EMProxy physical-listener hairpin applied and verified")
