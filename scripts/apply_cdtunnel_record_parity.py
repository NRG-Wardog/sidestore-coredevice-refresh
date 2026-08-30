#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_cdtunnel_record_parity.py <idevice/src/tunnel.rs>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-CDTUNNEL-PARITY] exact client handshake bytes active"
exact_literal = 'br#"{"type":"clientHandshakeRequest","mtu":16000}"#'

if marker in s:
    required = [
        exact_literal,
        "let mut packet = Vec::with_capacity",
        "packet.extend_from_slice(CDTUNNEL_MAGIC)",
        "stream.write_all(&packet).await?",
        "[SS-CDTUNNEL-PARITY] single-record handshake write active",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"CDTunnel exact-byte marker present but patch incomplete: {missing}")
    print("CDTunnel exact-byte single-record write already present and verified")
    raise SystemExit(0)

old = '''        let request = serde_json::json!({
            "type": "clientHandshakeRequest",
            "mtu": DEFAULT_MTU
        });
        let body = serde_json::to_vec(&request)?;

        stream.write_all(CDTUNNEL_MAGIC).await?;
        stream.write_all(&(body.len() as u16).to_be_bytes()).await?;
        stream.write_all(&body).await?;
        stream.flush().await?;

        debug!("Sent CDTunnel handshake request");
'''

new = '''        // Match the known-working TCP CDTunnel clients byte-for-byte. Both
        // pymobiledevice3 and RemotePairingKit send the type field before mtu;
        // serde_json without preserve_order can reorder object keys. Avoid any
        // serializer-dependent ordering/spacing here and keep the entire frame in
        // one TLS application-data write.
        // [SS-CDTUNNEL-PARITY] single-record handshake write active
        let body: &[u8] = br#"{"type":"clientHandshakeRequest","mtu":16000}"#;
        let mut packet = Vec::with_capacity(CDTUNNEL_MAGIC.len() + 2 + body.len());
        packet.extend_from_slice(CDTUNNEL_MAGIC);
        packet.extend_from_slice(&(body.len() as u16).to_be_bytes());
        packet.extend_from_slice(body);
        tracing::error!(
            "[SS-CDTUNNEL-PARITY] exact client handshake bytes active body_len={} packet_len={}",
            body.len(),
            packet.len()
        );
        stream.write_all(&packet).await?;
        stream.flush().await?;

        debug!("Sent CDTunnel handshake request");
'''

if old not in s:
    raise SystemExit("Could not locate stock CDTunnel handshake block")

s = s.replace(old, new, 1)
p.write_text(s)

patched = p.read_text()
required = [
    marker,
    "[SS-CDTUNNEL-PARITY] single-record handshake write active",
    exact_literal,
    "let mut packet = Vec::with_capacity",
    "packet.extend_from_slice(CDTUNNEL_MAGIC)",
    "packet.extend_from_slice(&(body.len() as u16).to_be_bytes())",
    "packet.extend_from_slice(body)",
    "stream.write_all(&packet).await?",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"CDTunnel exact-byte single-record verification failed: {missing}")

for forbidden in [
    "serde_json::to_vec(&request)?",
    "stream.write_all(CDTUNNEL_MAGIC).await?;",
    "stream.write_all(&body).await?;",
    r'br#"{\"type\"',
]:
    if forbidden in patched:
        raise SystemExit(f"Old serializer/split-write/escaped CDTunnel path still remains: {forbidden}")

print("Patched CDTunnel handshake to exact known-working bytes in one TLS application-data write")
