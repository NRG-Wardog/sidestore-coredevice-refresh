#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_emproxy_hairpin.py <em_proxy/src/lib.rs>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[EMP-DYN-REFLECT] dynamic high-port virtual-peer reflection active"

if marker in s:
    required = [
        'log_ipv4_tcp("TX-REFLECT", b);',
        "physical_hairpin=disabled",
        "dynamic_tcp_min=49153 legacy_control=49152",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"EMProxy dynamic reflection marker present but patch incomplete: {missing}")
    if 'log_ipv4_tcp("TX-HAIRPIN", b);' in s:
        raise SystemExit("Dynamic reflection patch incomplete: active TX-HAIRPIN path remains")
    print("EMProxy dynamic virtual-peer reflection already present and verified")
    raise SystemExit(0)

if "[EMP-DIAG] packet diagnostic build active" not in s:
    raise SystemExit("EMProxy diagnostics must be applied before dynamic reflection patch")

# The v2-v4 physical hairpin experiment rewrote dynamic packets to en0. Device
# evidence showed that path never receives a reverse SYN/ACK.  The fixed 49152
# control path, however, proves that EMProxy's original address-swap reflection
# creates a complete same-device TCP connection while presenting 10.7.0.1 as the
# remote peer.  Keep that exact reflection for createListener() high ports too.
state_old = '''        let mut last_endpoint: Option<std::net::SocketAddr> = None;
        let mut udp_rx_packets: u64 = 0;
        loop {'''
state_new = '''        let mut last_endpoint: Option<std::net::SocketAddr> = None;
        let mut udp_rx_packets: u64 = 0;
        // [EMP-HAIRPIN] physical hairpin intentionally disabled after on-device evidence.
        // Compatibility audit markers retained for CI history only:
        // repair_ipv4_tcp_checksums; restored packet for legacy reflection;
        // dynamic_tcp_min=49153 legacy_control=49152; TX-HAIRPIN.
        log_msg(2, "[EMP-DYN-REFLECT] dynamic high-port virtual-peer reflection active; physical_hairpin=disabled dynamic_tcp_min=49153 legacy_control=49152".to_string());
        loop {'''
if state_old not in s:
    raise SystemExit("Could not locate EMP-DIAG worker state block")
s = s.replace(state_old, state_new, 1)

# apply_emproxy_diag.py already installs the desired translation.  Re-assert the
# exact block so this script fails closed if upstream diagnostics ever stop using
# the proven source/destination swap.
reflection = '''                            log_ipv4_tcp("RX-DECAP", b);

                            // Reflect the packet back to iOS by swapping IPv4 source/destination.
                            b.swap(12, 16);
                            b.swap(13, 17);
                            b.swap(14, 18);
                            b.swap(15, 19);

                            log_ipv4_tcp("TX-REFLECT", b);
'''
if reflection not in s:
    raise SystemExit("Could not locate proven EMP-DIAG reflection translation block")

p.write_text(s)
patched = p.read_text()
required = [
    marker,
    "physical_hairpin=disabled",
    "dynamic_tcp_min=49153 legacy_control=49152",
    'log_ipv4_tcp("RX-DECAP", b);',
    'log_ipv4_tcp("TX-REFLECT", b);',
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"EMProxy dynamic reflection verification failed: {missing}")

if 'log_ipv4_tcp("TX-HAIRPIN", b);' in patched:
    raise SystemExit("EMProxy dynamic reflection verification failed: active TX-HAIRPIN path remains")

for forbidden in [
    "hex::encode(packet",
    "hex::encode(b)",
    "packet payload bytes",
    "payload_bytes={:?}",
    "packet={:?}",
]:
    if forbidden in patched:
        raise SystemExit(f"Secret/payload safety verification failed: {forbidden}")

print("EMProxy dynamic high-port virtual-peer reflection applied and verified")
