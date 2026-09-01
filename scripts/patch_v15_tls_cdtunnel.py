#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys

MARKER = "[SS-V17-CDT] HANDSHAKE_START"


def die(msg: str) -> None:
    raise SystemExit(msg)


def patch_tunnel(path: Path) -> None:
    text = path.read_text()
    start = text.find("pub async fn connect_tls_psk_tunnel_native")
    if start < 0:
        die("connect_tls_psk_tunnel_native not found")
    end = text.find("/// Wraps a `tokio::net::TcpStream` with TLS-PSK using OpenSSL", start)
    if end < 0:
        die("OpenSSL tunnel function marker not found")

    replacement = r'''pub async fn connect_tls_psk_tunnel_native<S: ReadWrite>(
    stream: S,
    encryption_key: &[u8],
) -> Result<CdTunnel<super::tls_psk::TlsPskStream<S>>, IdeviceError> {
    tracing::error!(
        "[SS-V15-TLS] TLS_PSK_START psk_len={} implementation=pure-rust-tls12",
        encryption_key.len()
    );

    let mut tls_stream = match super::tls_psk::tls_psk_handshake(stream, encryption_key).await {
        Ok(stream) => {
            tracing::error!("[SS-V15-TLS] TLS_PSK_PASS");
            stream
        }
        Err(error) => {
            tracing::error!("[SS-V15-TLS] TLS_PSK_FAIL error={error}");
            return Err(error);
        }
    };

    // Apple/remotepairingd's TCP CDTunnel parser is read-boundary sensitive.
    // Send the *entire* CDTunnel frame in one TLS application-data record, while
    // reading the response as a byte stream accumulated across any number of TLS
    // records.  This combines the known-working pymobiledevice3 write semantics
    // with a fragmentation-safe receive path.
    let body: &[u8] = br#"{"type":"clientHandshakeRequest","mtu":16000}"#;
    let mut packet = Vec::with_capacity(CDTUNNEL_MAGIC.len() + 2 + body.len());
    packet.extend_from_slice(CDTUNNEL_MAGIC);
    packet.extend_from_slice(&(body.len() as u16).to_be_bytes());
    packet.extend_from_slice(body);

    tracing::error!(
        "[SS-V17-CDT] HANDSHAKE_START tx_mode=single_tls_record body_len={} frame_len={}",
        body.len(),
        packet.len()
    );
    tls_stream.write_app_data(&packet).await.map_err(|error| {
        tracing::error!("[SS-V17-CDT] REQUEST_WRITE_FAIL error={error}");
        error
    })?;
    tracing::error!("[SS-V17-CDT] REQUEST_WRITE_PASS");

    let mut response_data = Vec::<u8>::new();
    let expected_total = loop {
        let chunk = tls_stream.read_app_data().await.map_err(|error| {
            tracing::error!(
                "[SS-V17-CDT] RESPONSE_TLS_READ_FAIL accumulated={} error={error}",
                response_data.len()
            );
            error
        })?;
        if chunk.is_empty() {
            return Err(IdeviceError::UnexpectedResponse(
                "empty TLS application-data record during CDTunnel handshake".into(),
            ));
        }
        tracing::error!(
            "[SS-V17-CDT] RESPONSE_TLS_RECORD bytes={} accumulated_before={}",
            chunk.len(),
            response_data.len()
        );
        response_data.extend_from_slice(&chunk);

        if response_data.len() < CDTUNNEL_MAGIC.len() + 2 {
            continue;
        }
        if &response_data[..CDTUNNEL_MAGIC.len()] != CDTUNNEL_MAGIC {
            let preview_len = response_data.len().min(10);
            tracing::error!(
                "[SS-V17-CDT] RESPONSE_MAGIC_FAIL first_bytes={:02x?}",
                &response_data[..preview_len]
            );
            return Err(IdeviceError::UnexpectedResponse(
                "invalid CDTunnel magic in handshake response".into(),
            ));
        }

        let body_len = u16::from_be_bytes([
            response_data[CDTUNNEL_MAGIC.len()],
            response_data[CDTUNNEL_MAGIC.len() + 1],
        ]) as usize;
        let total = CDTUNNEL_MAGIC.len() + 2 + body_len;
        if total > 65545 {
            return Err(IdeviceError::UnexpectedResponse(
                "CDTunnel response length exceeds protocol maximum".into(),
            ));
        }
        tracing::error!(
            "[SS-V17-CDT] RESPONSE_HEADER_PASS body_len={} expected_total={} accumulated={}",
            body_len,
            total,
            response_data.len()
        );
        if response_data.len() >= total {
            break total;
        }
    };

    // Preserve any plaintext that shared the final TLS record with the handshake
    // response.  Normally there is none, but dropping it would corrupt the first
    // IPv6 packet on a fast device.
    if response_data.len() > expected_total {
        let extra = response_data[expected_total..].to_vec();
        tracing::error!("[SS-V17-CDT] RESPONSE_CARRY bytes={}", extra.len());
        tls_stream.prepend_read_data(&extra);
    }

    let body_start = CDTUNNEL_MAGIC.len() + 2;
    let response_body = &response_data[body_start..expected_total];
    let response: serde_json::Value = serde_json::from_slice(response_body)?;
    let response_type = response.get("type").and_then(|v| v.as_str()).unwrap_or("");
    if response_type != "serverHandshakeResponse" {
        tracing::error!("[SS-V17-CDT] RESPONSE_TYPE_FAIL type={response_type}");
        return Err(IdeviceError::UnexpectedResponse(format!(
            "unexpected CDTunnel response type: {response_type}"
        )));
    }

    let client_params = response
        .get("clientParameters")
        .ok_or(IdeviceError::UnexpectedResponse(
            "missing clientParameters in CDTunnel handshake response".into(),
        ))?;
    let client_address = client_params
        .get("address")
        .and_then(|a| a.as_str())
        .ok_or(IdeviceError::UnexpectedResponse(
            "missing client address in CDTunnel handshake response".into(),
        ))?
        .to_string();
    let server_address = response
        .get("serverAddress")
        .and_then(|a| a.as_str())
        .ok_or(IdeviceError::UnexpectedResponse(
            "missing serverAddress in CDTunnel handshake response".into(),
        ))?
        .to_string();
    let mtu = client_params
        .get("mtu")
        .and_then(|m| m.as_u64())
        .ok_or(IdeviceError::UnexpectedResponse(
            "missing mtu in CDTunnel handshake response".into(),
        ))? as u16;
    let server_rsd_port = response
        .get("serverRSDPort")
        .and_then(|p| p.as_u64())
        .ok_or(IdeviceError::UnexpectedResponse(
            "missing serverRSDPort in CDTunnel handshake response".into(),
        ))? as u16;

    let client_ip: std::net::IpAddr = client_address.parse().map_err(|e| {
        IdeviceError::UnexpectedResponse(format!("invalid CDTunnel client address: {e}"))
    })?;
    let server_ip: std::net::IpAddr = server_address.parse().map_err(|e| {
        IdeviceError::UnexpectedResponse(format!("invalid CDTunnel server address: {e}"))
    })?;
    if !client_ip.is_ipv6() || !server_ip.is_ipv6() {
        return Err(IdeviceError::UnexpectedResponse(
            "CDTunnel did not return IPv6 tunnel endpoints".into(),
        ));
    }
    if !(1280..=16000).contains(&mtu) {
        return Err(IdeviceError::UnexpectedResponse(format!(
            "CDTunnel returned invalid MTU {mtu}"
        )));
    }
    if server_rsd_port == 0 {
        return Err(IdeviceError::UnexpectedResponse(
            "CDTunnel returned RSD port 0".into(),
        ));
    }

    let info = TunnelInfo {
        client_address,
        netmask: client_params
            .get("netmask")
            .and_then(|n| n.as_str())
            .unwrap_or("")
            .to_string(),
        server_address,
        mtu,
        server_rsd_port,
    };

    tracing::error!(
        "[SS-V17-CDT] HANDSHAKE_PASS client={} server={} mtu={} rsd_port={}",
        info.client_address,
        info.server_address,
        info.mtu,
        info.server_rsd_port
    );
    tracing::error!(
        "[SS-V15-TLS] CDTUNNEL_HANDSHAKE_PASS client={} server={} mtu={} rsd_port={}",
        info.client_address,
        info.server_address,
        info.mtu,
        info.server_rsd_port
    );

    Ok(CdTunnel {
        inner: tls_stream,
        info,
    })
}

'''
    text = text[:start] + replacement + text[end:]
    path.write_text(text)
    verify_tunnel(path.read_text())
    print("v17 CDTunnel single-record TX + fragmented RX patch applied")


def verify_tunnel(text: str) -> None:
    required = [
        "[SS-V17-CDT] HANDSHAKE_START",
        "tx_mode=single_tls_record",
        "tls_stream.write_app_data(&packet).await",
        "[SS-V17-CDT] RESPONSE_TLS_RECORD",
        "[SS-V17-CDT] RESPONSE_HEADER_PASS",
        "prepend_read_data(&extra)",
        'serverHandshakeResponse',
        "[SS-V17-CDT] HANDSHAKE_PASS",
        "[SS-V15-TLS] TLS_PSK_PASS",
    ]
    missing = [x for x in required if x not in text]
    if missing:
        die(f"v17 tunnel verification failed: {missing}")
    fn = text[text.find("pub async fn connect_tls_psk_tunnel_native"):text.find("/// Wraps a `tokio::net::TcpStream` with TLS-PSK using OpenSSL")]
    for forbidden in ["CdTunnel::handshake(tls_stream).await", "stream.write_all(CDTUNNEL_MAGIC).await"]:
        if forbidden in fn:
            die(f"v17 forbidden split/canonical helper path remains: {forbidden}")


def patch_tls(path: Path) -> None:
    text = path.read_text()
    if "[SS-V15-TLS] CIPHER_SELECTED" not in text:
        needle = '    debug!("Using cipher suite: {suite:?}");\n'
        if text.count(needle) != 1:
            die("cipher suite diagnostic anchor not unique")
        text = text.replace(needle, needle + '    tracing::error!("[SS-V15-TLS] CIPHER_SELECTED suite={suite:?}");\n', 1)

    if "[SS-V15-TLS] SERVER_FINISHED_PASS" not in text:
        needle = '                        debug!("Server Finished verified!");\n'
        if text.count(needle) != 1:
            die("server Finished diagnostic anchor not unique")
        text = text.replace(needle, needle + '                        tracing::error!("[SS-V15-TLS] SERVER_FINISHED_PASS");\n', 1)

    if "[SS-V17-TLS] SERVER_FINISHED_FATAL" not in text:
        old = '                        debug!("Server Finished verify_data mismatch (continuing anyway)");\n'
        if text.count(old) != 1:
            die("server Finished mismatch anchor not unique")
        new = '''                        tracing::error!("[SS-V17-TLS] SERVER_FINISHED_FATAL");
                        return Err(IdeviceError::InternalError(
                            "TLS server Finished verify_data mismatch".into(),
                        ));
'''
        text = text.replace(old, new, 1)

    impl_anchor = "impl<S: AsyncRead + AsyncWrite + Unpin + Send> TlsPskStream<S> {\n"
    if "pub(crate) fn prepend_read_data" not in text:
        if text.count(impl_anchor) != 1:
            die(f"TlsPskStream impl anchor count={text.count(impl_anchor)}")
        method = '''    /// Preserve plaintext already decrypted from a TLS record so the normal
    /// AsyncRead path consumes it before reading another record.
    pub(crate) fn prepend_read_data(&mut self, data: &[u8]) {
        if data.is_empty() {
            return;
        }
        let mut merged = Vec::with_capacity(data.len() + self.read_buf.len());
        merged.extend_from_slice(data);
        merged.extend_from_slice(&self.read_buf);
        self.read_buf = merged;
    }

'''
        text = text.replace(impl_anchor, impl_anchor + method, 1)

    for marker in (
        "[SS-V15-TLS] CIPHER_SELECTED",
        "[SS-V15-TLS] SERVER_FINISHED_PASS",
        "[SS-V17-TLS] SERVER_FINISHED_FATAL",
        "pub(crate) fn prepend_read_data",
    ):
        if marker not in text:
            die(f"missing TLS marker/method {marker}")

    path.write_text(text)
    print("v17 TLS integrity + plaintext carry patch applied")


def patch_rsd(path: Path) -> None:
    text = path.read_text()
    if "[SS-V17-RSD] CONNECT_START" not in text:
        insert_at = text.find("fn write_result(")
        if insert_at < 0:
            die("write_result anchor not found in FFI tunnel provider")
        helper = r'''async fn v17_connect_rsd_with_retry(
    adapter: &mut idevice::tcp::handle::AdapterHandle,
    rsd_port: u16,
) -> Result<RsdHandshake, IdeviceError> {
    if rsd_port == 0 {
        return Err(IdeviceError::InternalError("RSD port is zero".into()));
    }
    let mut last_error = String::from("RSD connection not attempted");
    for attempt in 1u64..=5 {
        tracing::error!(
            "[SS-V17-RSD] CONNECT_START attempt={} port={}",
            attempt,
            rsd_port
        );
        let stream = match tokio::time::timeout(
            std::time::Duration::from_secs(4),
            adapter.connect(rsd_port),
        )
        .await
        {
            Ok(Ok(stream)) => {
                tracing::error!("[SS-V17-RSD] TCP_PASS attempt={} port={}", attempt, rsd_port);
                stream
            }
            Ok(Err(error)) => {
                last_error = format!("RSD TCP connect attempt {attempt}: {error}");
                tracing::error!("[SS-V17-RSD] TCP_FAIL attempt={} error={error}", attempt);
                tokio::time::sleep(std::time::Duration::from_millis(50 * attempt)).await;
                continue;
            }
            Err(_) => {
                last_error = format!("RSD TCP connect attempt {attempt}: timeout");
                tracing::error!("[SS-V17-RSD] TCP_TIMEOUT attempt={}", attempt);
                tokio::time::sleep(std::time::Duration::from_millis(50 * attempt)).await;
                continue;
            }
        };

        tracing::error!("[SS-V17-RSD] HANDSHAKE_START attempt={}", attempt);
        match tokio::time::timeout(
            std::time::Duration::from_secs(6),
            RsdHandshake::new(stream),
        )
        .await
        {
            Ok(Ok(handshake)) => {
                tracing::error!(
                    "[SS-V17-RSD] HANDSHAKE_PASS attempt={} services={} protocol={}",
                    attempt,
                    handshake.services.len(),
                    handshake.protocol_version
                );
                return Ok(handshake);
            }
            Ok(Err(error)) => {
                last_error = format!("RSD handshake attempt {attempt}: {error}");
                tracing::error!("[SS-V17-RSD] HANDSHAKE_FAIL attempt={} error={error}", attempt);
            }
            Err(_) => {
                last_error = format!("RSD handshake attempt {attempt}: timeout");
                tracing::error!("[SS-V17-RSD] HANDSHAKE_TIMEOUT attempt={}", attempt);
            }
        }
        tokio::time::sleep(std::time::Duration::from_millis(75 * attempt)).await;
    }
    Err(IdeviceError::InternalError(last_error))
}

'''
        text = text[:insert_at] + helper + text[insert_at:]

    # Replace every stock direct adapter->RSD handshake sequence. This catches
    # finish_tunnel, USB CoreDevice, and v14 matrix copies if present.
    pattern = re.compile(
        r'''(?P<indent>[ \t]*)let rsd_stream = adapter\n'''
        r'''(?P=indent)    \.connect\(rsd_port\)\n'''
        r'''(?P=indent)    \.await\n'''
        r'''(?P=indent)    \.map_err\(\|e\| IdeviceError::InternalError\(format!\("\{e\}"\)\)\)\?;\n'''
        r'''(?P=indent)let handshake = RsdHandshake::new\(rsd_stream\)\.await\?;'''
    )
    def repl(m: re.Match[str]) -> str:
        i = m.group("indent")
        return f"{i}let handshake = v17_connect_rsd_with_retry(&mut adapter, rsd_port).await?;"

    text, count = pattern.subn(repl, text)
    # Some v14 generated paths already wrap errors differently. Replace a more
    # permissive two-statement form as a fallback.
    if count == 0 and "v17_connect_rsd_with_retry(&mut adapter, rsd_port)" not in text:
        generic = re.compile(
            r'''(?P<indent>[ \t]*)let rsd_stream = adapter(?P<body>[\s\S]{0,260}?)?;\n'''
            r'''(?P=indent)let handshake = RsdHandshake::new\(rsd_stream\)\.await\?;'''
        )
        text, count = generic.subn(lambda m: f'{m.group("indent")}let handshake = v17_connect_rsd_with_retry(&mut adapter, rsd_port).await?;', text)

    if "v17_connect_rsd_with_retry(&mut adapter, rsd_port).await?" not in text:
        die("could not replace any adapter->RSD direct handshake path")

    path.write_text(text)
    patched = path.read_text()
    for required in [
        "[SS-V17-RSD] CONNECT_START",
        "[SS-V17-RSD] TCP_PASS",
        "[SS-V17-RSD] HANDSHAKE_PASS",
        "v17_connect_rsd_with_retry(&mut adapter, rsd_port).await?",
    ]:
        if required not in patched:
            die(f"RSD pipeline verification missing {required}")
    print(f"v17 RSD retry/validation pipeline applied; direct sequences replaced={count}")


def main() -> None:
    if len(sys.argv) != 4:
        die("usage: patch_v15_tls_cdtunnel.py <remote_pairing/tunnel.rs> <remote_pairing/tls_psk.rs> <ffi/tunnel_provider.rs>")
    tunnel = Path(sys.argv[1])
    tls = Path(sys.argv[2])
    ffi = Path(sys.argv[3])
    for p in (tunnel, tls, ffi):
        if not p.exists():
            die(f"missing file: {p}")
    patch_tunnel(tunnel)
    patch_tls(tls)
    patch_rsd(ffi)


if __name__ == "__main__":
    main()
