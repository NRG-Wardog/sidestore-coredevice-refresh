#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_emproxy_diag.py <src/lib.rs>")

p = Path(sys.argv[1])
s = p.read_text()

if "[EMP-DIAG] packet diagnostic build active" in s:
    print("EMProxy diagnostic patch already present")
    raise SystemExit(0)

helper = r'''
fn log_ipv4_tcp(stage: &str, packet: &[u8]) {
    if packet.len() < 20 || (packet[0] >> 4) != 4 {
        return;
    }

    let ihl = ((packet[0] & 0x0f) as usize) * 4;
    if ihl < 20 || packet.len() < ihl + 20 || packet[9] != 6 {
        return;
    }

    let src = std::net::Ipv4Addr::new(packet[12], packet[13], packet[14], packet[15]);
    let dst = std::net::Ipv4Addr::new(packet[16], packet[17], packet[18], packet[19]);
    let src_port = u16::from_be_bytes([packet[ihl], packet[ihl + 1]]);
    let dst_port = u16::from_be_bytes([packet[ihl + 2], packet[ihl + 3]]);
    let seq = u32::from_be_bytes([
        packet[ihl + 4],
        packet[ihl + 5],
        packet[ihl + 6],
        packet[ihl + 7],
    ]);
    let ack = u32::from_be_bytes([
        packet[ihl + 8],
        packet[ihl + 9],
        packet[ihl + 10],
        packet[ihl + 11],
    ]);
    let data_offset = ((packet[ihl + 12] >> 4) as usize) * 4;
    let flags = packet[ihl + 13];
    let payload_len = packet
        .len()
        .saturating_sub(ihl.saturating_add(data_offset));

    let mut names = Vec::new();
    if flags & 0x02 != 0 { names.push("SYN"); }
    if flags & 0x10 != 0 { names.push("ACK"); }
    if flags & 0x04 != 0 { names.push("RST"); }
    if flags & 0x01 != 0 { names.push("FIN"); }
    if flags & 0x08 != 0 { names.push("PSH"); }
    if flags & 0x20 != 0 { names.push("URG"); }
    if names.is_empty() { names.push("NONE"); }

    log_msg(
        1,
        format!(
            "[EMP-DIAG] {stage} TCP {src}:{src_port} -> {dst}:{dst_port} flags=0x{flags:02x}({}) seq={seq} ack={ack} payload={} bytes={}",
            names.join("|"),
            payload_len,
            packet.len()
        ),
    );
}

fn log_ipv4_summary(stage: &str, packet: &[u8]) {
    if packet.len() < 20 || (packet[0] >> 4) != 4 {
        return;
    }

    let src = std::net::Ipv4Addr::new(packet[12], packet[13], packet[14], packet[15]);
    let dst = std::net::Ipv4Addr::new(packet[16], packet[17], packet[18], packet[19]);
    let proto = packet[9];

    if proto == 6 {
        log_ipv4_tcp(stage, packet);
    } else {
        log_msg(
            1,
            format!(
                "[EMP-DIAG] {stage} IPv4 {src} -> {dst} proto={} bytes={}",
                proto,
                packet.len()
            ),
        );
    }
}

'''

marker = "pub fn start_loopback(bind_addr: SocketAddrV4) -> Result<ProxyHandle, c_int> {"
if marker not in s:
    raise SystemExit("Could not locate start_loopback()")
s = s.replace(marker, helper + marker, 1)

start_old = marker + "\n    // Create the handle"
start_new = marker + "\n    log_msg(1, format!(\"[EMP-DIAG] packet diagnostic build active; bind={bind_addr}\"));\n\n    // Create the handle"
if start_old not in s:
    raise SystemExit("Could not locate start_loopback body marker")
s = s.replace(start_old, start_new, 1)

loop_old = """    let join_handle = std::thread::spawn(move || {
        let mut ready = false;
        loop {"""
loop_new = """    let join_handle = std::thread::spawn(move || {
        let mut ready = false;
        let mut last_endpoint: Option<std::net::SocketAddr> = None;
        let mut udp_rx_packets: u64 = 0;
        let mut reflected_ipv4_packets: u64 = 0;
        loop {"""
if loop_old not in s:
    raise SystemExit("Could not locate EMProxy worker loop")
s = s.replace(loop_old, loop_new, 1)

recv_old = """                Ok((size, endpoint)) => {
                    // Parse it with boringtun
                    let mut unencrypted_buf = [0; 2176];"""
recv_new = """                Ok((size, endpoint)) => {
                    udp_rx_packets = udp_rx_packets.saturating_add(1);
                    if last_endpoint != Some(endpoint) {
                        log_msg(
                            1,
                            format!(
                                "[EMP-DIAG] WireGuard UDP endpoint changed: previous={:?} current={} rx_packets={}",
                                last_endpoint,
                                endpoint,
                                udp_rx_packets
                            ),
                        );
                        last_endpoint = Some(endpoint);
                    }
                    if udp_rx_packets <= 8 || udp_rx_packets % 100 == 0 {
                        log_msg(
                            1,
                            format!(
                                "[EMP-DIAG] UDP RX packet={} bytes={} endpoint={}",
                                udp_rx_packets,
                                size,
                                endpoint
                            ),
                        );
                    }

                    // Parse it with boringtun
                    let mut unencrypted_buf = [0; 2176];"""
if recv_old not in s:
    raise SystemExit("Could not locate UDP recv block")
s = s.replace(recv_old, recv_new, 1)

done_old = """                        boringtun::noise::TunnResult::Done => {
                            // literally nobody knows what to do with this
                            if !ready {
                                ready = true;
                                log_msg(1, "Ready!!".to_string());
                            }
                        }"""
done_new = """                        boringtun::noise::TunnResult::Done => {
                            if !ready {
                                ready = true;
                                log_msg(1, "[EMP-DIAG] WireGuard session READY (TunnResult::Done)".to_string());
                            }
                        }"""
if done_old not in s:
    raise SystemExit("Could not locate TunnResult::Done block")
s = s.replace(done_old, done_new, 1)

err_old = """                        boringtun::noise::TunnResult::Err(e) => {
                            log_msg(2, format!("Decapsulation error: {:?}", e));
                        }"""
err_new = """                        boringtun::noise::TunnResult::Err(e) => {
                            log_msg(
                                2,
                                format!(
                                    "[EMP-DIAG] Decapsulation error: {:?}; ready={} endpoint={} rx_packets={}",
                                    e,
                                    ready,
                                    endpoint,
                                    udp_rx_packets
                                ),
                            );
                        }"""
if err_old not in s:
    raise SystemExit("Could not locate decapsulation error block")
s = s.replace(err_old, err_new, 1)

wtn_old = """                        boringtun::noise::TunnResult::WriteToNetwork(b) => {
                            if let Err(e) = socket.send_to(b, endpoint) {"""
wtn_new = """                        boringtun::noise::TunnResult::WriteToNetwork(b) => {
                            log_msg(
                                1,
                                format!(
                                    "[EMP-DIAG] WireGuard WriteToNetwork bytes={} endpoint={} ready={}",
                                    b.len(),
                                    endpoint,
                                    ready
                                ),
                            );
                            if let Err(e) = socket.send_to(b, endpoint) {"""
if wtn_old not in s:
    raise SystemExit("Could not locate initial WriteToNetwork block")
s = s.replace(wtn_old, wtn_new, 1)

old = '''                        boringtun::noise::TunnResult::WriteToTunnelV4(b, _addr) => {
                            // Swap bytes 12-15 with 16-19
                            b.swap(12, 16);
                            b.swap(13, 17);
                            b.swap(14, 18);
                            b.swap(15, 19);

                            let mut buf = [0_u8; 2048];
                            match tun.encapsulate(b, &mut buf) {
                                boringtun::noise::TunnResult::WriteToNetwork(b) => {
                                    if let Err(e) = socket.send_to(b, endpoint) {
                                        log_msg(3, format!("Error sending UDP packet: {:?}", e));
                                    }
                                }
                                _ => {
                                    log_msg(2, "Unexpected result".to_string());
                                }
                            }
                        }'''

new = '''                        boringtun::noise::TunnResult::WriteToTunnelV4(b, _addr) => {
                            reflected_ipv4_packets = reflected_ipv4_packets.saturating_add(1);
                            log_ipv4_summary("RX-DECAP", b);

                            // Reflect the packet back to iOS by swapping IPv4 source/destination.
                            b.swap(12, 16);
                            b.swap(13, 17);
                            b.swap(14, 18);
                            b.swap(15, 19);

                            log_ipv4_summary("TX-REFLECT", b);

                            let mut buf = [0_u8; 2048];
                            match tun.encapsulate(b, &mut buf) {
                                boringtun::noise::TunnResult::WriteToNetwork(encapsulated) => {
                                    log_msg(
                                        1,
                                        format!(
                                            "[EMP-DIAG] reflected IPv4 packet={} encapsulated_bytes={} endpoint={}",
                                            reflected_ipv4_packets,
                                            encapsulated.len(),
                                            endpoint
                                        ),
                                    );
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

if old not in s:
    raise SystemExit("Could not locate WriteToTunnelV4 reflection block")
s = s.replace(old, new, 1)

v6_old = '''                        boringtun::noise::TunnResult::WriteToTunnelV6(_b, _addr) => {
                            log_msg(2, "IPv6 packet ignored".to_string());
                        }'''
v6_new = '''                        boringtun::noise::TunnResult::WriteToTunnelV6(b, addr) => {
                            log_msg(
                                2,
                                format!(
                                    "[EMP-DIAG] IPv6 packet ignored bytes={} addr={:?}",
                                    b.len(),
                                    addr
                                ),
                            );
                        }'''
if v6_old not in s:
    raise SystemExit("Could not locate IPv6 ignored block")
s = s.replace(v6_old, v6_new, 1)

p.write_text(s)

required = [
    "[EMP-DIAG] packet diagnostic build active",
    "[EMP-DIAG] WireGuard UDP endpoint changed",
    "[EMP-DIAG] WireGuard session READY",
    "[EMP-DIAG] Decapsulation error",
    "[EMP-DIAG] RX-DECAP",
    "[EMP-DIAG] TX-REFLECT",
    "payload=",
    "[EMP-DIAG] reflected IPv4 packet",
]
patched = p.read_text()
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"Patch verification failed; missing: {missing}")

print("Deep EMProxy packet diagnostic patch applied and verified")
