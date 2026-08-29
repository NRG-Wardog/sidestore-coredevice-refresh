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
    let tunnel = connect_tls_psk_tunnel_native(tunnel_stream, rpc.encryption_key()).await?;'''

new = '''    let tunnel_port = rpc.create_tcp_listener().await?;
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
                tracing::error!(
                    "[SS-DIAG] dynamic TCP CONNECTED on attempt {} -> {}",
                    attempt,
                    tunnel_addr
                );
                connected_attempt = Some(attempt);
                tunnel_stream = Some(stream);
                break;
            }
            Ok(Err(e)) => {
                last_error = format!("socket error: {e}");
                tracing::error!(
                    "[SS-DIAG] attempt {} socket error: {}",
                    attempt,
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
        "[SS-DIAG] starting TLS-PSK handshake on {} after TCP attempt {}",
        tunnel_addr,
        connected_attempt
    );

    let tunnel = match connect_tls_psk_tunnel_native(tunnel_stream, rpc.encryption_key()).await {
        Ok(tunnel) => {
            tracing::error!(
                "[SS-DIAG] TLS-PSK/CDTunnel handshake SUCCESS on {} after TCP attempt {}",
                tunnel_addr,
                connected_attempt
            );
            tunnel
        }
        Err(e) => {
            return Err(IdeviceError::InternalError(format!(
                "[SS-DIAG] TLS-PSK/CDTunnel failed: target={tunnel_addr}, port={tunnel_port}, connected_attempt={connected_attempt}, error={e:?}"
            )));
        }
    };'''

if "[SS-DIAG] create_tcp_listener returned" in s:
    print("Diagnostic patch already present")
    raise SystemExit(0)

if old not in s:
    marker = "let tunnel_port = rpc.create_tcp_listener().await?;"
    idx = s.find(marker)
    if idx >= 0:
        print(s[max(0, idx - 200):idx + 1000])
    raise SystemExit("Could not locate stock finish_tunnel block")

p.write_text(s.replace(old, new, 1))

required = [
    "[SS-DIAG] create_tcp_listener returned",
    "[SS-DIAG] dynamic TCP connect attempt",
    "[SS-DIAG] dynamic TCP CONNECTED",
    "[SS-DIAG] TLS-PSK/CDTunnel",
]

patched = p.read_text()
missing = [marker for marker in required if marker not in patched]
if missing:
    raise SystemExit(f"Patch verification failed; missing: {missing}")

print("Diagnostic patch applied and verified")
