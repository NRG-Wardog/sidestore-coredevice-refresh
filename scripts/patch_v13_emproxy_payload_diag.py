#!/usr/bin/env python3
"""Expand EMProxy diagnostics without logging payload contents or spinning forever.

The existing diagnostic patch records only SYN/RST/FIN transitions.  v13 needs
to distinguish a four-byte Lockdown prefix from a complete framed plist, so it
records bounded metadata for peer-facing payload packets as well.
"""

from __future__ import annotations

from pathlib import Path
import sys

MARKER = "[EMP-V13-PAYLOAD]"


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def verify(text: str) -> None:
    required = [
        MARKER,
        "V13_LOCKDOWN_EVENT_COUNTER",
        "V13_PEER_EVENT_COUNTER",
        "is_lockdown_flow",
        "payload_len",
        "event_index=",
        "event_class=",
        "interesting_flags",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        die(f"v13 EMProxy payload diagnostic verification failed; missing: {missing}")

    forbidden = [
        "from_utf8_lossy(b)",
        "hex::encode",
        "payload_bytes",
        "packet_body",
        "{:02x?}",
    ]
    leaked = [item for item in forbidden if item in text]
    if leaked:
        die(f"v13 EMProxy payload diagnostic leaks packet content: {leaked}")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v13_emproxy_payload_diag.py <em_proxy/src/lib.rs>")

    path = Path(sys.argv[1])
    if not path.exists():
        die(f"missing EMProxy source: {path}")
    source = path.read_text()

    if MARKER in source:
        verify(source)
        print("v13 bounded EMProxy payload diagnostics already present and verified")
        return

    if "[EMP-DIAG] packet diagnostic build active" not in source:
        die("v13 EMProxy patch requires apply_emproxy_diag.py first")
    if "[EMP-NONBLOCK]" not in source:
        die("v13 EMProxy patch requires the nonblocking UDP fix first")

    old_gate = '''    // Packet-level logging can itself perturb a loopback timing bug. We only
    // record TCP control transitions needed to distinguish SYN reflection,
    // SYN-ACK, RST and teardown. TLS/RSD progress is logged by SS-DIAG.
    let interesting_flags = flags & (0x02 | 0x04 | 0x01) != 0; // SYN | RST | FIN
    if !interesting_flags {
        return;
    }
'''
    new_gate = '''    // v13 records payload lengths, never payload bytes.  This is required to
    // prove whether lockdownd receives only the four-byte frame prefix or the
    // complete prefix+plist write.  Event budgets prevent runaway console logs.
    let interesting_flags = flags & (0x02 | 0x04 | 0x01) != 0; // SYN | RST | FIN
    let peer = std::net::Ipv4Addr::new(10, 7, 0, 1);
    let involves_peer = src == peer || dst == peer;
    let is_lockdown_flow = src_port == 62078 || dst_port == 62078;
    if !involves_peer || (!is_lockdown_flow && payload_len == 0 && !interesting_flags) {
        return;
    }

    static V13_LOCKDOWN_EVENT_COUNTER: std::sync::atomic::AtomicU64 =
        std::sync::atomic::AtomicU64::new(0);
    static V13_PEER_EVENT_COUNTER: std::sync::atomic::AtomicU64 =
        std::sync::atomic::AtomicU64::new(0);
    let (event_index, event_limit, event_class) = if is_lockdown_flow {
        (
            V13_LOCKDOWN_EVENT_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
            256_u64,
            "lockdown",
        )
    } else {
        (
            V13_PEER_EVENT_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
            512_u64,
            "peer",
        )
    };
    if event_index >= event_limit {
        return;
    }
'''
    if source.count(old_gate) != 1:
        die(f"EMProxy diagnostic gate anchor: expected once, found {source.count(old_gate)}")
    source = source.replace(old_gate, new_gate, 1)

    old_format = '''            "[EMP-DIAG] {stage} TCP {src}:{src_port} -> {dst}:{dst_port} flags=0x{flags:02x}({}) seq={seq} ack={ack} payload={} bytes={}",
            names.join("|"),
            payload_len,
            packet.len()
'''
    new_format = '''            "[EMP-V13-PAYLOAD] {stage} event_class={event_class} event_index={event_index} TCP {src}:{src_port} -> {dst}:{dst_port} flags=0x{flags:02x}({}) seq={seq} ack={ack} payload={} bytes={}",
            names.join("|"),
            payload_len,
            packet.len()
'''
    if source.count(old_format) != 1:
        die(f"EMProxy diagnostic format anchor: expected once, found {source.count(old_format)}")
    source = source.replace(old_format, new_format, 1)

    path.write_text(source)
    verify(path.read_text())
    print("v13 bounded EMProxy TCP payload-length diagnostics applied and verified")


if __name__ == "__main__":
    main()
