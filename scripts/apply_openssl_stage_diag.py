#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_openssl_stage_diag.py <remote_pairing/tunnel.rs>")

p = Path(sys.argv[1])
s = p.read_text()

marker = "[SS-OPENSSL-STAGE] TLS_HANDSHAKE_SUCCESS"
if marker in s:
    required = [
        "[SS-OPENSSL-STAGE] TLS_HANDSHAKE_START",
        "[SS-OPENSSL-STAGE] TLS_HANDSHAKE_SUCCESS",
        "[SS-OPENSSL-STAGE] TLS_HANDSHAKE_FAILED",
        "[SS-OPENSSL-STAGE] CDTUNNEL_START",
        "[SS-OPENSSL-STAGE] CDTUNNEL_SUCCESS",
        "[SS-OPENSSL-STAGE] CDTUNNEL_FAILED",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"OpenSSL stage marker present but patch incomplete: {missing}")
    print("OpenSSL stage diagnostics already present and verified")
    raise SystemExit(0)

old_connect = '''    if let Err(e) = std::pin::Pin::new(&mut tls_stream).connect().await {
        let ssl_errors = openssl::error::ErrorStack::get();
        let msg = format!("TLS-PSK handshake failed: {e} (SSL errors: {ssl_errors:?})");
        tracing::error!("{msg}");
        return Err(IdeviceError::InternalError(msg));
    }

    debug!("TLS-PSK handshake complete");

    CdTunnel::handshake(tls_stream).await
'''

new_connect = '''    tracing::error!("[SS-OPENSSL-STAGE] TLS_HANDSHAKE_START psk_len={}", encryption_key.len());
    if let Err(e) = std::pin::Pin::new(&mut tls_stream).connect().await {
        let ssl_errors = openssl::error::ErrorStack::get();
        let msg = format!("TLS-PSK handshake failed: {e} (SSL errors: {ssl_errors:?})");
        tracing::error!("[SS-OPENSSL-STAGE] TLS_HANDSHAKE_FAILED error={}", msg);
        return Err(IdeviceError::InternalError(msg));
    }

    debug!("TLS-PSK handshake complete");
    tracing::error!("[SS-OPENSSL-STAGE] TLS_HANDSHAKE_SUCCESS");
    tracing::error!("[SS-OPENSSL-STAGE] CDTUNNEL_START mtu=16000");

    match CdTunnel::handshake(tls_stream).await {
        Ok(tunnel) => {
            tracing::error!(
                "[SS-OPENSSL-STAGE] CDTUNNEL_SUCCESS mtu={} rsd_port={} client_addr_present={} server_addr_present={}",
                tunnel.info.mtu,
                tunnel.info.server_rsd_port,
                !tunnel.info.client_address.is_empty(),
                !tunnel.info.server_address.is_empty()
            );
            Ok(tunnel)
        }
        Err(e) => {
            tracing::error!("[SS-OPENSSL-STAGE] CDTUNNEL_FAILED error={:?}", e);
            Err(e)
        }
    }
'''

if old_connect not in s:
    raise SystemExit("Could not locate OpenSSL TLS connect/CDTunnel tail")

s = s.replace(old_connect, new_connect, 1)
p.write_text(s)

patched = p.read_text()
required = [
    "[SS-OPENSSL-STAGE] TLS_HANDSHAKE_START",
    "[SS-OPENSSL-STAGE] TLS_HANDSHAKE_SUCCESS",
    "[SS-OPENSSL-STAGE] TLS_HANDSHAKE_FAILED",
    "[SS-OPENSSL-STAGE] CDTUNNEL_START",
    "[SS-OPENSSL-STAGE] CDTUNNEL_SUCCESS",
    "[SS-OPENSSL-STAGE] CDTUNNEL_FAILED",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"OpenSSL stage diagnostic verification failed: {missing}")

print("Exact OpenSSL TLS/CDTunnel stage diagnostics applied and verified")
