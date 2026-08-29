#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_multipath_dynamic_diag.py <tunnel_provider.rs>")

p = Path(sys.argv[1])
s = p.read_text()

marker = "[SS-MPATH] probing dynamic listener candidates"
if marker in s:
    required = [
        "reflected-peer",
        "utun-local-10.7.0.10",
        "loopback-127.0.0.1",
        "[SS-MPATH] TCP CONNECTED",
        "[SS-MPATH] all candidates failed",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"Multipath marker present but patch incomplete; missing: {missing}")
    print("Multipath dynamic-listener diagnostic patch already present and verified")
    raise SystemExit(0)

start = s.find("    let mut tunnel_addr = connect_addr;\n    tunnel_addr.set_port(tunnel_port);")
if start < 0:
    raise SystemExit("Could not locate patched dynamic tunnel target block")

end_marker = "    let connected_attempt = connected_attempt.unwrap_or(0);"
end = s.find(end_marker, start)
if end < 0:
    raise SystemExit("Could not locate end of patched dynamic connect block")
end += len(end_marker)

old = s[start:end]
if "[SS-DIAG] dynamic TCP connect attempt" not in old:
    raise SystemExit("Refusing to patch unexpected dynamic connect block")

new = r'''    let mut tunnel_addr = connect_addr;
    tunnel_addr.set_port(tunnel_port);

    tracing::error!(
        "[SS-DIAG] create_tcp_listener returned port={} target={}",
        tunnel_port,
        tunnel_addr
    );

    // The reflected peer path is known to reach RemotePairing :49152, but on
    // affected iOS versions the listener created by createListener may be bound
    // to a different local path. Probe the three plausible on-device paths in a
    // deterministic order and continue with TLS on the first TCP path that opens.
    let mut candidates: Vec<(&'static str, std::net::SocketAddr)> = Vec::new();
    candidates.push(("reflected-peer", tunnel_addr));

    // Current SideStore WireGuard layout is 10.7.0.10/24 with reflected peer
    // 10.7.0.1. Only add this candidate when the active peer is in that subnet,
    // so the diagnostic cannot accidentally target an unrelated address.
    if let std::net::SocketAddr::V4(v4) = tunnel_addr {
        let o = v4.ip().octets();
        if o[0] == 10 && o[1] == 7 && o[2] == 0 {
            candidates.push((
                "utun-local-10.7.0.10",
                std::net::SocketAddr::V4(std::net::SocketAddrV4::new(
                    std::net::Ipv4Addr::new(10, 7, 0, 10),
                    tunnel_port,
                )),
            ));
        }
    }

    candidates.push((
        "loopback-127.0.0.1",
        std::net::SocketAddr::V4(std::net::SocketAddrV4::new(
            std::net::Ipv4Addr::LOCALHOST,
            tunnel_port,
        )),
    ));

    tracing::error!(
        "[SS-MPATH] probing dynamic listener candidates port={} count={}",
        tunnel_port,
        candidates.len()
    );

    let mut tunnel_stream = None;
    let mut selected_target = tunnel_addr;
    let mut selected_label = "none";
    let mut connected_attempt: Option<u32> = None;
    let mut outcomes: Vec<String> = Vec::new();

    for (label, candidate) in candidates {
        let attempts_for_candidate = if label == "reflected-peer" { 3u32 } else { 2u32 };
        let mut candidate_last = String::from("no attempt made");

        tracing::error!(
            "[SS-MPATH] candidate START label={} target={} attempts={}",
            label,
            candidate,
            attempts_for_candidate
        );

        for attempt in 1..=attempts_for_candidate {
            tracing::error!(
                "[SS-MPATH] TCP connect label={} attempt={}/{} target={}",
                label,
                attempt,
                attempts_for_candidate,
                candidate
            );

            match tokio::time::timeout(
                std::time::Duration::from_millis(350),
                tokio::net::TcpStream::connect(candidate),
            )
            .await
            {
                Ok(Ok(stream)) => {
                    let local = stream
                        .local_addr()
                        .map(|a| a.to_string())
                        .unwrap_or_else(|e| format!("<local_addr error: {e}>"));
                    let peer = stream
                        .peer_addr()
                        .map(|a| a.to_string())
                        .unwrap_or_else(|e| format!("<peer_addr error: {e}>"));
                    tracing::error!(
                        "[SS-MPATH] TCP CONNECTED label={} attempt={} local={} peer={}",
                        label,
                        attempt,
                        local,
                        peer
                    );
                    outcomes.push(format!("{label}=CONNECTED(attempt={attempt},local={local},peer={peer})"));
                    selected_target = candidate;
                    selected_label = label;
                    connected_attempt = Some(attempt);
                    tunnel_stream = Some(stream);
                    break;
                }
                Ok(Err(e)) => {
                    candidate_last = format!("socket:{:?}:{e}", e.kind());
                    tracing::error!(
                        "[SS-MPATH] TCP socket error label={} attempt={} kind={:?} error={}",
                        label,
                        attempt,
                        e.kind(),
                        e
                    );
                }
                Err(_) => {
                    candidate_last = String::from("timeout-350ms");
                    tracing::error!(
                        "[SS-MPATH] TCP timeout label={} attempt={} timeout=350ms",
                        label,
                        attempt
                    );
                }
            }

            tokio::time::sleep(std::time::Duration::from_millis(80)).await;
        }

        if tunnel_stream.is_some() {
            break;
        }

        outcomes.push(format!("{label}=FAILED({candidate_last})"));
        tracing::error!(
            "[SS-MPATH] candidate FAILED label={} target={} last={}",
            label,
            candidate,
            candidate_last
        );
    }

    let tunnel_stream = tunnel_stream.ok_or_else(|| {
        let summary = outcomes.join("; ");
        IdeviceError::InternalError(format!(
            "[SS-MPATH] all candidates failed: port={tunnel_port}; {summary}"
        ))
    })?;
    let connected_attempt = connected_attempt.unwrap_or(0);

    tracing::error!(
        "[SS-MPATH] selected dynamic path label={} target={} connected_attempt={}",
        selected_label,
        selected_target,
        connected_attempt
    );'''

s = s[:start] + new + s[end:]

# TLS must use/log the selected candidate rather than the original reflected-peer address.
s = s.replace(
    '"[SS-DIAG] TLS-PSK handshake START target={} connected_attempt={}",\n        tunnel_addr,',
    '"[SS-DIAG] TLS-PSK handshake START target={} label={} connected_attempt={}",\n        selected_target,\n        selected_label,',
    1,
)
s = s.replace(
    '"[SS-DIAG] TLS-PSK/CDTunnel handshake SUCCESS target={}",\n                tunnel_addr',
    '"[SS-DIAG] TLS-PSK/CDTunnel handshake SUCCESS target={} label={}",\n                selected_target,\n                selected_label',
    1,
)
s = s.replace(
    '"[SS-DIAG] TLS-PSK/CDTunnel FAILED target={tunnel_addr} port={tunnel_port} connected_attempt={connected_attempt} error={e:?}"',
    '"[SS-DIAG] TLS-PSK/CDTunnel FAILED target={selected_target} label={selected_label} port={tunnel_port} connected_attempt={connected_attempt} error={e:?}"',
    1,
)
s = s.replace(
    '"[SS-DIAG] TLS-PSK/CDTunnel TIMEOUT target={tunnel_addr} port={tunnel_port} connected_attempt={connected_attempt} timeout=5s"',
    '"[SS-DIAG] TLS-PSK/CDTunnel TIMEOUT target={selected_target} label={selected_label} port={tunnel_port} connected_attempt={connected_attempt} timeout=5s"',
    1,
)

required = [
    marker,
    '"reflected-peer"',
    '"utun-local-10.7.0.10"',
    '"loopback-127.0.0.1"',
    "[SS-MPATH] TCP CONNECTED",
    "[SS-MPATH] selected dynamic path",
    "[SS-MPATH] all candidates failed",
    "TLS-PSK handshake START target={} label={}",
]
missing = [x for x in required if x not in s]
if missing:
    raise SystemExit(f"Multipath patch verification failed; missing: {missing}")

# Ensure the old single-path terminal error is gone from the patched source.
if "[SS-DIAG] dynamic TCP failed: target=" in s:
    raise SystemExit("Multipath patch verification failed: old single-path terminal error remains")

p.write_text(s)
print("Multipath dynamic-listener probe applied and verified")
