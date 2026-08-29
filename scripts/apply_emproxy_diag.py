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
    let flags = packet[ihl + 13];

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
            "[EMP-DIAG] {stage} TCP {src}:{src_port} -> {dst}:{dst_port} flags=0x{flags:02x}({}) seq={seq} ack={ack} bytes={}",
            names.join("|"),
            packet.len()
        ),
    );
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

old = '''                        boringtun::noise::TunnResult::WriteToTunnelV4(b, _addr) => {
                            // Swap bytes 12-15 with 16-19
                            b.swap(12, 16);
                            b.swap(13, 17);
                            b.swap(14, 18);
                            b.swap(15, 19);

                            let mut buf = [0_u8; 2048];
                            match tun.encapsulate(b, &mut buf) {'''

new = '''                        boringtun::noise::TunnResult::WriteToTunnelV4(b, _addr) => {
                            log_ipv4_tcp("RX-DECAP", b);

                            // Reflect the packet back to iOS by swapping IPv4 source/destination.
                            b.swap(12, 16);
                            b.swap(13, 17);
                            b.swap(14, 18);
                            b.swap(15, 19);

                            log_ipv4_tcp("TX-REFLECT", b);

                            let mut buf = [0_u8; 2048];
                            match tun.encapsulate(b, &mut buf) {'''

if old not in s:
    raise SystemExit("Could not locate WriteToTunnelV4 reflection block")
s = s.replace(old, new, 1)

p.write_text(s)
print("EMProxy TCP packet diagnostic patch applied")
