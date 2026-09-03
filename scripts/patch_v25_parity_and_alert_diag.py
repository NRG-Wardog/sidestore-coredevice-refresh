#!/usr/bin/env python3
"""
SideStore V25: Parity & Alert Diagnostic Patch
- Enforces exact CDTunnel 58-byte packet parity with known-working pymobiledevice3:
  magic: b"CDTunnel" (8 bytes) + len: 48 (2 bytes 0x00 0x30) + b'{"type": "clientHandshakeRequest", "mtu": 16000}'
- Adds peerConnectionsInfo with CoreDeviceService to createListener in rpc.
- Enforces strict Server Finished cryptographic verify_data check (mismatch is fatal).
- Implements comprehensive TLS Alert decoding: decrypts Alert record, extracts level & description,
  maps to human-readable names, and logs [SS-V25-ALERT] TLS_ALERT stage=after_cdtunnel_request.
- Implements fragmented TLS application data accumulation with plaintext carryover (prepend_read_data).
- Preserves V24 dynamic listener candidate probing and V13/V23 serialization and Lockdown framing.
"""

from pathlib import Path
import sys

def die(msg: str) -> None:
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)

def once(content: str, old: str, new: str, desc: str) -> str:
    count = content.count(old)
    if count == 0:
        die(f"Pattern for {desc} not found")
    if count > 1:
        die(f"Pattern for {desc} found {count} times (expected 1)")
    return content.replace(old, new, 1)

def patch_tls_psk(root: Path) -> None:
    path = root / "idevice" / "src" / "remote_pairing" / "tls_psk.rs"
    if not path.exists():
        die(f"tls_psk.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if "[SS-V25-TLS] SERVER_FINISHED_PASS" in text:
        print("v25 tls_psk.rs already patched")
        return

    # 1. Strict Server Finished verification
    old_sf = '''                    if plaintext[4..] == server_vd {
                        debug!("Server Finished verified!");
                    } else {
                        debug!("Server Finished verify_data mismatch (continuing anyway)");
                    }'''
    new_sf = '''                    if plaintext[4..] == server_vd {
                        tracing::error!("[SS-V25-TLS] SERVER_FINISHED_PASS");
                    } else {
                        tracing::error!("[SS-V25-TLS] SERVER_FINISHED_FATAL verify_data mismatch");
                        return Err(IdeviceError::InternalError(
                            "TLS server Finished verify_data mismatch".into(),
                        ));
                    }'''
    text = once(text, old_sf, new_sf, "strict server finished verification")

    # 2. Add prepend_read_data to TlsPskStream
    impl_marker = "impl<S: AsyncRead + AsyncWrite + Unpin + Send> TlsPskStream<S> {\n"
    new_method = '''    /// Preserve plaintext already decrypted from a TLS record so the normal
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
    if "pub(crate) fn prepend_read_data" not in text:
        text = once(text, impl_marker, impl_marker + new_method, "prepend_read_data method")

    # 3. Explicit TLS Alert decoding in read_app_data
    old_read_app = '''    pub async fn read_app_data(&mut self) -> Result<Vec<u8>, IdeviceError> {
        let (ct, payload) = read_record(&mut self.inner).await?;
        if ct != CT_APPLICATION_DATA {
            return Err(IdeviceError::InternalError(format!(
                "Expected application data, got ct={ct}"
            )));
        }
        let plaintext = decrypt_record(
            &self.keys,
            true,
            self.read_seq,
            CT_APPLICATION_DATA,
            &payload,
        )?;
        self.read_seq += 1;
        Ok(plaintext)
    }'''

    new_read_app = '''    pub async fn read_app_data(&mut self) -> Result<Vec<u8>, IdeviceError> {
        loop {
            let seq = self.read_seq;
            let (ct, payload) = read_record(&mut self.inner).await?;
            tracing::error!("[SS-V25-TLS] RX_RECORD seq={} ct=0x{:02x} encrypted_len={}", seq, ct, payload.len());

            match ct {
                CT_APPLICATION_DATA => {
                    let plaintext = decrypt_record(&self.keys, true, seq, CT_APPLICATION_DATA, &payload)?;
                    self.read_seq += 1;
                    tracing::error!("[SS-V25-TLS] APP_DECRYPT_PASS seq={} plaintext_len={}", seq, plaintext.len());
                    return Ok(plaintext);
                }
                CT_ALERT => {
                    let plaintext = decrypt_record(&self.keys, true, seq, CT_ALERT, &payload)?;
                    self.read_seq += 1;
                    let level = plaintext.first().copied().unwrap_or(0);
                    let description = plaintext.get(1).copied().unwrap_or(0);
                    let level_name = match level {
                        1 => "warning",
                        2 => "fatal",
                        _ => "unknown_level",
                    };
                    let desc_name = match description {
                        0 => "close_notify",
                        10 => "unexpected_message",
                        20 => "bad_record_mac",
                        21 => "decryption_failed_RESERVED",
                        22 => "record_overflow",
                        30 => "decompression_failure",
                        40 => "handshake_failure",
                        42 => "bad_certificate",
                        43 => "unsupported_certificate",
                        44 => "certificate_revoked",
                        45 => "certificate_expired",
                        46 => "certificate_unknown",
                        47 => "illegal_parameter",
                        48 => "unknown_ca",
                        49 => "access_denied",
                        50 => "decode_error",
                        51 => "decrypt_error",
                        70 => "protocol_version",
                        71 => "insufficient_security",
                        80 => "internal_error",
                        90 => "user_canceled",
                        100 => "no_renegotiation",
                        110 => "unsupported_extension",
                        _ => "other_alert",
                    };
                    tracing::error!(
                        "[SS-V25-ALERT] TLS_ALERT stage=after_cdtunnel_request level={} level_name={} description={} description_name={} plaintext_len={}",
                        level, level_name, description, desc_name, plaintext.len()
                    );
                    return Err(IdeviceError::InternalError(format!(
                        "TLS Alert: stage=after_cdtunnel_request level={level} ({level_name}) description={description} ({desc_name})"
                    )));
                }
                CT_HANDSHAKE => {
                    let plaintext = decrypt_record(&self.keys, true, seq, CT_HANDSHAKE, &payload)?;
                    self.read_seq += 1;
                    let hs_type = plaintext.first().copied().unwrap_or(0);
                    tracing::error!("[SS-V25-TLS] POST_HANDSHAKE_RECORD seq={} hs_type=0x{:02x} plaintext_len={} action=continue", seq, hs_type, plaintext.len());
                    continue;
                }
                _ => {
                    return Err(IdeviceError::InternalError(format!(
                        "Expected application data or alert, got unexpected ct={ct}"
                    )));
                }
            }
        }
    }'''
    text = once(text, old_read_app, new_read_app, "read_app_data with alert decoder")

    path.write_text(text, encoding="utf-8")
    print("v25 tls_psk.rs patch applied")

def patch_tunnel(root: Path) -> None:
    path = root / "idevice" / "src" / "remote_pairing" / "tunnel.rs"
    if not path.exists():
        die(f"tunnel.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if "[SS-V25-CDT] HANDSHAKE_START" in text:
        print("v25 tunnel.rs already patched")
        return

    start_fn = "pub async fn connect_tls_psk_tunnel_native<S: ReadWrite>("
    start = text.find(start_fn)
    if start < 0:
        die("connect_tls_psk_tunnel_native start not found")

    end_marker = "/// Wraps a `tokio::net::TcpStream` with TLS-PSK using OpenSSL"
    end = text.find(end_marker, start)
    if end < 0:
        die("OpenSSL marker not found after connect_tls_psk_tunnel_native")

    replacement = r'''pub async fn connect_tls_psk_tunnel_native<S: ReadWrite>(
    stream: S,
    encryption_key: &[u8],
) -> Result<CdTunnel<super::tls_psk::TlsPskStream<S>>, IdeviceError> {
    tracing::error!(
        "[SS-V25-TLS] TLS_PSK_START psk_len={} implementation=pure-rust-tls12",
        encryption_key.len()
    );

    let mut tls_stream = match super::tls_psk::tls_psk_handshake(stream, encryption_key).await {
        Ok(s) => {
            tracing::error!("[SS-V25-TLS] TLS_PSK_PASS");
            s
        }
        Err(e) => {
            tracing::error!("[SS-V25-TLS] TLS_PSK_FAIL error={e}");
            return Err(e);
        }
    };

    // Exact byte parity with pymobiledevice3:
    // b'CDTunnel\x000{"type": "clientHandshakeRequest", "mtu": 16000}'
    // 8 bytes magic + 2 bytes big-endian length 48 (0x00 0x30) + 48 bytes JSON body = 58 bytes total
    let body: &[u8] = br#"{"type": "clientHandshakeRequest", "mtu": 16000}"#;
    let mut packet = Vec::with_capacity(CDTUNNEL_MAGIC.len() + 2 + body.len());
    packet.extend_from_slice(CDTUNNEL_MAGIC);
    packet.extend_from_slice(&(body.len() as u16).to_be_bytes());
    packet.extend_from_slice(body);

    tracing::error!(
        "[SS-V25-CDT] HANDSHAKE_START tx_mode=single_tls_record body_len={} frame_len={}",
        body.len(),
        packet.len()
    );
    tls_stream.write_app_data(&packet).await.map_err(|error| {
        tracing::error!("[SS-V25-CDT] REQUEST_WRITE_FAIL error={error}");
        error
    })?;
    tracing::error!("[SS-V25-CDT] REQUEST_WRITE_PASS");

    let mut response_data = Vec::<u8>::new();
    let expected_total = loop {
        let chunk = tls_stream.read_app_data().await.map_err(|error| {
            tracing::error!(
                "[SS-V25-CDT] RESPONSE_TLS_READ_FAIL accumulated={} error={error}",
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
            "[SS-V25-CDT] RESPONSE_TLS_RECORD bytes={} accumulated_before={}",
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
                "[SS-V25-CDT] RESPONSE_MAGIC_FAIL first_bytes={:02x?}",
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
            "[SS-V25-CDT] RESPONSE_HEADER_PASS body_len={} expected_total={} accumulated={}",
            body_len,
            total,
            response_data.len()
        );
        if response_data.len() >= total {
            break total;
        }
    };

    let body_start = CDTUNNEL_MAGIC.len() + 2;
    let response_body = &response_data[body_start..expected_total];
    if response_data.len() > expected_total {
        let extra = &response_data[expected_total..];
        tracing::error!(
            "[SS-V25-CDT] RESPONSE_TRAILING_DATA bytes={} carried_over=true",
            extra.len()
        );
        tls_stream.prepend_read_data(extra);
    }

    let response: serde_json::Value = serde_json::from_slice(response_body).map_err(|e| {
        tracing::error!("[SS-V25-CDT] RESPONSE_JSON_PARSE_FAIL error={e}");
        IdeviceError::InternalError(format!("CDTunnel JSON parse: {e}"))
    })?;

    let resp_type = response.get("type").and_then(|v| v.as_str()).unwrap_or("");
    if resp_type != "serverHandshakeResponse" {
        tracing::error!("[SS-V25-CDT] UNEXPECTED_RESPONSE_TYPE type={resp_type}");
        return Err(IdeviceError::UnexpectedResponse(format!(
            "unexpected CDTunnel response type: {resp_type}"
        )));
    }

    let client_params = response
        .get("clientParameters")
        .ok_or_else(|| IdeviceError::UnexpectedResponse("missing clientParameters".into()))?;

    let client_address = client_params
        .get("address")
        .and_then(|a| a.as_str())
        .ok_or_else(|| IdeviceError::UnexpectedResponse("missing client address".into()))?
        .to_string();

    let mtu = client_params
        .get("mtu")
        .and_then(|m| m.as_u64())
        .unwrap_or(1500) as u16;

    let server_address = response
        .get("serverAddress")
        .and_then(|a| a.as_str())
        .ok_or_else(|| IdeviceError::UnexpectedResponse("missing serverAddress".into()))?
        .to_string();

    let server_rsd_port = response
        .get("serverRSDPort")
        .and_then(|p| p.as_u64())
        .ok_or_else(|| IdeviceError::UnexpectedResponse("missing serverRSDPort".into()))?
        as u16;

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
        "[SS-V25-CDT] HANDSHAKE_PASS client={} server={} mtu={} rsd_port={}",
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
    path.write_text(text, encoding="utf-8")
    print("v25 tunnel.rs patch applied")

def patch_mod_rpc(root: Path) -> None:
    path = root / "idevice" / "src" / "remote_pairing" / "mod.rs"
    if not path.exists():
        die(f"remote_pairing/mod.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if "[SS-V25-RP] CREATE_LISTENER_PEER_INFO_SENT" in text:
        print("v25 remote_pairing/mod.rs already patched")
        return

    old_req = '''        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                        "transportProtocolType": "tcp"
                    }
                }
            }
        });'''

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
        tracing::error!("[SS-V25-RP] CREATE_LISTENER_PEER_INFO_SENT owningProcess=CoreDeviceService");'''

    text = once(text, old_req, new_req, "createListener peerConnectionsInfo")
    path.write_text(text, encoding="utf-8")
    print("v25 remote_pairing/mod.rs patch applied")

def main():
    if len(sys.argv) < 2:
        target = Path(".")
    else:
        target = Path(sys.argv[1])

    patch_tls_psk(target)
    patch_tunnel(target)
    patch_mod_rpc(target)
    print("All V25 parity & alert diagnostic patches applied successfully!")

if __name__ == "__main__":
    main()
