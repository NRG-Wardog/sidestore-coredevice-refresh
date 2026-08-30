#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_cdtunnel_record_parity.py <idevice/src/tunnel.rs>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-CDTUNNEL-PARITY] single-record handshake write active"

if marker in s:
    required = [
        "let mut packet = Vec::with_capacity",
        "packet.extend_from_slice(CDTUNNEL_MAGIC)",
        "stream.write_all(&packet).await?",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"CDTunnel parity marker present but patch incomplete: {missing}")
    print("CDTunnel single-record write already present and verified")
    raise SystemExit(0)

old = '''        let body = serde_json::to_vec(&request)?;

        stream.write_all(CDTUNNEL_MAGIC).await?;
        stream.write_all(&(body.len() as u16).to_be_bytes()).await?;
        stream.write_all(&body).await?;
        stream.flush().await?;

        debug!("Sent CDTunnel handshake request");
'''

new = '''        let body = serde_json::to_vec(&request)?;

        // pymobiledevice3 emits the complete CDTunnel handshake packet through
        // the TLS stream in one write. Keep the magic, length and JSON body in a
        // single application-data write as CoreDevice's TCP tunnel path is
        // sensitive to TLS/read boundaries on some iOS versions.
        let mut packet = Vec::with_capacity(CDTUNNEL_MAGIC.len() + 2 + body.len());
        packet.extend_from_slice(CDTUNNEL_MAGIC);
        packet.extend_from_slice(&(body.len() as u16).to_be_bytes());
        packet.extend_from_slice(&body);
        tracing::error!(
            "[SS-CDTUNNEL-PARITY] single-record handshake write active body_len={} packet_len={}",
            body.len(),
            packet.len()
        );
        stream.write_all(&packet).await?;
        stream.flush().await?;

        debug!("Sent CDTunnel handshake request");
'''

if old not in s:
    raise SystemExit("Could not locate split CDTunnel handshake write block")

s = s.replace(old, new, 1)
p.write_text(s)

patched = p.read_text()
required = [
    marker,
    "let mut packet = Vec::with_capacity",
    "packet.extend_from_slice(CDTUNNEL_MAGIC)",
    "packet.extend_from_slice(&(body.len() as u16).to_be_bytes())",
    "packet.extend_from_slice(&body)",
    "stream.write_all(&packet).await?",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"CDTunnel single-record write verification failed: {missing}")

if "stream.write_all(CDTUNNEL_MAGIC).await?;" in patched:
    raise SystemExit("Split CDTunnel magic write still remains")

print("Patched CDTunnel handshake to a single TLS application-data write")
