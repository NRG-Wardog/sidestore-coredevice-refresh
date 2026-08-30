#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_source_bound_dynamic.py <tunnel_provider.rs>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-SOURCE-BOUND] en0-v4 source-bound connect active"

if marker in s:
    required = [
        "tokio::net::TcpSocket::new_v4()",
        "10, 7, 0, 10",
        "socket.bind(source)",
        "socket.connect(target).await",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"Source-bound marker present but patch incomplete: {missing}")
    print("Source-bound dynamic connect already present and verified")
    raise SystemExit(0)

if "[SS-ADAPT] adaptive transport engine active" not in s:
    raise SystemExit("Adaptive transport patch must be applied first")

insert_marker = "/// Shared logic: given a connected & paired `RemotePairingClient`, create"
insert_at = s.find(insert_marker)
if insert_at < 0:
    raise SystemExit("Could not locate helper insertion point")

helper = r'''// On-device RemotePairing cannot treat a physical-interface self-connect
// exactly like an external peer.  The raw EMProxy hairpin proves the desired
// source identity (10.7.0.10/10.7.0.1 side) but iOS does not feed that spoofed
// outbound packet back through the local TCP listener.  For the physical en0
// IPv4 candidate, bind the client socket to the real utun address first and let
// the kernel perform the local delivery.  This preserves a distinct peer source
// without raw packet injection.
async fn ss_connect_dynamic_candidate(
    label: &str,
    target: std::net::SocketAddr,
) -> std::io::Result<tokio::net::TcpStream> {
    if label == "dynamic-iface-en0-v4" && target.is_ipv4() {
        let source = std::net::SocketAddr::V4(std::net::SocketAddrV4::new(
            std::net::Ipv4Addr::new(10, 7, 0, 10),
            0,
        ));
        tracing::error!(
            "[SS-SOURCE-BOUND] en0-v4 source-bound connect active source={} target={}",
            source,
            target
        );
        let socket = tokio::net::TcpSocket::new_v4()?;
        socket.bind(source)?;
        match socket.connect(target).await {
            Ok(stream) => {
                let local = stream
                    .local_addr()
                    .map(|a| a.to_string())
                    .unwrap_or_else(|e| format!("<local_addr error: {e}>"));
                let peer = stream
                    .peer_addr()
                    .map(|a| a.to_string())
                    .unwrap_or_else(|e| format!("<peer_addr error: {e}>"));
                tracing::error!(
                    "[SS-SOURCE-BOUND] TCP CONNECTED local={} peer={}",
                    local,
                    peer
                );
                Ok(stream)
            }
            Err(e) => {
                tracing::error!(
                    "[SS-SOURCE-BOUND] TCP FAILED source={} target={} kind={:?} error={}",
                    source,
                    target,
                    e.kind(),
                    e
                );
                Err(e)
            }
        }
    } else {
        tokio::net::TcpStream::connect(target).await
    }
}

'''

s = s[:insert_at] + helper + s[insert_at:]

old = '''            match tokio::time::timeout(
                std::time::Duration::from_millis(450),
                tokio::net::TcpStream::connect(target),
            )
            .await
'''
new = '''            match tokio::time::timeout(
                std::time::Duration::from_millis(450),
                ss_connect_dynamic_candidate(&label, target),
            )
            .await
'''

if old not in s:
    raise SystemExit("Could not locate adaptive dynamic TcpStream::connect block")
s = s.replace(old, new, 1)

p.write_text(s)
patched = p.read_text()
required = [
    marker,
    "async fn ss_connect_dynamic_candidate",
    "tokio::net::TcpSocket::new_v4()",
    "std::net::Ipv4Addr::new(10, 7, 0, 10)",
    "socket.bind(source)?",
    "socket.connect(target).await",
    "ss_connect_dynamic_candidate(&label, target)",
    "[SS-SOURCE-BOUND] TCP CONNECTED",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"Source-bound dynamic verification failed: {missing}")

print("Source-bound en0-v4 dynamic listener connect applied and verified")
