#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_tls_phase_diag.py <remote_pairing/tunnel.rs>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-TLS-PHASE] phase diagnostic active"

if marker in s:
    required = [
        "TLS_HANDSHAKE_START",
        "TLS_HANDSHAKE_SUCCESS",
        "TLS_HANDSHAKE_FAILED",
        "CDTUNNEL_REQUEST_SENT",
        "CDTUNNEL_RESPONSE_RECEIVED",
        "CDTUNNEL_SUCCESS",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"TLS phase marker present but patch incomplete: {missing}")
    print("TLS/CDTunnel phase diagnostics already present and verified")
    raise SystemExit(0)

old_start = '''pub async fn connect_tls_psk_tunnel_native<S: ReadWrite>(
    stream: S,
    encryption_key: &[u8],
) -> Result<CdTunnel<super::tls_psk::TlsPskStream<S>>, IdeviceError> {
    let mut tls_stream = super::tls_psk::tls_psk_handshake(stream, encryption_key).await?;
    debug!("Native TLS-PSK handshake complete");
'''

new_start = '''pub async fn connect_tls_psk_tunnel_native<S: ReadWrite>(
    stream: S,
    encryption_key: &[u8],
) -> Result<CdTunnel<super::tls_psk::TlsPskStream<S>>, IdeviceError> {
    // Diagnostics intentionally log phase boundaries only. Never log the PSK.
    tracing::error!("[SS-TLS-PHASE] phase diagnostic active");
    tracing::error!("[SS-TLS-PHASE] TLS_HANDSHAKE_START");
    let mut tls_stream = match super::tls_psk::tls_psk_handshake(stream, encryption_key).await {
        Ok(stream) => {
            tracing::error!("[SS-TLS-PHASE] TLS_HANDSHAKE_SUCCESS");
            stream
        }
        Err(error) => {
            tracing::error!("[SS-TLS-PHASE] TLS_HANDSHAKE_FAILED error={:?}", error);
            return Err(error);
        }
    };
    debug!("Native TLS-PSK handshake complete");
'''

if old_start not in s:
    raise SystemExit("Could not locate native TLS-PSK function prologue")
s = s.replace(old_start, new_start, 1)

old_write = '''    tls_stream.write_app_data(&pkt).await?;

    debug!("Sent CDTunnel handshake request via TLS");

    let response_data = tls_stream.read_app_data().await?;
'''
new_write = '''    if let Err(error) = tls_stream.write_app_data(&pkt).await {
        tracing::error!("[SS-TLS-PHASE] CDTUNNEL_REQUEST_FAILED error={:?}", error);
        return Err(error);
    }
    tracing::error!("[SS-TLS-PHASE] CDTUNNEL_REQUEST_SENT");

    debug!("Sent CDTunnel handshake request via TLS");

    let response_data = match tls_stream.read_app_data().await {
        Ok(data) => {
            tracing::error!("[SS-TLS-PHASE] CDTUNNEL_RESPONSE_RECEIVED bytes={}", data.len());
            data
        }
        Err(error) => {
            tracing::error!("[SS-TLS-PHASE] CDTUNNEL_RESPONSE_FAILED error={:?}", error);
            return Err(error);
        }
    };
'''
if old_write not in s:
    raise SystemExit("Could not locate native CDTunnel request/response block")
s = s.replace(old_write, new_write, 1)

old_success = '''    debug!("CDTunnel established: {info:?}");

    Ok(CdTunnel {
'''
new_success = '''    debug!("CDTunnel established: {info:?}");
    tracing::error!(
        "[SS-TLS-PHASE] CDTUNNEL_SUCCESS mtu={} rsd_port_present={}",
        info.mtu,
        info.server_rsd_port != 0
    );

    Ok(CdTunnel {
'''
if old_success not in s:
    raise SystemExit("Could not locate native CDTunnel success block")
s = s.replace(old_success, new_success, 1)

p.write_text(s)
patched = p.read_text()
required = [
    marker,
    "TLS_HANDSHAKE_START",
    "TLS_HANDSHAKE_SUCCESS",
    "TLS_HANDSHAKE_FAILED",
    "CDTUNNEL_REQUEST_FAILED",
    "CDTUNNEL_REQUEST_SENT",
    "CDTUNNEL_RESPONSE_RECEIVED",
    "CDTUNNEL_RESPONSE_FAILED",
    "CDTUNNEL_SUCCESS",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"TLS phase verification failed: {missing}")

if "encryption_key={" in patched or "PSK=" in patched:
    raise SystemExit("Secret-safety verification failed: PSK/key logging pattern detected")

print("TLS/CDTunnel phase diagnostics applied and verified")
