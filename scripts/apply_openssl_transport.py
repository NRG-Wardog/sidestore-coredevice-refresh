#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_openssl_transport.py <tunnel_provider.rs>")

p = Path(sys.argv[1])
s = p.read_text()
script_dir = Path(__file__).resolve().parent
repo_root = p.resolve().parents[2]
listener_mod = repo_root / "idevice/src/remote_pairing/mod.rs"
tunnel_rs = repo_root / "idevice/src/remote_pairing/tunnel.rs"
cdtunnel_rs = repo_root / "idevice/src/tunnel.rs"


def apply_companion_patches() -> None:
    if not listener_mod.exists():
        raise SystemExit(f"RemotePairing mod.rs not found at {listener_mod}")
    if not tunnel_rs.exists():
        raise SystemExit(f"RemotePairing tunnel.rs not found at {tunnel_rs}")
    if not cdtunnel_rs.exists():
        raise SystemExit(f"CDTunnel tunnel.rs not found at {cdtunnel_rs}")

    subprocess.run(
        [sys.executable, str(script_dir / "apply_source_bound_dynamic.py"), str(p)],
        check=True,
    )
    subprocess.run(
        [sys.executable, str(script_dir / "apply_listener_parity.py"), str(listener_mod)],
        check=True,
    )
    subprocess.run(
        [sys.executable, str(script_dir / "apply_openssl_stage_diag.py"), str(tunnel_rs)],
        check=True,
    )
    subprocess.run(
        [sys.executable, str(script_dir / "apply_cdtunnel_record_parity.py"), str(cdtunnel_rs)],
        check=True,
    )

    provider_text = p.read_text()
    listener_text = listener_mod.read_text()
    tunnel_text = tunnel_rs.read_text()
    cdtunnel_text = cdtunnel_rs.read_text()
    companion_required = [
        (provider_text, "[SS-SOURCE-BOUND] en0-v4 source-bound connect active"),
        (provider_text, "ss_connect_dynamic_candidate(&label, target)"),
        (listener_text, "[SS-LISTENER] pymobiledevice3 listener parity active"),
        (listener_text, '"peerConnectionsInfo"'),
        (listener_text, '"owningProcessName": "CoreDeviceService"'),
        (tunnel_text, "[SS-OPENSSL-STAGE] TLS_HANDSHAKE_SUCCESS"),
        (tunnel_text, "[SS-OPENSSL-STAGE] CDTUNNEL_SUCCESS"),
        (tunnel_text, "[SS-OPENSSL-STAGE] CDTUNNEL_FAILED"),
        (cdtunnel_text, "[SS-CDTUNNEL-PARITY] single-record handshake write active"),
        (cdtunnel_text, "stream.write_all(&packet).await?"),
    ]
    missing = [marker for text, marker in companion_required if marker not in text]
    if missing:
        raise SystemExit(f"OpenSSL companion patch verification failed; missing: {missing}")


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
    apply_companion_patches()
    print("OpenSSL adaptive transport and companion protocol patches already present and verified")
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
        // Use OpenSSL's TLS 1.2 PSK implementation, matching pymobiledevice3.
        // Companion patches applied by this builder also match pymobiledevice3's
        // createListener metadata and CDTunnel TLS record boundaries. The en0-v4
        // candidate is additionally source-bound to the utun address so CoreDevice
        // sees a distinct peer rather than an on-device physical self-connect.
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
apply_companion_patches()

patched = p.read_text()
required = [
    marker,
    "use idevice::remote_pairing::connect_tls_psk_tunnel;",
    "connect_tls_psk_tunnel(tunnel_stream, rpc.encryption_key())",
    "[SS-SOURCE-BOUND] en0-v4 source-bound connect active",
    "ss_connect_dynamic_candidate(&label, target)",
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

print("OpenSSL adaptive TLS-PSK + source-bound en0 + listener parity + CDTunnel record parity + stage diagnostics applied and verified")
