#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys

MARKER = "[SS-V15-TLS] TLS_PSK_START"


def die(msg: str) -> None:
    raise SystemExit(msg)


def patch_tunnel(path: Path) -> None:
    text = path.read_text()
    if MARKER in text:
        verify_tunnel(text)
        print("v15 tunnel patch already present and verified")
        return

    start = text.find("pub async fn connect_tls_psk_tunnel_native")
    if start < 0:
        die("connect_tls_psk_tunnel_native not found")
    end = text.find("/// Wraps a `tokio::net::TcpStream` with TLS-PSK using OpenSSL", start)
    if end < 0:
        die("OpenSSL tunnel function marker not found")

    replacement = '''pub async fn connect_tls_psk_tunnel_native<S: ReadWrite>(
    stream: S,
    encryption_key: &[u8],
) -> Result<CdTunnel<super::tls_psk::TlsPskStream<S>>, IdeviceError> {
    tracing::error!(
        "[SS-V15-TLS] TLS_PSK_START psk_len={} implementation=pure-rust-tls12",
        encryption_key.len()
    );

    let tls_stream = match super::tls_psk::tls_psk_handshake(stream, encryption_key).await {
        Ok(stream) => {
            tracing::error!("[SS-V15-TLS] TLS_PSK_PASS");
            stream
        }
        Err(error) => {
            tracing::error!("[SS-V15-TLS] TLS_PSK_FAIL error={error}");
            return Err(error);
        }
    };

    // Important: treat the TLS stream as a byte stream and use the canonical
    // CDTunnel read_exact parser.  The older native path called read_app_data()
    // once and assumed the complete CDTunnel response arrived in one TLS record.
    // iOS is free to split the response across records, which made a valid
    // connection look like a magic/length failure.
    tracing::error!(
        "[SS-V15-TLS] CDTUNNEL_HANDSHAKE_START parser=canonical-stream-read-exact"
    );
    let tunnel = match CdTunnel::handshake(tls_stream).await {
        Ok(tunnel) => tunnel,
        Err(error) => {
            tracing::error!("[SS-V15-TLS] CDTUNNEL_HANDSHAKE_FAIL error={error}");
            return Err(error);
        }
    };

    tracing::error!(
        "[SS-V15-TLS] CDTUNNEL_HANDSHAKE_PASS client={} server={} mtu={} rsd_port={}",
        tunnel.info.client_address,
        tunnel.info.server_address,
        tunnel.info.mtu,
        tunnel.info.server_rsd_port
    );
    Ok(tunnel)
}

'''
    text = text[:start] + replacement + text[end:]
    verify_tunnel(text)
    path.write_text(text)
    print("v15 TLS/CDTunnel canonical stream parser applied")


def verify_tunnel(text: str) -> None:
    required = [
        "[SS-V15-TLS] TLS_PSK_START",
        "[SS-V15-TLS] TLS_PSK_PASS",
        "[SS-V15-TLS] TLS_PSK_FAIL",
        "[SS-V15-TLS] CDTUNNEL_HANDSHAKE_START",
        "[SS-V15-TLS] CDTUNNEL_HANDSHAKE_PASS",
        "[SS-V15-TLS] CDTUNNEL_HANDSHAKE_FAIL",
        "CdTunnel::handshake(tls_stream).await",
        "canonical-stream-read-exact",
    ]
    missing = [x for x in required if x not in text]
    if missing:
        die(f"v15 tunnel verification failed: {missing}")

    # The legacy native implementation was record-oriented and therefore
    # incorrectly assumed the response was contained in one TLS application record.
    fn = text[text.find("pub async fn connect_tls_psk_tunnel_native"):text.find("/// Wraps a `tokio::net::TcpStream` with TLS-PSK using OpenSSL")]
    forbidden = [
        "read_app_data().await",
        "response_data[..CDTUNNEL_MAGIC.len()]",
        "response_body = &response_data",
    ]
    leaked = [x for x in forbidden if x in fn]
    if leaked:
        die(f"v15 record-oriented parser still active: {leaked}")


def patch_tls(path: Path) -> None:
    text = path.read_text()
    if "[SS-V15-TLS] CIPHER_SELECTED" not in text:
        needle = '    debug!("Using cipher suite: {suite:?}");\n'
        if text.count(needle) != 1:
            die("cipher suite diagnostic anchor not unique")
        text = text.replace(
            needle,
            needle + '    tracing::error!("[SS-V15-TLS] CIPHER_SELECTED suite={suite:?}");\n',
            1,
        )

    if "[SS-V15-TLS] SERVER_FINISHED_PASS" not in text:
        needle = '                        debug!("Server Finished verified!");\n'
        if text.count(needle) != 1:
            die("server Finished diagnostic anchor not unique")
        text = text.replace(
            needle,
            needle + '                        tracing::error!("[SS-V15-TLS] SERVER_FINISHED_PASS");\n',
            1,
        )

    if "[SS-V15-TLS] SERVER_FINISHED_MISMATCH" not in text:
        needle = '                        debug!("Server Finished verify_data mismatch (continuing anyway)");\n'
        if text.count(needle) != 1:
            die("server Finished mismatch anchor not unique")
        text = text.replace(
            needle,
            needle + '                        tracing::error!("[SS-V15-TLS] SERVER_FINISHED_MISMATCH");\n',
            1,
        )

    for marker in (
        "[SS-V15-TLS] CIPHER_SELECTED",
        "[SS-V15-TLS] SERVER_FINISHED_PASS",
        "[SS-V15-TLS] SERVER_FINISHED_MISMATCH",
    ):
        if marker not in text:
            die(f"missing TLS marker {marker}")

    path.write_text(text)
    print("v15 TLS handshake stage diagnostics applied")


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: patch_v15_tls_cdtunnel.py <remote_pairing/tunnel.rs> <remote_pairing/tls_psk.rs>")
    tunnel = Path(sys.argv[1])
    tls = Path(sys.argv[2])
    for p in (tunnel, tls):
        if not p.exists():
            die(f"missing file: {p}")
    patch_tunnel(tunnel)
    patch_tls(tls)


if __name__ == "__main__":
    main()
