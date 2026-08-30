#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_source_bound_dynamic.py <tunnel_provider.rs>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-SOURCE-BOUND-V6] utun-v6 to scoped en0 listener active"

if marker in s:
    required = [
        "dynamic-iface-en0-v6-sourcebound",
        "dynamic-iface-en0-v6-direct",
        "fd00:7::10",
        "libc::IPV6_BOUND_IF",
        "ss_connect_dynamic_candidate(&label, target)",
        "[SS-V8-DIAG] dynamic candidate plan",
        "[SS-SOURCE-BOUND] en0-v4 source-bound connect active",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"Scoped IPv6 source-bound marker present but patch incomplete: {missing}")
    print("Scoped IPv6 source-bound dynamic transport already present and verified")
    raise SystemExit(0)

if "[SS-ADAPT] adaptive transport engine active" not in s:
    raise SystemExit("Adaptive transport patch must be applied first")

# The exhaustive v7 candidate matrix was useful for diagnosis, but it burns a
# fresh one-shot CoreDevice listener for every irrelevant interface.  The logs
# have now proved that only the configured control path and en0 IPv6 dynamic
# listener are relevant.  Keep one direct en0 self-connect as a control test and
# the existing en0 IPv4 source-bound path as a final diagnostic fallback.
control_start = s.find("fn ss_control_candidates(")
dynamic_start = s.find("fn ss_dynamic_candidates(", control_start)
shared_start = s.find("\n/// Shared logic:", dynamic_start)
if control_start < 0 or dynamic_start < 0 or shared_start < 0:
    raise SystemExit("Could not locate adaptive control/dynamic candidate functions")

candidate_functions = r'''fn ss_control_candidates(configured: std::net::SocketAddr) -> Vec<(String, std::net::SocketAddr)> {
    tracing::error!(
        "[SS-V8-DIAG] control candidate plan: configured endpoint only target={}",
        configured
    );
    vec![(String::from("configured-control"), configured)]
}

fn ss_dynamic_candidates(control_addr: std::net::SocketAddr) -> Vec<(String, std::net::SocketAddr)> {
    let mut en0_link_local_v6: Option<std::net::SocketAddr> = None;
    let mut en0_other_v6: Option<std::net::SocketAddr> = None;
    let mut en0_v4: Option<std::net::SocketAddr> = None;

    for (label, mut addr) in ss_local_interface_candidates(0) {
        addr.set_port(0);
        if label == "iface-en0-v6" {
            if let std::net::SocketAddr::V6(v6) = addr {
                if v6.ip().is_unicast_link_local() {
                    if en0_link_local_v6.is_none() {
                        en0_link_local_v6 = Some(std::net::SocketAddr::V6(v6));
                    }
                } else if en0_other_v6.is_none() {
                    en0_other_v6 = Some(std::net::SocketAddr::V6(v6));
                }
            }
        } else if label == "iface-en0-v4" && en0_v4.is_none() {
            en0_v4 = Some(addr);
        }
    }

    let mut out: Vec<(String, std::net::SocketAddr)> = Vec::new();
    if let Some(en0_v6) = en0_link_local_v6.or(en0_other_v6) {
        // First: the actual fix.  The socket is bound to the WireGuard ULA so
        // CoreDevice sees a distinct peer, while the scoped destination keeps
        // the local delivery in en0's link-local zone.
        out.push((String::from("dynamic-iface-en0-v6-sourcebound"), en0_v6));

        // Second: a forensic control.  This is the old physical self-connect;
        // it is expected to reach TLS and then be rejected by CDTunnel.  Keeping
        // it makes a source-bound regression immediately distinguishable from
        // a protocol regression.
        out.push((String::from("dynamic-iface-en0-v6-direct"), en0_v6));
    }
    if let Some(en0_v4) = en0_v4 {
        out.push((String::from("dynamic-iface-en0-v4"), en0_v4));
    }

    if out.is_empty() {
        tracing::error!(
            "[SS-V8-DIAG] NO_EN0_CANDIDATE control_target={} — Wi-Fi is not connected or en0 has no usable address",
            control_addr
        );
    } else {
        tracing::error!(
            "[SS-V8-DIAG] dynamic candidate plan control_target={} count={} order={:?}",
            control_addr,
            out.len(),
            out.iter().map(|(label, addr)| format!("{label}@{addr}")).collect::<Vec<_>>()
        );
    }
    out
}
'''

s = s[:control_start] + candidate_functions + s[shared_start:]

insert_marker = "/// Shared logic: given a connected & paired `RemotePairingClient`, create"
insert_at = s.find(insert_marker)
if insert_at < 0:
    raise SystemExit("Could not locate helper insertion point")

helper = r'''// SideStore v8: kernel-routed scoped IPv6 dynamic-listener transport.
//
// v7 proved that a raw IPv6 SYN re-injected through WireGuard cannot reach an
// en0 link-local listener: the packet arrives through utun and the IPv6 header
// carries no zone identifier.  Use a real kernel TCP socket instead.  Bind the
// local endpoint to the WireGuard ULA (fd00:7::10), preserve the en0 scope_id on
// the link-local destination, and on Apple platforms also test IPV6_BOUND_IF.
// This gives CoreDevice a peer address distinct from the phone's own en0 address
// while retaining the correct link-local zone.
#[cfg(target_vendor = "apple")]
fn ss_set_ipv6_bound_if(
    socket: &tokio::net::TcpSocket,
    ifindex: u32,
) -> std::io::Result<()> {
    use std::os::fd::AsRawFd;

    if ifindex == 0 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "link-local target has no scope/interface index",
        ));
    }
    let value: libc::c_uint = ifindex as libc::c_uint;
    let rc = unsafe {
        libc::setsockopt(
            socket.as_raw_fd(),
            libc::IPPROTO_IPV6,
            libc::IPV6_BOUND_IF,
            &value as *const _ as *const libc::c_void,
            std::mem::size_of_val(&value) as libc::socklen_t,
        )
    };
    if rc == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

#[cfg(not(target_vendor = "apple"))]
fn ss_set_ipv6_bound_if(
    _socket: &tokio::net::TcpSocket,
    _ifindex: u32,
) -> std::io::Result<()> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "IPV6_BOUND_IF is Apple-specific",
    ))
}

async fn ss_connect_scoped_en0_v6(
    target: std::net::SocketAddr,
) -> std::io::Result<tokio::net::TcpStream> {
    let target_v6 = match target {
        std::net::SocketAddr::V6(v6) => v6,
        _ => {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "scoped en0 source-bound path requires an IPv6 target",
            ));
        }
    };
    let source = std::net::SocketAddr::V6(std::net::SocketAddrV6::new(
        std::net::Ipv6Addr::new(0xfd00, 0x0007, 0, 0, 0, 0, 0, 0x0010),
        0,
        0,
        0,
    ));

    tracing::error!(
        "[SS-SOURCE-BOUND-V6] utun-v6 to scoped en0 listener active source={} target={} scope_id={}",
        source,
        target,
        target_v6.scope_id()
    );

    let mut last_error: Option<std::io::Error> = None;
    for (mode, bind_interface) in [
        ("scope-id-route", false),
        ("IPV6_BOUND_IF", true),
    ] {
        let socket = match tokio::net::TcpSocket::new_v6() {
            Ok(socket) => socket,
            Err(e) => {
                tracing::error!(
                    "[SS-SOURCE-BOUND-V6] SOCKET FAILED mode={} kind={:?} error={}",
                    mode,
                    e.kind(),
                    e
                );
                last_error = Some(e);
                continue;
            }
        };

        if bind_interface {
            match ss_set_ipv6_bound_if(&socket, target_v6.scope_id()) {
                Ok(()) => tracing::error!(
                    "[SS-SOURCE-BOUND-V6] IPV6_BOUND_IF SUCCESS ifindex={}",
                    target_v6.scope_id()
                ),
                Err(e) => {
                    tracing::error!(
                        "[SS-SOURCE-BOUND-V6] IPV6_BOUND_IF FAILED ifindex={} kind={:?} error={}",
                        target_v6.scope_id(),
                        e.kind(),
                        e
                    );
                    last_error = Some(e);
                    continue;
                }
            }
        }

        tracing::error!(
            "[SS-SOURCE-BOUND-V6] BIND START mode={} source={} target={}",
            mode,
            source,
            target
        );
        if let Err(e) = socket.bind(source) {
            tracing::error!(
                "[SS-SOURCE-BOUND-V6] BIND FAILED mode={} source={} kind={:?} error={} — ensure WireGuard interface has fd00:7::10/64",
                mode,
                source,
                e.kind(),
                e
            );
            last_error = Some(e);
            continue;
        }
        tracing::error!(
            "[SS-SOURCE-BOUND-V6] BIND SUCCESS mode={} source={} target={}",
            mode,
            source,
            target
        );

        match tokio::time::timeout(
            std::time::Duration::from_millis(550),
            socket.connect(target),
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
                    "[SS-SOURCE-BOUND-V6] TCP CONNECTED mode={} local={} peer={}",
                    mode,
                    local,
                    peer
                );
                return Ok(stream);
            }
            Ok(Err(e)) => {
                tracing::error!(
                    "[SS-SOURCE-BOUND-V6] TCP FAILED mode={} source={} target={} kind={:?} error={}",
                    mode,
                    source,
                    target,
                    e.kind(),
                    e
                );
                last_error = Some(e);
            }
            Err(_) => {
                let e = std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    format!("{mode} connect timed out after 550ms"),
                );
                tracing::error!(
                    "[SS-SOURCE-BOUND-V6] TCP TIMEOUT mode={} source={} target={} timeout=550ms",
                    mode,
                    source,
                    target
                );
                last_error = Some(e);
            }
        }
    }

    Err(last_error.unwrap_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::Other,
            "scoped IPv6 source-bound connect exhausted without an OS result",
        )
    }))
}

async fn ss_connect_dynamic_candidate(
    label: &str,
    target: std::net::SocketAddr,
) -> std::io::Result<tokio::net::TcpStream> {
    if label == "dynamic-iface-en0-v6-sourcebound" {
        return ss_connect_scoped_en0_v6(target).await;
    }

    if label == "dynamic-iface-en0-v6-direct" {
        tracing::error!(
            "[SS-V8-DIAG] direct en0-v6 self-connect control path source=kernel-selected target={}",
            target
        );
        return tokio::net::TcpStream::connect(target).await;
    }

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
                std::time::Duration::from_millis(
                    if label == "dynamic-iface-en0-v6-sourcebound" { 1400 } else { 450 }
                ),
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
    "async fn ss_connect_scoped_en0_v6",
    "tokio::net::TcpSocket::new_v6()",
    "std::net::Ipv6Addr::new(0xfd00, 0x0007",
    "libc::IPV6_BOUND_IF",
    "socket.bind(source)",
    "socket.connect(target)",
    "dynamic-iface-en0-v6-sourcebound",
    "dynamic-iface-en0-v6-direct",
    "ss_connect_dynamic_candidate(&label, target)",
    "[SS-SOURCE-BOUND-V6] TCP CONNECTED",
    "[SS-SOURCE-BOUND] en0-v4 source-bound connect active",
    "[SS-V8-DIAG] dynamic candidate plan",
    "[SS-V8-DIAG] NO_EN0_CANDIDATE",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"Scoped IPv6 source-bound verification failed: {missing}")

# The v7 raw NAT46 path remains compiled in EMProxy for forensic comparison but
# is intentionally unreachable because control-same-ip is no longer a dynamic
# candidate.  This assertion prevents an accidental reintroduction.
if 'String::from("control-same-ip")' in patched:
    raise SystemExit("v8 candidate reduction failed: control-same-ip/NAT46 path is still reachable")

print("Scoped IPv6 source-bound en0 dynamic listener transport applied and verified")
