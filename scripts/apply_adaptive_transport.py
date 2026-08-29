#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_adaptive_transport.py <tunnel_provider.rs>")

p = Path(sys.argv[1])
s = p.read_text()

marker = "[SS-ADAPT] adaptive transport engine active"
if marker in s:
    required = [
        "[SS-IFACE]",
        "[SS-ADAPT] control candidate START",
        "[SS-ADAPT] dynamic candidate START",
        "[SS-ADAPT] TCP CONNECTED",
        "[SS-ADAPT] TLS SUCCESS",
        "[SS-ADAPT] RSD handshake SUCCESS",
        "[SS-ADAPT] all dynamic paths failed",
        "[SS-ADAPT] all control paths failed",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"Adaptive transport marker present but patch incomplete; missing: {missing}")
    print("Adaptive transport patch already present and verified")
    raise SystemExit(0)

if "[SS-DIAG] finish_tunnel START" not in s:
    raise SystemExit("Base deep diagnostic patch must be applied before adaptive transport patch")

shared_marker = "/// Shared logic: given a connected & paired `RemotePairingClient`, create"
insert_at = s.find(shared_marker)
if insert_at < 0:
    raise SystemExit("Could not locate shared tunnel helper insertion point")

helpers = r'''// Comprehensive SideStore transport diagnostics/fallbacks.
// Enumerate every local interface so the dynamic listener can be tested on the
// reflected WireGuard peer, the local utun address, Wi-Fi/cellular/bridge IPs,
// loopback, and scoped IPv6 addresses without requiring another build.
fn ss_push_unique_candidate(
    out: &mut Vec<(String, std::net::SocketAddr)>,
    label: String,
    addr: std::net::SocketAddr,
) {
    if !out.iter().any(|(_, existing)| *existing == addr) {
        out.push((label, addr));
    }
}

fn ss_iface_priority(label: &str, addr: &std::net::SocketAddr) -> u8 {
    if addr.ip().is_loopback() {
        return 90;
    }
    if label.contains("en0") || label.contains("iface-en") {
        return 0;
    }
    if label.contains("bridge") {
        return 1;
    }
    if label.contains("pdp_ip") {
        return 2;
    }
    if label.contains("awdl") || label.contains("llw") {
        return 4;
    }
    if label.contains("utun") {
        return 20;
    }
    10
}

fn ss_local_interface_candidates(port: u16) -> Vec<(String, std::net::SocketAddr)> {
    let mut out: Vec<(String, std::net::SocketAddr)> = Vec::new();

    #[cfg(unix)]
    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) != 0 {
            tracing::error!(
                "[SS-IFACE] getifaddrs FAILED errno={}",
                std::io::Error::last_os_error()
            );
        } else {
            let mut cur = head;
            while !cur.is_null() {
                let ifa = &*cur;
                if !ifa.ifa_addr.is_null() {
                    let name = if ifa.ifa_name.is_null() {
                        String::from("unknown")
                    } else {
                        std::ffi::CStr::from_ptr(ifa.ifa_name)
                            .to_string_lossy()
                            .into_owned()
                    };
                    let family = (*ifa.ifa_addr).sa_family as i32;

                    if family == libc::AF_INET {
                        let sin = &*(ifa.ifa_addr as *const libc::sockaddr_in);
                        let ip = std::net::Ipv4Addr::from(u32::from_be(sin.sin_addr.s_addr));
                        if !ip.is_unspecified() && !ip.is_multicast() {
                            ss_push_unique_candidate(
                                &mut out,
                                format!("iface-{name}-v4"),
                                std::net::SocketAddr::V4(std::net::SocketAddrV4::new(ip, port)),
                            );
                        }
                    } else if family == libc::AF_INET6 {
                        let sin6 = &*(ifa.ifa_addr as *const libc::sockaddr_in6);
                        let ip = std::net::Ipv6Addr::from(sin6.sin6_addr.s6_addr);
                        if !ip.is_unspecified() && !ip.is_multicast() {
                            ss_push_unique_candidate(
                                &mut out,
                                format!("iface-{name}-v6"),
                                std::net::SocketAddr::V6(std::net::SocketAddrV6::new(
                                    ip,
                                    port,
                                    sin6.sin6_flowinfo,
                                    sin6.sin6_scope_id,
                                )),
                            );
                        }
                    }
                }
                cur = ifa.ifa_next;
            }
            libc::freeifaddrs(head);
        }
    }

    out.sort_by_key(|(label, addr)| ss_iface_priority(label, addr));
    for (label, addr) in &out {
        tracing::error!("[SS-IFACE] candidate label={} addr={}", label, addr);
    }
    out
}

fn ss_control_candidates(configured: std::net::SocketAddr) -> Vec<(String, std::net::SocketAddr)> {
    let mut out = Vec::new();
    ss_push_unique_candidate(&mut out, String::from("configured-control"), configured);

    // Probe all local interface addresses on the fixed RemotePairing port. This
    // directly tests the hypothesis that createListener is scoped to the
    // physical control-flow interface rather than the reflected utun flow.
    for (label, addr) in ss_local_interface_candidates(configured.port()) {
        ss_push_unique_candidate(&mut out, format!("control-{label}"), addr);
    }

    if let std::net::SocketAddr::V4(v4) = configured {
        let o = v4.ip().octets();
        if o[0] == 10 && o[1] == 7 && o[2] == 0 {
            ss_push_unique_candidate(
                &mut out,
                String::from("control-known-utun-local"),
                std::net::SocketAddr::V4(std::net::SocketAddrV4::new(
                    std::net::Ipv4Addr::new(10, 7, 0, 10),
                    configured.port(),
                )),
            );
        }
    }

    ss_push_unique_candidate(
        &mut out,
        String::from("control-loopback-v4"),
        std::net::SocketAddr::V4(std::net::SocketAddrV4::new(
            std::net::Ipv4Addr::LOCALHOST,
            configured.port(),
        )),
    );
    ss_push_unique_candidate(
        &mut out,
        String::from("control-loopback-v6"),
        std::net::SocketAddr::V6(std::net::SocketAddrV6::new(
            std::net::Ipv6Addr::LOCALHOST,
            configured.port(),
            0,
            0,
        )),
    );
    out
}

fn ss_dynamic_candidates(control_addr: std::net::SocketAddr) -> Vec<(String, std::net::SocketAddr)> {
    let mut out = Vec::new();
    let mut same = control_addr;
    same.set_port(0);
    ss_push_unique_candidate(&mut out, String::from("control-same-ip"), same);

    for (label, mut addr) in ss_local_interface_candidates(0) {
        addr.set_port(0);
        ss_push_unique_candidate(&mut out, format!("dynamic-{label}"), addr);
    }

    if let std::net::SocketAddr::V4(v4) = control_addr {
        let o = v4.ip().octets();
        if o[0] == 10 && o[1] == 7 && o[2] == 0 {
            ss_push_unique_candidate(
                &mut out,
                String::from("dynamic-known-utun-local"),
                std::net::SocketAddr::V4(std::net::SocketAddrV4::new(
                    std::net::Ipv4Addr::new(10, 7, 0, 10),
                    0,
                )),
            );
        }
    }

    ss_push_unique_candidate(
        &mut out,
        String::from("dynamic-loopback-v4"),
        std::net::SocketAddr::V4(std::net::SocketAddrV4::new(
            std::net::Ipv4Addr::LOCALHOST,
            0,
        )),
    );
    ss_push_unique_candidate(
        &mut out,
        String::from("dynamic-loopback-v6"),
        std::net::SocketAddr::V6(std::net::SocketAddrV6::new(
            std::net::Ipv6Addr::LOCALHOST,
            0,
            0,
            0,
        )),
    );
    out
}

'''

s = s[:insert_at] + helpers + s[insert_at:]

finish_start = s.find("async fn finish_tunnel(")
finish_end = s.find("\nfn write_result(", finish_start)
if finish_start < 0 or finish_end < 0:
    raise SystemExit("Could not locate finish_tunnel function bounds")

adaptive_finish = r'''async fn finish_tunnel(
    rpc: &mut idevice::remote_pairing::RemotePairingClient<
        impl idevice::remote_pairing::RpPairingSocketProvider,
    >,
    connect_addr: std::net::SocketAddr,
) -> Result<(idevice::tcp::handle::AdapterHandle, RsdHandshake), IdeviceError> {
    use idevice::remote_pairing::connect_tls_psk_tunnel_native;

    tracing::error!(
        "[SS-ADAPT] adaptive transport engine active control_target={}",
        connect_addr
    );

    let candidates = ss_dynamic_candidates(connect_addr);
    tracing::error!(
        "[SS-ADAPT] dynamic candidate set control_target={} count={}",
        connect_addr,
        candidates.len()
    );

    let mut outcomes: Vec<String> = Vec::new();

    for (label, mut target) in candidates {
        tracing::error!(
            "[SS-ADAPT] dynamic candidate START label={} base_target={}",
            label,
            target
        );

        // Fresh listener per candidate: if one path accepts TCP but TLS/RSD fails,
        // the next path is tested against a new one-shot listener rather than a
        // potentially consumed socket.
        let tunnel_port = match tokio::time::timeout(
            std::time::Duration::from_secs(2),
            rpc.create_tcp_listener(),
        )
        .await
        {
            Ok(Ok(port)) => {
                tracing::error!(
                    "[SS-ADAPT] createListener SUCCESS label={} port={}",
                    label,
                    port
                );
                port
            }
            Ok(Err(e)) => {
                let summary = format!("{label}=CREATE_LISTENER_FAILED({e:?})");
                tracing::error!("[SS-ADAPT] {}", summary);
                outcomes.push(summary);
                continue;
            }
            Err(_) => {
                let summary = format!("{label}=CREATE_LISTENER_TIMEOUT");
                tracing::error!("[SS-ADAPT] {}", summary);
                outcomes.push(summary);
                continue;
            }
        };
        target.set_port(tunnel_port);

        // Small readiness allowance followed by two bounded TCP attempts. This
        // covers listener startup races without repeating the old multi-second wait.
        tokio::time::sleep(std::time::Duration::from_millis(40)).await;
        let mut stream_opt = None;
        let mut tcp_last = String::from("no-attempt");
        for attempt in 1..=2u32 {
            tracing::error!(
                "[SS-ADAPT] TCP START label={} attempt={}/2 target={}",
                label,
                attempt,
                target
            );
            match tokio::time::timeout(
                std::time::Duration::from_millis(450),
                tokio::net::TcpStream::connect(target),
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
                        "[SS-ADAPT] TCP CONNECTED label={} attempt={} local={} peer={}",
                        label,
                        attempt,
                        local,
                        peer
                    );
                    stream_opt = Some(stream);
                    break;
                }
                Ok(Err(e)) => {
                    tcp_last = format!("socket:{:?}:{e}", e.kind());
                    tracing::error!(
                        "[SS-ADAPT] TCP FAILED label={} attempt={} kind={:?} error={}",
                        label,
                        attempt,
                        e.kind(),
                        e
                    );
                }
                Err(_) => {
                    tcp_last = String::from("timeout-450ms");
                    tracing::error!(
                        "[SS-ADAPT] TCP TIMEOUT label={} attempt={} target={}",
                        label,
                        attempt,
                        target
                    );
                }
            }
            tokio::time::sleep(std::time::Duration::from_millis(70)).await;
        }

        let tunnel_stream = match stream_opt {
            Some(stream) => stream,
            None => {
                outcomes.push(format!("{label}=TCP_FAILED({tcp_last})@{target}"));
                continue;
            }
        };

        tracing::error!(
            "[SS-ADAPT] TLS START label={} target={} port={}",
            label,
            target,
            tunnel_port
        );
        let tunnel = match tokio::time::timeout(
            std::time::Duration::from_secs(5),
            connect_tls_psk_tunnel_native(tunnel_stream, rpc.encryption_key()),
        )
        .await
        {
            Ok(Ok(tunnel)) => {
                tracing::error!(
                    "[SS-ADAPT] TLS SUCCESS label={} target={}",
                    label,
                    target
                );
                tunnel
            }
            Ok(Err(e)) => {
                tracing::error!(
                    "[SS-ADAPT] TLS FAILED label={} target={} error={:?}",
                    label,
                    target,
                    e
                );
                outcomes.push(format!("{label}=TLS_FAILED({e:?})@{target}"));
                continue;
            }
            Err(_) => {
                tracing::error!(
                    "[SS-ADAPT] TLS TIMEOUT label={} target={} timeout=5s",
                    label,
                    target
                );
                outcomes.push(format!("{label}=TLS_TIMEOUT@{target}"));
                continue;
            }
        };

        tracing::error!(
            "[SS-ADAPT] tunnel info label={} client_address={} server_address={} mtu={} server_rsd_port={}",
            label,
            tunnel.info.client_address,
            tunnel.info.server_address,
            tunnel.info.mtu,
            tunnel.info.server_rsd_port
        );

        let client_ip: std::net::IpAddr = match tunnel.info.client_address.parse() {
            Ok(ip) => ip,
            Err(e) => {
                tracing::error!(
                    "[SS-ADAPT] client_address parse FAILED label={} value={} error={}",
                    label,
                    tunnel.info.client_address,
                    e
                );
                outcomes.push(format!("{label}=CLIENT_IP_PARSE_FAILED({e})"));
                continue;
            }
        };
        let server_ip: std::net::IpAddr = match tunnel.info.server_address.parse() {
            Ok(ip) => ip,
            Err(e) => {
                tracing::error!(
                    "[SS-ADAPT] server_address parse FAILED label={} value={} error={}",
                    label,
                    tunnel.info.server_address,
                    e
                );
                outcomes.push(format!("{label}=SERVER_IP_PARSE_FAILED({e})"));
                continue;
            }
        };
        let mtu = tunnel.info.mtu as usize;
        let rsd_port = tunnel.info.server_rsd_port;
        let mss = mtu.saturating_sub(60);

        let raw = tunnel.into_inner();
        let mut adapter = idevice::tcp::adapter::Adapter::new(Box::new(raw), client_ip, server_ip);
        adapter.set_mss(mss);
        let mut adapter = adapter.to_async_handle();

        tracing::error!(
            "[SS-ADAPT] adapter READY label={} client_ip={} server_ip={} mtu={} mss={} rsd_port={}",
            label,
            client_ip,
            server_ip,
            mtu,
            mss,
            rsd_port
        );

        // RSD is the next plausible transient point. Retry complete RSD connects
        // and handshakes three times before abandoning a transport candidate.
        let mut rsd_last = String::from("no-attempt");
        for rsd_attempt in 1..=3u32 {
            tracing::error!(
                "[SS-ADAPT] RSD connect START label={} attempt={}/3 server_ip={} port={}",
                label,
                rsd_attempt,
                server_ip,
                rsd_port
            );
            let rsd_stream = match tokio::time::timeout(
                std::time::Duration::from_secs(3),
                adapter.connect(rsd_port),
            )
            .await
            {
                Ok(Ok(stream)) => {
                    tracing::error!(
                        "[SS-ADAPT] RSD connect SUCCESS label={} attempt={}",
                        label,
                        rsd_attempt
                    );
                    stream
                }
                Ok(Err(e)) => {
                    rsd_last = format!("connect:{e:?}");
                    tracing::error!(
                        "[SS-ADAPT] RSD connect FAILED label={} attempt={} error={:?}",
                        label,
                        rsd_attempt,
                        e
                    );
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                    continue;
                }
                Err(_) => {
                    rsd_last = String::from("connect-timeout-3s");
                    tracing::error!(
                        "[SS-ADAPT] RSD connect TIMEOUT label={} attempt={} timeout=3s",
                        label,
                        rsd_attempt
                    );
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                    continue;
                }
            };

            tracing::error!(
                "[SS-ADAPT] RSD handshake START label={} attempt={}",
                label,
                rsd_attempt
            );
            match tokio::time::timeout(
                std::time::Duration::from_secs(4),
                RsdHandshake::new(rsd_stream),
            )
            .await
            {
                Ok(Ok(handshake)) => {
                    tracing::error!(
                        "[SS-ADAPT] RSD handshake SUCCESS label={} attempt={} services={}",
                        label,
                        rsd_attempt,
                        handshake.services.len()
                    );
                    tracing::error!(
                        "[SS-ADAPT] TRANSPORT SUCCESS label={} target={} control_target={}",
                        label,
                        target,
                        connect_addr
                    );
                    return Ok((adapter, handshake));
                }
                Ok(Err(e)) => {
                    rsd_last = format!("handshake:{e:?}");
                    tracing::error!(
                        "[SS-ADAPT] RSD handshake FAILED label={} attempt={} error={:?}",
                        label,
                        rsd_attempt,
                        e
                    );
                }
                Err(_) => {
                    rsd_last = String::from("handshake-timeout-4s");
                    tracing::error!(
                        "[SS-ADAPT] RSD handshake TIMEOUT label={} attempt={} timeout=4s",
                        label,
                        rsd_attempt
                    );
                }
            }
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        }

        outcomes.push(format!("{label}=RSD_FAILED({rsd_last})@{target}"));
    }

    let summary = outcomes.join("; ");
    Err(IdeviceError::InternalError(format!(
        "[SS-ADAPT] all dynamic paths failed control_target={connect_addr}: {summary}"
    )))
}
'''

s = s[:finish_start] + adaptive_finish + s[finish_end:]

fn_start = s.find('pub unsafe extern "C" fn tunnel_create_rppairing(')
if fn_start < 0:
    raise SystemExit("Could not locate tunnel_create_rppairing")
res_start = s.find("    let res = run_sync_local(async {", fn_start)
match_start = s.find("\n    match res {", res_start)
if res_start < 0 or match_start < 0:
    raise SystemExit("Could not locate raw RPPairing run_sync_local block")

old_control = s[res_start:match_start]
if "RPPairing control TCP connect START" not in old_control:
    raise SystemExit("Refusing to replace unexpected raw RPPairing control block")

adaptive_control = r'''    let res = run_sync_local(async {
        let candidates = ss_control_candidates(socket_addr);
        tracing::error!(
            "[SS-ADAPT] control candidate set configured={} count={}",
            socket_addr,
            candidates.len()
        );
        let mut outcomes: Vec<String> = Vec::new();

        for (label, control_addr) in candidates {
            tracing::error!(
                "[SS-ADAPT] control candidate START label={} target={}",
                label,
                control_addr
            );

            let mut control_stream = None;
            let attempts = if label == "configured-control" { 2u32 } else { 1u32 };
            let mut last = String::from("no-attempt");
            for attempt in 1..=attempts {
                match tokio::time::timeout(
                    std::time::Duration::from_millis(700),
                    tokio::net::TcpStream::connect(control_addr),
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
                            "[SS-ADAPT] control TCP CONNECTED label={} attempt={} local={} peer={}",
                            label,
                            attempt,
                            local,
                            peer
                        );
                        control_stream = Some(stream);
                        break;
                    }
                    Ok(Err(e)) => {
                        last = format!("socket:{:?}:{e}", e.kind());
                        tracing::error!(
                            "[SS-ADAPT] control TCP FAILED label={} attempt={} kind={:?} error={}",
                            label,
                            attempt,
                            e.kind(),
                            e
                        );
                    }
                    Err(_) => {
                        last = String::from("timeout-700ms");
                        tracing::error!(
                            "[SS-ADAPT] control TCP TIMEOUT label={} attempt={} target={}",
                            label,
                            attempt,
                            control_addr
                        );
                    }
                }
                tokio::time::sleep(std::time::Duration::from_millis(80)).await;
            }

            let stream = match control_stream {
                Some(stream) => stream,
                None => {
                    outcomes.push(format!("{label}=CONTROL_TCP_FAILED({last})@{control_addr}"));
                    continue;
                }
            };

            let conn = RpPairingSocket::new(stream);
            let mut rpc = RemotePairingClient::new(conn, &host);

            tracing::error!(
                "[SS-ADAPT] pair-verify/connect START label={} target={}",
                label,
                control_addr
            );
            match tokio::time::timeout(
                std::time::Duration::from_secs(5),
                rpc.connect(rpf, async || get_pin(pin_callback, &ctx)),
            )
            .await
            {
                Ok(Ok(())) => {
                    tracing::error!(
                        "[SS-ADAPT] pair-verify/connect SUCCESS label={} target={}",
                        label,
                        control_addr
                    );
                }
                Ok(Err(e)) => {
                    tracing::error!(
                        "[SS-ADAPT] pair-verify/connect FAILED label={} target={} error={:?}",
                        label,
                        control_addr,
                        e
                    );
                    outcomes.push(format!("{label}=PAIR_FAILED({e:?})@{control_addr}"));
                    continue;
                }
                Err(_) => {
                    tracing::error!(
                        "[SS-ADAPT] pair-verify/connect TIMEOUT label={} target={} timeout=5s",
                        label,
                        control_addr
                    );
                    outcomes.push(format!("{label}=PAIR_TIMEOUT@{control_addr}"));
                    continue;
                }
            }

            match finish_tunnel(&mut rpc, control_addr).await {
                Ok(result) => {
                    tracing::error!(
                        "[SS-ADAPT] full control path SUCCESS label={} target={}",
                        label,
                        control_addr
                    );
                    return Ok(result);
                }
                Err(e) => {
                    tracing::error!(
                        "[SS-ADAPT] full control path FAILED label={} target={} error={:?}",
                        label,
                        control_addr,
                        e
                    );
                    outcomes.push(format!("{label}=TUNNEL_FAILED({e:?})@{control_addr}"));
                }
            }
        }

        Err(IdeviceError::InternalError(format!(
            "[SS-ADAPT] all control paths failed configured={socket_addr}: {}",
            outcomes.join("; ")
        )))
    });'''

s = s[:res_start] + adaptive_control + s[match_start:]

required = [
    marker,
    "fn ss_local_interface_candidates",
    "[SS-IFACE] candidate",
    "[SS-ADAPT] control candidate START",
    "[SS-ADAPT] control TCP CONNECTED",
    "[SS-ADAPT] pair-verify/connect SUCCESS",
    "[SS-ADAPT] dynamic candidate START",
    "[SS-ADAPT] createListener SUCCESS",
    "[SS-ADAPT] TCP CONNECTED",
    "[SS-ADAPT] TLS SUCCESS",
    "[SS-ADAPT] RSD connect SUCCESS",
    "[SS-ADAPT] RSD handshake SUCCESS",
    "[SS-ADAPT] TRANSPORT SUCCESS",
    "[SS-ADAPT] all dynamic paths failed",
    "[SS-ADAPT] all control paths failed",
]
missing = [x for x in required if x not in s]
if missing:
    raise SystemExit(f"Adaptive transport verification failed; missing: {missing}")

# The adaptive function replaces the old fixed three-path engine. Keeping both
# would duplicate probes and make logs/results ambiguous.
for forbidden in [
    "[SS-MPATH] probing dynamic listener candidates",
    "utun-local-10.7.0.10",
]:
    if forbidden in s:
        raise SystemExit(f"Adaptive transport verification failed: stale multipath marker remains: {forbidden}")

p.write_text(s)
print("Comprehensive adaptive transport engine applied and verified")
