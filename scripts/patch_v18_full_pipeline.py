#!/usr/bin/env python3
from pathlib import Path
import sys


def die(msg: str) -> None:
    raise SystemExit(msg)


def patch_tunnel(path: Path) -> None:
    s = path.read_text()
    old = '    let body: &[u8] = br#"{"type":"clientHandshakeRequest","mtu":16000}"#;\n'
    new = '''    // pymobiledevice3 uses Python json.dumps() here. Match that wire image
    // byte-for-byte, including spaces, because CoreDevice's CDTunnel parser has
    // repeatedly proven read/wire-shape sensitive on-device.
    let body: &[u8] = br#"{"type": "clientHandshakeRequest", "mtu": 16000}"#;
    tracing::error!("[SS-V18-CDT] REQUEST_WIRE_PARITY=pymobiledevice3-json-dumps body_len={} frame_len={}", body.len(), CDTUNNEL_MAGIC.len() + 2 + body.len());
'''
    if old not in s:
        die('v18 CDTunnel compact body anchor missing')
    s = s.replace(old, new, 1)
    s = s.replace('[SS-V17-CDT] REQUEST_WRITE_PASS', '[SS-V18-CDT] REQUEST_WRITE_PASS')
    s = s.replace('[SS-V17-CDT] RESPONSE_TLS_READ_FAIL', '[SS-V18-CDT] RESPONSE_TLS_READ_FAIL')
    s = s.replace('[SS-V17-CDT] RESPONSE_TLS_RECORD', '[SS-V18-CDT] RESPONSE_TLS_RECORD')
    s = s.replace('[SS-V17-CDT] RESPONSE_HEADER_PASS', '[SS-V18-CDT] RESPONSE_HEADER_PASS')
    s = s.replace('[SS-V17-CDT] HANDSHAKE_PASS', '[SS-V18-CDT] HANDSHAKE_PASS')
    path.write_text(s)
    for marker in ('REQUEST_WIRE_PARITY=pymobiledevice3-json-dumps', 'body_len={}', '[SS-V18-CDT] REQUEST_WRITE_PASS'):
        if marker not in s:
            die(f'missing tunnel marker {marker}')


def patch_tls(path: Path) -> None:
    s = path.read_text()
    if 'const CT_ALERT: u8 = 0x15;' not in s:
        s = s.replace('const CT_CHANGE_CIPHER_SPEC: u8 = 0x14;\n', 'const CT_CHANGE_CIPHER_SPEC: u8 = 0x14;\nconst CT_ALERT: u8 = 0x15;\n', 1)

    # Harden CBC padding parsing. The upstream subtraction can underflow on a malformed/alert record.
    old = '''    let pad_value = *decrypted.last().unwrap() as usize;
    let content_len = decrypted.len() - (pad_value + 1);
    let mac_len = keys.suite.mac_key_len();
'''
    new = '''    let pad_value = *decrypted.last().unwrap() as usize;
    let padding_len = pad_value + 1;
    if padding_len > decrypted.len() {
        tracing::error!("[SS-V18-TLS] CBC_PADDING_BOUNDS_FAIL seq={} ct={} decrypted_len={} pad_value={}", seq, ct, decrypted.len(), pad_value);
        return Err(IdeviceError::InternalError("TLS CBC padding length invalid".into()));
    }
    if decrypted[decrypted.len() - padding_len..].iter().any(|b| *b as usize != pad_value) {
        tracing::error!("[SS-V18-TLS] CBC_PADDING_BYTES_FAIL seq={} ct={} pad_value={}", seq, ct, pad_value);
        return Err(IdeviceError::InternalError("TLS CBC padding bytes invalid".into()));
    }
    let content_len = decrypted.len() - padding_len;
    let mac_len = keys.suite.mac_key_len();
'''
    if old not in s:
        die('v18 TLS padding anchor missing')
    s = s.replace(old, new, 1)

    old = '''    pub async fn read_app_data(&mut self) -> Result<Vec<u8>, IdeviceError> {
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
    }
'''
    new = '''    pub async fn read_app_data(&mut self) -> Result<Vec<u8>, IdeviceError> {
        loop {
            let seq = self.read_seq;
            let (ct, payload) = match read_record(&mut self.inner).await {
                Ok(v) => v,
                Err(e) => {
                    tracing::error!("[SS-V18-TLS] RX_SOCKET_FAIL seq={} error={e}", seq);
                    return Err(e);
                }
            };
            tracing::error!("[SS-V18-TLS] RX_RECORD seq={} ct=0x{:02x} encrypted_len={}", seq, ct, payload.len());

            match ct {
                CT_APPLICATION_DATA => {
                    let plaintext = decrypt_record(&self.keys, true, seq, CT_APPLICATION_DATA, &payload)
                        .map_err(|e| {
                            tracing::error!("[SS-V18-TLS] APP_DECRYPT_FAIL seq={} encrypted_len={} error={e}", seq, payload.len());
                            e
                        })?;
                    self.read_seq += 1;
                    tracing::error!("[SS-V18-TLS] APP_DECRYPT_PASS seq={} plaintext_len={}", seq, plaintext.len());
                    return Ok(plaintext);
                }
                CT_ALERT => {
                    let plaintext = decrypt_record(&self.keys, true, seq, CT_ALERT, &payload)
                        .map_err(|e| {
                            tracing::error!("[SS-V18-TLS] ALERT_DECRYPT_FAIL seq={} encrypted_len={} error={e}", seq, payload.len());
                            e
                        })?;
                    self.read_seq += 1;
                    let level = plaintext.first().copied().unwrap_or(0);
                    let description = plaintext.get(1).copied().unwrap_or(0);
                    tracing::error!("[SS-V18-TLS] ALERT level={} description={} plaintext_len={}", level, description, plaintext.len());
                    return Err(IdeviceError::InternalError(format!("TLS alert after CDTunnel request: level={level} description={description}")));
                }
                CT_HANDSHAKE => {
                    let plaintext = decrypt_record(&self.keys, true, seq, CT_HANDSHAKE, &payload)?;
                    self.read_seq += 1;
                    let hs_type = plaintext.first().copied().unwrap_or(0);
                    tracing::error!("[SS-V18-TLS] POST_HANDSHAKE_RECORD seq={} hs_type=0x{:02x} plaintext_len={} action=continue", seq, hs_type, plaintext.len());
                    continue;
                }
                CT_CHANGE_CIPHER_SPEC => {
                    tracing::error!("[SS-V18-TLS] UNEXPECTED_CCS seq={} encrypted_len={}", seq, payload.len());
                    continue;
                }
                other => {
                    tracing::error!("[SS-V18-TLS] UNEXPECTED_CONTENT_TYPE seq={} ct=0x{:02x} encrypted_len={}", seq, other, payload.len());
                    return Err(IdeviceError::InternalError(format!("Unexpected TLS content type after handshake: {other}")));
                }
            }
        }
    }
'''
    if old not in s:
        die('v18 read_app_data anchor missing')
    s = s.replace(old, new, 1)
    path.write_text(s)
    for marker in ('[SS-V18-TLS] RX_RECORD', '[SS-V18-TLS] ALERT', '[SS-V18-TLS] APP_DECRYPT_PASS', 'CBC_PADDING_BOUNDS_FAIL'):
        if marker not in s:
            die(f'missing TLS marker {marker}')


def patch_rsd(path: Path) -> None:
    s = path.read_text()
    if '[SS-V18-RSD] VALIDATION_PASS' in s:
        return
    old = '''        Ok(Self {
            services,
            protocol_version,
            properties,
            uuid,
        })
'''
    new = '''        if services.is_empty() {
            tracing::error!("[SS-V18-RSD] VALIDATION_FAIL reason=no_services protocol={} uuid_len={}", protocol_version, uuid.len());
            return Err(IdeviceError::UnexpectedResponse("RSD returned zero services".into()));
        }
        if uuid.is_empty() {
            tracing::error!("[SS-V18-RSD] VALIDATION_FAIL reason=empty_uuid protocol={} services={}", protocol_version, services.len());
            return Err(IdeviceError::UnexpectedResponse("RSD returned empty UUID".into()));
        }
        tracing::error!("[SS-V18-RSD] VALIDATION_PASS protocol={} services={} properties={} uuid_len={}", protocol_version, services.len(), properties.len(), uuid.len());
        Ok(Self {
            services,
            protocol_version,
            properties,
            uuid,
        })
'''
    if old not in s:
        die('v18 RSD result anchor missing')
    s = s.replace(old, new, 1)
    path.write_text(s)


def main() -> None:
    if len(sys.argv) != 4:
        die('usage: patch_v18_full_pipeline.py <remote_pairing/tunnel.rs> <remote_pairing/tls_psk.rs> <services/rsd.rs>')
    paths = [Path(x) for x in sys.argv[1:]]
    for p in paths:
        if not p.exists():
            die(f'missing {p}')
    patch_tunnel(paths[0])
    patch_tls(paths[1])
    patch_rsd(paths[2])
    print('v18 full CDTunnel/TLS/RSD patch applied')


if __name__ == '__main__':
    main()
