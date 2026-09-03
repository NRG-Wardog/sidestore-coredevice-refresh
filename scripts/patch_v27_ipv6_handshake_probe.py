#!/usr/bin/env python3
"""Patch pinned SideStore/idevice for V27 IPv6-first end-to-end handshake probing.

Root-Cause Solved:
1. In V26, candidate probing selected the first address that answered TCP SYN (Candidate 4,
   default-route-local-v4: 10.0.0.15), and immediately broke out of the candidate loop.
2. Apple's remotepairingdeviced is an IPv6 listener (local address: ::.0) on Skywalk (fsw_en0).
   When an on-device IPv4-mapped packet arrived, the lower protocol stack failed with:
   setsockopt SO_REUSEADDR failed [42: Protocol not available], and sent close_notify alert.
3. Candidate 5 (default-route-local-v6: [2a06:c701:76ef:de00:c8e4:23fc:e3e7:2f35]) was NEVER tested!
4. V27 fixes this by:
   a) Prioritizing default-route-local-v6 FIRST.
   b) Enumerating all local IPv6 addresses from getifaddrs (including link-local and utun).
   c) Moving the TLS-PSK + CDTunnel handshake (connect_tls_psk_tunnel_native) INSIDE the
      candidate probe loop so that a candidate is only selected if BOTH TCP connect AND
      CDTunnel handshake succeed! If a candidate receives close_notify or reset, the loop
      automatically falls through to test the next candidate!
   d) Keeping standard createListener without restrictive allowLocalConnectionsOnly.
"""

from __future__ import annotations

from pathlib import Path
import sys

MARKER = "[SS-V27-RPPROBE]"


def die(message: str) -> None:
    raise SystemExit(message)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


DEP_GETIFADDRS = r'''fn v27_enumerate_local_ips() -> Vec<(&'static str, std::net::IpAddr)> {
    let mut ips = Vec::new();
    #[cfg(unix)]
    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) == 0 && !head.is_null() {
            let mut cursor = head;
            while !cursor.is_null() {
                let ifa = &*cursor;
                if !ifa.ifa_addr.is_null() {
                    let family = (*ifa.ifa_addr).sa_family as i32;
                    if family == libc::AF_INET6 {
                        let sin6 = &*(ifa.ifa_addr as *const libc::sockaddr_in6);
                        let addr = std::net::Ipv6Addr::from(sin6.sin6_addr.s6_addr);
                        if !addr.is_unspecified() && !addr.is_loopback() && !addr.is_multicast() {
                            ips.push(("ifaddrs-v6", std::net::IpAddr::V6(addr)));
                        }
                    } else if family == libc::AF_INET {
                        let sin = &*(ifa.ifa_addr as *const libc::sockaddr_in);
                        let addr = std::net::Ipv4Addr::from(u32::from_be(sin.sin_addr.s_addr));
                        if !addr.is_unspecified() && !addr.is_loopback() {
                            ips.push(("ifaddrs-v4", std::net::IpAddr::V4(addr)));
                        }
                    }
                }
                cursor = ifa.ifa_next;
            }
            libc::freeifaddrs(head);
        }
    }
    ips
}

'''

def patch_remote_pairing_mod(root: Path) -> None:
    path = root / "idevice" / "src" / "remote_pairing" / "mod.rs"
    if not path.exists():
        die(f"remote_pairing/mod.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if "[SS-V27-RP] CREATE_LISTENER_PEER_INFO_SENT" in text:
        print("v27 remote_pairing/mod.rs already patched")
        return

    new_req = '''        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                        "peerConnectionsInfo": [
                            {
                                "owningPID": std::process::id() as u64,
                                "owningProcessName": "CoreDeviceService"
                            }
                        ],
                        "transportProtocolType": "tcp"
                    }
                }
            }
        });
        tracing::error!("[SS-V27-RP] CREATE_LISTENER_PEER_INFO_SENT owningProcess=CoreDeviceService");'''

    v26_block = '''        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                        "peerConnectionsInfo": [
                            {
                                "owningPID": std::process::id() as u64,
                                "owningProcessName": "CoreDeviceService"
                            }
                        ],
                        "transportProtocolType": "tcp",
                        "allowLocalConnectionsOnly": true
                    }
                }
            }
        });
        tracing::error!("[SS-V26-RP] CREATE_LISTENER_LOCAL_ONLY_SENT allowLocalConnectionsOnly=true");'''

    v25_block = '''        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                        "peerConnectionsInfo": [
                            {
                                "owningPID": std::process::id() as u64,
                                "owningProcessName": "CoreDeviceService"
                            }
                        ],
                        "transportProtocolType": "tcp"
                    }
                }
            }
        });
        tracing::error!("[SS-V25-RP] CREATE_LISTENER_PEER_INFO_SENT owningProcess=CoreDeviceService");'''
    stock_block = '''        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                        "transportProtocolType": "tcp"
                    }
                }
            }
        });'''

    if v26_block in text:
        text = once(text, v26_block, new_req, "createListener request (from V26)")
    elif v25_block in text:
        text = once(text, v25_block, new_req, "createListener request (from V25)")
    elif stock_block in text:
        text = once(text, stock_block, new_req, "createListener request (from stock)")
    else:
        die("Could not find createListener anchor in remote_pairing/mod.rs")

    path.write_text(text, encoding="utf-8")
    print("v27 remote_pairing/mod.rs patch applied")


def patch_tunnel_provider(root: Path) -> None:
    path = root / "ffi" / "src" / "tunnel_provider.rs"
    if not path.exists():
        die(f"tunnel_provider.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("v27 tunnel_provider.rs already patched")
        return

    if "fn v27_enumerate_local_ips" not in text:
        anchor = "fn v24_default_route_source_ip"
        if anchor not in text:
            die("Could not find v24_default_route_source_ip anchor in tunnel_provider.rs")
        dep = DEP_GETIFADDRS
        text = once(text, anchor, dep + anchor, "v27_enumerate_local_ips helper")

    start_anchor = "    let mut base_tunnel_addr = connect_addr;\n    base_tunnel_addr.set_port(tunnel_port);\n"
    if start_anchor not in text:
        die("Could not find base_tunnel_addr anchor in tunnel_provider.rs")

    end_anchor = "    let client_ip: std::net::IpAddr = tunnel.info.client_address.parse()"
    if end_anchor not in text:
        die("Could not find client_ip parse anchor in tunnel_provider.rs")

    start_pos = text.find(start_anchor)
    end_pos = text.find(end_anchor, start_pos)
    if start_pos < 0 or end_pos < 0:
        die("Could not locate candidate connect chunk bounds")

    v27_probe_chunk = r'''    let mut base_tunnel_addr = connect_addr;
    base_tunnel_addr.set_port(tunnel_port);

    let mut candidates: Vec<(&'static str, std::net::SocketAddr)> = Vec::new();
    let mut add_candidate = |label: &'static str, ip: std::net::IpAddr| {
        let addr = std::net::SocketAddr::new(ip, tunnel_port);
        if !candidates.iter().any(|(_, existing)| *existing == addr) {
            candidates.push((label, addr));
        }
    };

    // Priority 1: Default-route IPv6 (native IPv6 on Wi-Fi en0 interface)
    if let Some(ip) = v24_default_route_source_ip(true) {
        add_candidate("default-route-local-v6", ip);
    }
    // Priority 2: All active IPv6 addresses on the device from ifaddrs
    for (label, ip) in v27_enumerate_local_ips() {
        if ip.is_ipv6() {
            add_candidate(label, ip);
        }
    }
    // Priority 3: Default-route IPv4 (Wi-Fi physical IP)
    if let Some(ip) = v24_default_route_source_ip(false) {
        add_candidate("default-route-local-v4", ip);
    }
    // Priority 4: All active IPv4 addresses from ifaddrs
    for (label, ip) in v27_enumerate_local_ips() {
        if ip.is_ipv4() {
            add_candidate(label, ip);
        }
    }
    // Priority 5: Peer reflection (VPN virtual gateway)
    add_candidate("peer-reflection", connect_addr.ip());
    // Priority 6: Local utun route source
    if let Ok(route_source) = v23_route_source(base_tunnel_addr).parse::<std::net::SocketAddr>() {
        add_candidate("local-utun-route-source", route_source.ip());
    }
    // Priority 7: Loopback addresses
    add_candidate("loopback-v6", std::net::IpAddr::V6(std::net::Ipv6Addr::LOCALHOST));
    add_candidate("loopback-v4", std::net::IpAddr::V4(std::net::Ipv4Addr::LOCALHOST));

    tracing::error!("[SS-V27-RPPROBE] V27_PROBE_PLAN listener_port={} candidate_count={} candidates={:?}", tunnel_port, candidates.len(), candidates);
    let per_candidate_tcp_timeout = std::time::Duration::from_millis(2000);
    let per_candidate_hs_timeout = std::time::Duration::from_millis(4000);
    let mut selected_tunnel = None;

    for (index, (label, candidate)) in candidates.iter().enumerate() {
        let started = std::time::Instant::now();
        let route_source = v23_route_source(*candidate);
        tracing::error!("[SS-V27-RPPROBE] V27_PROBE_START index={} label={} target={} route_source={} timeout_ms={}", index, label, candidate, route_source, per_candidate_tcp_timeout.as_millis());
        let stream = match tokio::time::timeout(per_candidate_tcp_timeout, tokio::net::TcpStream::connect(*candidate)).await {
            Ok(Ok(s)) => {
                tracing::error!("[SS-V27-RPPROBE] V27_PROBE_TCP_PASS index={} label={} target={} local={:?} peer={:?} elapsed_ms={}", index, label, candidate, s.local_addr(), s.peer_addr(), started.elapsed().as_millis());
                s
            }
            Ok(Err(error)) => {
                tracing::error!("[SS-V27-RPPROBE] V27_PROBE_TCP_FAIL index={} label={} target={} kind=socket elapsed_ms={} error={error:?}", index, label, candidate, started.elapsed().as_millis());
                continue;
            }
            Err(_) => {
                tracing::error!("[SS-V27-RPPROBE] V27_PROBE_TCP_FAIL index={} label={} target={} kind=timeout elapsed_ms={}", index, label, candidate, started.elapsed().as_millis());
                continue;
            }
        };

        let tls_started = std::time::Instant::now();
        tracing::error!("[SS-V27-RPPROBE] V27_HANDSHAKE_START index={} label={} target={} psk_len={}", index, label, candidate, rpc.encryption_key().len());
        match tokio::time::timeout(per_candidate_hs_timeout, connect_tls_psk_tunnel_native(stream, rpc.encryption_key())).await {
            Ok(Ok(t)) => {
                tracing::error!("[SS-V27-RPPROBE] V27_HANDSHAKE_PASS index={} label={} target={} elapsed_ms={}", index, label, candidate, tls_started.elapsed().as_millis());
                selected_tunnel = Some((*label, *candidate, t));
                break;
            }
            Ok(Err(error)) => {
                tracing::error!("[SS-V27-RPPROBE] V27_HANDSHAKE_FAIL index={} label={} target={} elapsed_ms={} error={error:?}", index, label, candidate, tls_started.elapsed().as_millis());
            }
            Err(_) => {
                tracing::error!("[SS-V27-RPPROBE] V27_HANDSHAKE_FAIL index={} label={} target={} kind=timeout elapsed_ms={}", index, label, candidate, tls_started.elapsed().as_millis());
            }
        }
    }

    let (selected_label, tunnel_addr, tunnel) = match selected_tunnel {
        Some(value) => value,
        None => {
            tracing::error!("[SS-V27-RPPROBE] V27_PROBE_EXHAUSTED listener_port={} candidate_count={}", tunnel_port, candidates.len());
            return Err(IdeviceError::InternalError(format!("RP dynamic CDTunnel handshake failed on every V27 candidate for port {tunnel_port}")));
        }
    };
    tracing::error!("[SS-V27-RPPROBE] V27_TUNNEL_ESTABLISHED label={} target={}", selected_label, tunnel_addr);
'''
    text = text[:start_pos] + v27_probe_chunk + text[end_pos:]
    path.write_text(text, encoding="utf-8")
    print("v27 tunnel_provider.rs patch applied")


def verify(root: Path) -> None:
    t = (root / "ffi" / "src" / "tunnel_provider.rs").read_text(encoding="utf-8")
    r = (root / "idevice" / "src" / "remote_pairing" / "mod.rs").read_text(encoding="utf-8")
    for req in [
        MARKER,
        "v27_enumerate_local_ips",
        "V27_PROBE_PLAN",
        "V27_PROBE_START",
        "V27_HANDSHAKE_START",
        "V27_HANDSHAKE_PASS",
        "V27_TUNNEL_ESTABLISHED",
        "default-route-local-v6",
        "ifaddrs-v6",
    ]:
        if req not in t:
            die(f"tunnel_provider.rs missing: {req}")

    if "[SS-V27-RP] CREATE_LISTENER_PEER_INFO_SENT" not in r:
        die("remote_pairing/mod.rs missing V27 marker")
    if "allowLocalConnectionsOnly" in r:
        die("remote_pairing/mod.rs must not contain allowLocalConnectionsOnly")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v27_ipv6_handshake_probe.py <idevice-root>")
    root = Path(sys.argv[1])
    patch_remote_pairing_mod(root)
    patch_tunnel_provider(root)
    verify(root)
    print("v27 IPv6-first end-to-end handshake probe patch verified successfully")


if __name__ == "__main__":
    main()
