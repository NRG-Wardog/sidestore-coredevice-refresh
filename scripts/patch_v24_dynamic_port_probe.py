#!/usr/bin/env python3
"""V24: probe the exact RemotePairing dynamic listener port across safe local address candidates.

Applied after V23 diagnostics. This does not introduce NAT, port rewriting, or custom TLS.
It only changes which local address is used for the TCP connection to the device-returned
listener port, then hands the connected socket to the stock IDevice TLS-PSK/CDTunnel/RSD path.
"""
from __future__ import annotations
from pathlib import Path
import sys

MARK = "[SS-V24-RPPROBE]"


def die(msg: str) -> None:
    raise SystemExit(msg)


def once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def verify(root: Path) -> None:
    tunnel = (root / "ffi/src/tunnel_provider.rs").read_text()
    rp = (root / "idevice/src/remote_pairing/mod.rs").read_text()
    required = [
        "V24_PROBE_PLAN",
        "V24_PROBE_START",
        "V24_PROBE_TCP_PASS",
        "V24_PROBE_TCP_FAIL",
        "V24_PROBE_SELECTED",
        "V24_PROBE_EXHAUSTED",
        "CREATE_LISTENER_META",
        "peer-reflection",
        "local-utun-route-source",
        "loopback",
        "default-route-local",
        "tokio::time::timeout",
    ]
    missing = [x for x in required if x not in (tunnel + rp)]
    if missing:
        die(f"v24 markers/features missing: {missing}")
    # Preserve the core contract: the port is device-returned and stock TLS follows the selected TCP socket.
    for snippet in [
        "rpc.create_tcp_listener().await",
        "connect_tls_psk_tunnel_native(tunnel_stream, rpc.encryption_key())",
        "RsdHandshake::new(rsd_stream).await",
    ]:
        if snippet not in tunnel:
            die(f"core RPPairing contract missing after V24: {snippet}")
    for forbidden in ["EMP-NAT44", "EMP-TRANSIT", "ipsec-first", "v14-rp-protocol-matrix"]:
        if forbidden in tunnel:
            die(f"forbidden legacy route returned: {forbidden}")
    marker_lines = "\n".join(x for x in (tunnel + "\n" + rp).splitlines() if MARK in x)
    for secret in ["private_key=", "public_key=", "HostPrivateKey", "RootPrivateKey", "DeviceCertificate", "HostCertificate"]:
        if secret in marker_lines:
            die(f"sensitive V24 log marker: {secret}")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v24_dynamic_port_probe.py <idevice-root>")
    root = Path(sys.argv[1])
    tunnel_path = root / "ffi/src/tunnel_provider.rs"
    rp_path = root / "idevice/src/remote_pairing/mod.rs"
    if not tunnel_path.is_file() or not rp_path.is_file():
        die("missing IDevice source files")

    tunnel = tunnel_path.read_text()
    rp = rp_path.read_text()
    if MARK in tunnel and MARK in rp:
        verify(root)
        print("v24 dynamic-port probe already present and verified")
        return
    if MARK in tunnel or MARK in rp:
        die("partial V24 patch detected")
    if "[SS-V23-RPDIAG]" not in tunnel:
        die("V24 requires V23 RP diagnostics to be applied first")

    pin_anchor = "struct PinCtx(*mut c_void);\n"
    helper = r'''fn v24_default_route_source_ip(is_v6: bool) -> Option<std::net::IpAddr> {
    let bind_addr = if is_v6 { "[::]:0" } else { "0.0.0.0:0" };
    let destination = if is_v6 { "[2606:4700:4700::1111]:443" } else { "1.1.1.1:443" };
    let socket = std::net::UdpSocket::bind(bind_addr).ok()?;
    socket.connect(destination).ok()?;
    let ip = socket.local_addr().ok()?.ip();
    if ip.is_unspecified() { None } else { Some(ip) }
}

'''
    tunnel = once(tunnel, pin_anchor, helper + pin_anchor, "V24 default-route helper")

    old_dynamic = r'''    let mut tunnel_addr = connect_addr;
    tunnel_addr.set_port(tunnel_port);
    let route_before = v23_route_source(tunnel_addr);
    tracing::error!("[SS-V23-RPDIAG] RP_DYNAMIC_ROUTE target={} route_source={} control_ip_same=true", tunnel_addr, route_before);

    let connect_started = std::time::Instant::now();
    tracing::error!("[SS-V23-RPDIAG] RP_DYNAMIC_CONNECT_START target={tunnel_addr}");
    let tunnel_stream = match run_global_timeout(|| tokio::net::TcpStream::connect(tunnel_addr)).await {
        Ok(stream) => {
            tracing::error!("[SS-V23-RPDIAG] RP_DYNAMIC_CONNECT_PASS target={} local={:?} peer={:?} elapsed_ms={}", tunnel_addr, stream.local_addr(), stream.peer_addr(), connect_started.elapsed().as_millis());
            stream
        }
        Err(error) => {
            let route_after = v23_route_source(tunnel_addr);
            tracing::error!("[SS-V23-RPDIAG] RP_DYNAMIC_CONNECT_FAIL target={} elapsed_ms={} route_before={} route_after={} error={error:?}", tunnel_addr, connect_started.elapsed().as_millis(), route_before, route_after);
            return Err(IdeviceError::InternalError(format!("RP dynamic TCP connect to {tunnel_addr}: {error}")));
        }
    };
'''
    new_dynamic = r'''    let mut base_tunnel_addr = connect_addr;
    base_tunnel_addr.set_port(tunnel_port);

    let mut candidates: Vec<(&'static str, std::net::SocketAddr)> = Vec::new();
    let mut add_candidate = |label: &'static str, ip: std::net::IpAddr| {
        let addr = std::net::SocketAddr::new(ip, tunnel_port);
        if !candidates.iter().any(|(_, existing)| *existing == addr) {
            candidates.push((label, addr));
        }
    };

    add_candidate("peer-reflection", connect_addr.ip());
    if let Ok(route_source) = v23_route_source(base_tunnel_addr).parse::<std::net::SocketAddr>() {
        add_candidate("local-utun-route-source", route_source.ip());
    }
    let loopback = match connect_addr {
        std::net::SocketAddr::V4(_) => std::net::IpAddr::V4(std::net::Ipv4Addr::LOCALHOST),
        std::net::SocketAddr::V6(_) => std::net::IpAddr::V6(std::net::Ipv6Addr::LOCALHOST),
    };
    add_candidate("loopback", loopback);
    if let Some(ip) = v24_default_route_source_ip(connect_addr.is_ipv6()) {
        add_candidate("default-route-local", ip);
    }

    tracing::error!("[SS-V24-RPPROBE] V24_PROBE_PLAN listener_port={} candidate_count={} candidates={:?}", tunnel_port, candidates.len(), candidates);
    let per_candidate_timeout = std::time::Duration::from_millis(2000);
    let mut selected: Option<(&'static str, std::net::SocketAddr, tokio::net::TcpStream)> = None;

    for (index, (label, candidate)) in candidates.iter().enumerate() {
        let started = std::time::Instant::now();
        let route_source = v23_route_source(*candidate);
        tracing::error!("[SS-V24-RPPROBE] V24_PROBE_START index={} label={} target={} route_source={} timeout_ms={}", index, label, candidate, route_source, per_candidate_timeout.as_millis());
        match tokio::time::timeout(per_candidate_timeout, tokio::net::TcpStream::connect(*candidate)).await {
            Ok(Ok(stream)) => {
                tracing::error!("[SS-V24-RPPROBE] V24_PROBE_TCP_PASS index={} label={} target={} local={:?} peer={:?} elapsed_ms={}", index, label, candidate, stream.local_addr(), stream.peer_addr(), started.elapsed().as_millis());
                selected = Some((*label, *candidate, stream));
                break;
            }
            Ok(Err(error)) => {
                tracing::error!("[SS-V24-RPPROBE] V24_PROBE_TCP_FAIL index={} label={} target={} kind=socket elapsed_ms={} error={error:?}", index, label, candidate, started.elapsed().as_millis());
            }
            Err(_) => {
                tracing::error!("[SS-V24-RPPROBE] V24_PROBE_TCP_FAIL index={} label={} target={} kind=timeout elapsed_ms={}", index, label, candidate, started.elapsed().as_millis());
            }
        }
    }

    let (selected_label, tunnel_addr, tunnel_stream) = match selected {
        Some(value) => value,
        None => {
            tracing::error!("[SS-V24-RPPROBE] V24_PROBE_EXHAUSTED listener_port={} candidate_count={} candidates={:?}", tunnel_port, candidates.len(), candidates);
            return Err(IdeviceError::InternalError(format!("RP dynamic listener port {tunnel_port} was unreachable on every V24 local candidate")));
        }
    };
    tracing::error!("[SS-V24-RPPROBE] V24_PROBE_SELECTED label={} target={} local={:?} peer={:?}", selected_label, tunnel_addr, tunnel_stream.local_addr(), tunnel_stream.peer_addr());
'''
    tunnel = once(tunnel, old_dynamic, new_dynamic, "V23 dynamic-connect block")
    tunnel_path.write_text(tunnel)

    rp_anchor = '''        let response = self.send_receive_encrypted_request(request).await?;\n        debug!("createListener response: {response:#?}");\n'''
    rp_insert = '''        let response = self.send_receive_encrypted_request(request).await?;\n        debug!("createListener response: {response:#?}");\n        for field in ["address", "listenerAddress", "host", "hostname", "interface", "interfaceName", "port", "listenerPort"] {\n            if let Some(value) = find_in_plist(&response, field) {\n                tracing::error!("[SS-V24-RPPROBE] CREATE_LISTENER_META field={} value={:?}", field, value);\n            }\n        }\n'''
    rp = once(rp, rp_anchor, rp_insert, "createListener metadata diagnostics")
    rp_path.write_text(rp)

    verify(root)
    print("v24 dynamic-listener multi-address probing applied and verified")


if __name__ == "__main__":
    main()
