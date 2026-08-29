from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_diag_patch.py <tunnel_provider.rs>")

p = Path(sys.argv[1])
s = p.read_text()

old = '''    let tunnel_port = rpc.create_tcp_listener().await?;
    let mut tunnel_addr = connect_addr;
    tunnel_addr.set_port(tunnel_port);
    let tunnel_stream = run_global_timeout(|| tokio::net::TcpStream::connect(tunnel_addr))
        .await
        .map_err(|e| IdeviceError::InternalError(format!("TLS tunnel: {e}")))?;
    let tunnel = connect_tls_psk_tunnel_native(tunnel_stream, rpc.encryption_key()).await?;

    let client_ip: std::net::IpAddr = tunnel
        .info
        .client_address
        .parse()
        .map_err(|e| IdeviceError::InternalError(format!("{e}")))?;
    let server_ip: std::net::IpAddr = tunnel
        .info
        .server_address
        .parse()
        .map_err(|e| IdeviceError::InternalError(format!("{e}")))?;
    let mtu = tunnel.info.mtu as usize;
    let rsd_port = tunnel.info.server_rsd_port;

    let raw = tunnel.into_inner();
    let mut adapter = idevice::tcp::adapter::Adapter::new(Box::new(raw), client_ip, server_ip);
    adapter.set_mss(mtu.saturating_sub(60));
    let mut adapter = adapter.to_async_handle();

    let rsd_stream = adapter
        .connect(rsd_port)
        .await
        .map_err(|e| IdeviceError::InternalError(format!("{e}")))?;
    let handshake = RsdHandshake::new(rsd_stream).await?;

    Ok((adapter, handshake))'''

new = '''    tracing::error!(
        "[SS-DIAG] finish_tunnel START control_target={}",
        connect_addr
    );
    tracing::error!("[SS-DIAG] requesting create_tcp_listener");

    let tunnel_port = match rpc.create_tcp_listener().await {
        Ok(port) => {
            tracing::error!("[SS-DIAG] create_tcp_listener SUCCESS port={}", port);
            port
        }
        Err(e) => {
            return Err(IdeviceError::InternalError(format!(
                "[SS-DIAG] create_tcp_listener FAILED control_target={connect_addr} error={e:?}"
            )));
        }
    };

    let mut tunnel_addr = connect_addr;
    tunnel_addr.set_port(tunnel_port);

    tracing::error!(
        "[SS-DIAG] create_tcp_listener returned port={} target={}",
        tunnel_port,
        tunnel_addr
    );

    let mut tunnel_stream = None;
    let mut connected_attempt: Option<u32> = None;
    let mut last_error = String::from("no attempt made");

    for attempt in 1..=12u32 {
        tracing::error!(
            "[SS-DIAG] dynamic TCP connect attempt {}/12 -> {}",
            attempt,
            tunnel_addr
        );

        match tokio::time::timeout(
            std::time::Duration::from_millis(250),
            tokio::net::TcpStream::connect(tunnel_addr),
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
                    "[SS-DIAG] dynamic TCP CONNECTED attempt={} local={} peer={}",
                    attempt,
                    local,
                    peer
                );
                connected_attempt = Some(attempt);
                tunnel_stream = Some(stream);
                break;
            }
            Ok(Err(e)) => {
                last_error = format!("socket error: {e}");
                tracing::error!(
                    "[SS-DIAG] attempt {} socket error kind={:?} error={}",
                    attempt,
                    e.kind(),
                    e
                );
            }
            Err(_) => {
                last_error = String::from("250ms per-attempt timeout");
                tracing::error!(
                    "[SS-DIAG] attempt {} timed out after 250ms",
                    attempt
                );
            }
        }

        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }

    let tunnel_stream = tunnel_stream.ok_or_else(|| {
        IdeviceError::InternalError(format!(
            "[SS-DIAG] dynamic TCP failed: target={tunnel_addr}, port={tunnel_port}, attempts=12, last={last_error}"
        ))
    })?;
    let connected_attempt = connected_attempt.unwrap_or(0);

    tracing::error!(
        "[SS-DIAG] TLS-PSK handshake START target={} connected_attempt={}",
        tunnel_addr,
        connected_attempt
    );

    let tunnel = match tokio::time::timeout(
        std::time::Duration::from_secs(5),
        connect_tls_psk_tunnel_native(tunnel_stream, rpc.encryption_key()),
    )
    .await
    {
        Ok(Ok(tunnel)) => {
            tracing::error!(
                "[SS-DIAG] TLS-PSK/CDTunnel handshake SUCCESS target={}",
                tunnel_addr
            );
            tunnel
        }
        Ok(Err(e)) => {
            return Err(IdeviceError::InternalError(format!(
                "[SS-DIAG] TLS-PSK/CDTunnel FAILED target={tunnel_addr} port={tunnel_port} connected_attempt={connected_attempt} error={e:?}"
            )));
        }
        Err(_) => {
            return Err(IdeviceError::InternalError(format!(
                "[SS-DIAG] TLS-PSK/CDTunnel TIMEOUT target={tunnel_addr} port={tunnel_port} connected_attempt={connected_attempt} timeout=5s"
            )));
        }
    };

    tracing::error!(
        "[SS-DIAG] tunnel info client_address={} server_address={} mtu={} server_rsd_port={}",
        tunnel.info.client_address,
        tunnel.info.server_address,
        tunnel.info.mtu,
        tunnel.info.server_rsd_port
    );

    let client_ip: std::net::IpAddr = tunnel
        .info
        .client_address
        .parse()
        .map_err(|e| IdeviceError::InternalError(format!(
            "[SS-DIAG] client_address parse FAILED value={} error={e}",
            tunnel.info.client_address
        )))?;
    let server_ip: std::net::IpAddr = tunnel
        .info
        .server_address
        .parse()
        .map_err(|e| IdeviceError::InternalError(format!(
            "[SS-DIAG] server_address parse FAILED value={} error={e}",
            tunnel.info.server_address
        )))?;
    let mtu = tunnel.info.mtu as usize;
    let rsd_port = tunnel.info.server_rsd_port;
    let mss = mtu.saturating_sub(60);

    tracing::error!(
        "[SS-DIAG] creating userspace TCP adapter client_ip={} server_ip={} mtu={} mss={} rsd_port={}",
        client_ip,
        server_ip,
        mtu,
        mss,
        rsd_port
    );

    let raw = tunnel.into_inner();
    let mut adapter = idevice::tcp::adapter::Adapter::new(Box::new(raw), client_ip, server_ip);
    adapter.set_mss(mss);
    let mut adapter = adapter.to_async_handle();

    tracing::error!("[SS-DIAG] inner RSD TCP connect START server_ip={} port={}", server_ip, rsd_port);
    let rsd_stream = match tokio::time::timeout(
        std::time::Duration::from_secs(5),
        adapter.connect(rsd_port),
    )
    .await
    {
        Ok(Ok(stream)) => {
            tracing::error!("[SS-DIAG] inner RSD TCP connect SUCCESS port={}", rsd_port);
            stream
        }
        Ok(Err(e)) => {
            return Err(IdeviceError::InternalError(format!(
                "[SS-DIAG] inner RSD TCP connect FAILED server_ip={server_ip} port={rsd_port} error={e:?}"
            )));
        }
        Err(_) => {
            return Err(IdeviceError::InternalError(format!(
                "[SS-DIAG] inner RSD TCP connect TIMEOUT server_ip={server_ip} port={rsd_port} timeout=5s"
            )));
        }
    };

    tracing::error!("[SS-DIAG] RSD handshake START port={}", rsd_port);
    let handshake = match tokio::time::timeout(
        std::time::Duration::from_secs(5),
        RsdHandshake::new(rsd_stream),
    )
    .await
    {
        Ok(Ok(handshake)) => {
            tracing::error!(
                "[SS-DIAG] RSD handshake SUCCESS services={}",
                handshake.services.len()
            );
            handshake
        }
        Ok(Err(e)) => {
            return Err(IdeviceError::InternalError(format!(
                "[SS-DIAG] RSD handshake FAILED port={rsd_port} error={e:?}"
            )));
        }
        Err(_) => {
            return Err(IdeviceError::InternalError(format!(
                "[SS-DIAG] RSD handshake TIMEOUT port={rsd_port} timeout=5s"
            )));
        }
    };

    tracing::error!("[SS-DIAG] finish_tunnel SUCCESS");
    Ok((adapter, handshake))'''

if "[SS-DIAG] finish_tunnel START" in s:
    print("Deep diagnostic patch already present")
    raise SystemExit(0)

if old not in s:
    marker = "let tunnel_port = rpc.create_tcp_listener().await?;"
    idx = s.find(marker)
    if idx >= 0:
        print(s[max(0, idx - 300):idx + 1800])
    raise SystemExit("Could not locate stock finish_tunnel block")

s = s.replace(old, new, 1)

control_old = '''    let res = run_sync_local(async {
        // Connect directly and use raw RPPairing protocol
        let stream = run_global_timeout(|| tokio::net::TcpStream::connect(socket_addr))
            .await
            .map_err(|e| IdeviceError::InternalError(format!("connect: {e}")))?;
        let conn = RpPairingSocket::new(stream);

        let mut rpc = RemotePairingClient::new(conn, &host);
        rpc.connect(rpf, async || get_pin(pin_callback, &ctx))
            .await?;

        finish_tunnel(&mut rpc, socket_addr).await
    });'''

control_new = '''    let res = run_sync_local(async {
        // Connect directly and use raw RPPairing protocol
        tracing::error!(
            "[SS-DIAG] RPPairing control TCP connect START target={} host_label_len={}",
            socket_addr,
            host.len()
        );
        let stream = run_global_timeout(|| tokio::net::TcpStream::connect(socket_addr))
            .await
            .map_err(|e| IdeviceError::InternalError(format!(
                "[SS-DIAG] RPPairing control TCP connect FAILED target={socket_addr} error={e}"
            )))?;

        let local = stream
            .local_addr()
            .map(|a| a.to_string())
            .unwrap_or_else(|e| format!("<local_addr error: {e}>"));
        let peer = stream
            .peer_addr()
            .map(|a| a.to_string())
            .unwrap_or_else(|e| format!("<peer_addr error: {e}>"));
        tracing::error!(
            "[SS-DIAG] RPPairing control TCP CONNECTED local={} peer={}",
            local,
            peer
        );

        let conn = RpPairingSocket::new(stream);
        let mut rpc = RemotePairingClient::new(conn, &host);

        tracing::error!("[SS-DIAG] RPPairing pair-verify/connect START");
        if let Err(e) = rpc.connect(rpf, async || get_pin(pin_callback, &ctx)).await {
            return Err(IdeviceError::InternalError(format!(
                "[SS-DIAG] RPPairing pair-verify/connect FAILED error={e:?}"
            )));
        }
        tracing::error!("[SS-DIAG] RPPairing pair-verify/connect SUCCESS");

        finish_tunnel(&mut rpc, socket_addr).await
    });'''

if control_old not in s:
    raise SystemExit("Could not locate raw RPPairing control block")
s = s.replace(control_old, control_new, 1)

p.write_text(s)

required = [
    "[SS-DIAG] RPPairing control TCP connect START",
    "[SS-DIAG] RPPairing pair-verify/connect SUCCESS",
    "[SS-DIAG] create_tcp_listener returned",
    "[SS-DIAG] dynamic TCP connect attempt",
    "[SS-DIAG] dynamic TCP CONNECTED",
    "[SS-DIAG] TLS-PSK/CDTunnel",
    "[SS-DIAG] tunnel info",
    "[SS-DIAG] inner RSD TCP connect",
    "[SS-DIAG] RSD handshake",
    "[SS-DIAG] finish_tunnel SUCCESS",
]

patched = p.read_text()
missing = [marker for marker in required if marker not in patched]
if missing:
    raise SystemExit(f"Patch verification failed; missing: {missing}")

print("Deep IDevice diagnostic patch applied and verified")
