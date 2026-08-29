#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_openssl_transport.py <tunnel_provider.rs>")

p = Path(sys.argv[1])
s = p.read_text()

marker = "[SS-OPENSSL] standards TLS-PSK transport active"
if marker in s:
    required = [
        "connect_tls_psk_tunnel(tunnel_stream, rpc.encryption_key())",
        "[SS-OPENSSL] TLS+CDTunnel START",
        "[SS-OPENSSL] TLS+CDTunnel SUCCESS",
        "[SS-OPENSSL] TLS+CDTunnel FAILED",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"OpenSSL transport marker present but patch incomplete; missing: {missing}")
    print("OpenSSL adaptive transport patch already present and verified")
    raise SystemExit(0)

if "[SS-ADAPT] adaptive transport engine active" not in s:
    raise SystemExit("Adaptive transport patch must be applied before OpenSSL transport patch")

old_import = "    use idevice::remote_pairing::connect_tls_psk_tunnel_native;"
new_import = '''    use idevice::remote_pairing::connect_tls_psk_tunnel;
    tracing::error!("[SS-OPENSSL] standards TLS-PSK transport active");'''
if old_import not in s:
    raise SystemExit("Could not locate native TLS-PSK import in adaptive finish_tunnel")
s = s.replace(old_import, new_import, 1)

old_start = '''        tracing::error!(
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
        };'''

new_start = '''        tracing::error!(
            "[SS-OPENSSL] TLS+CDTunnel START label={} target={} port={} psk_len={}",
            label,
            target,
            tunnel_port,
            rpc.encryption_key().len()
        );
        // The pure-Rust TLS implementation is useful for diagnostics, but on this
        // iOS build it completes TLS and then receives a TLS Alert for the first
        // CDTunnel application record. Use OpenSSL's mature TLS 1.2 PSK record
        // implementation here, matching pymobiledevice3's working TCP-tunnel path.
        // connect_tls_psk_tunnel() includes both the TLS-PSK handshake and the
        // CDTunnel clientHandshakeRequest, so success means we are ready for RSD.
        let tunnel = match tokio::time::timeout(
            std::time::Duration::from_secs(7),
            connect_tls_psk_tunnel(tunnel_stream, rpc.encryption_key()),
        )
        .await
        {
            Ok(Ok(tunnel)) => {
                tracing::error!(
                    "[SS-OPENSSL] TLS+CDTunnel SUCCESS label={} target={}",
                    label,
                    target
                );
                tunnel
            }
            Ok(Err(e)) => {
                tracing::error!(
                    "[SS-OPENSSL] TLS+CDTunnel FAILED label={} target={} error={:?}",
                    label,
                    target,
                    e
                );
                outcomes.push(format!("{label}=OPENSSL_TLS_CDTUNNEL_FAILED({e:?})@{target}"));
                continue;
            }
            Err(_) => {
                tracing::error!(
                    "[SS-OPENSSL] TLS+CDTunnel TIMEOUT label={} target={} timeout=7s",
                    label,
                    target
                );
                outcomes.push(format!("{label}=OPENSSL_TLS_CDTUNNEL_TIMEOUT@{target}"));
                continue;
            }
        };'''

if old_start not in s:
    raise SystemExit("Could not locate adaptive native TLS block")
s = s.replace(old_start, new_start, 1)

p.write_text(s)

patched = p.read_text()
required = [
    marker,
    "use idevice::remote_pairing::connect_tls_psk_tunnel;",
    "connect_tls_psk_tunnel(tunnel_stream, rpc.encryption_key())",
    "[SS-OPENSSL] TLS+CDTunnel START",
    "[SS-OPENSSL] TLS+CDTunnel SUCCESS",
    "[SS-OPENSSL] TLS+CDTunnel FAILED",
    "[SS-ADAPT] RSD handshake SUCCESS",
    "[SS-ADAPT] TRANSPORT SUCCESS",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"OpenSSL transport verification failed; missing: {missing}")

if "connect_tls_psk_tunnel_native(tunnel_stream, rpc.encryption_key())" in patched:
    raise SystemExit("OpenSSL transport verification failed: active native TLS call remains")

print("OpenSSL adaptive TLS-PSK/CDTunnel transport applied and verified")
